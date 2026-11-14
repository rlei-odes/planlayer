"""Workforce planning (ADR 0020): a cost center's FTE and headcount per staff type x category
and month, typed in the simple modes or rolled up from positions.

Values are levels at month end (``domain.workforce``). A typed year or quarter value fills its
open months. In a positions round, every change of a position recomputes the cost center's
values in the same transaction, so they share the request's change batch.
"""

import uuid
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal, cast

from sqlalchemy import (
    Connection,
    Engine,
    Integer,
    Row,
    delete,
    func,
    insert,
    literal,
    select,
    tuple_,
    update,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError

from planlayer.access import AccessError, User, cost_center_rights
from planlayer.domain.periods import QUARTERS, closed_months, quarter_months
from planlayer.domain.workflow import EDITABLE, RoundStatus, Status
from planlayer.domain.workforce import (
    MODES,
    BuildUp,
    Measure,
    Mode,
    Position,
    PositionError,
    TransferKind,
    build_up,
    check,
    check_transfer,
    measures,
    month_end,
    rollup,
)
from planlayer.planning import (
    MONTHS,
    Cell,
    ComparisonColumn,
    Conflict,
    ConflictError,
    EditError,
    LockedError,
    _comparisons,
    _locked,
    _parse,
)
from planlayer.planning import _Context as _SheetContext
from planlayer.rounds import Grain, NotFoundError, RoundError, RoundRef, fiscal_periods, get_round
from planlayer.store.bulk import insert_all
from planlayer.store.schema import (
    BASE_LAYER,
    COST_CENTER,
    ENTITY,
    FTE,
    HEADCOUNT,
    MODULE_COST_CENTER,
    MODULE_WORKFORCE,
    NO_ENTITY,
    STAFF_CATEGORY,
    STAFF_TYPE,
    app_user,
    comparison,
    fact_workforce,
    hr_staff,
    member,
    planning_round,
    round_member,
    round_module,
    version,
    workforce_change_log,
    workforce_line,
    workforce_position,
    workforce_transfer,
)
from planlayer.store.uow import system_rights, unit_of_work
from planlayer.sync import ACTUALS_CODE
from planlayer.workflow import enabled_members, status_of

CENT = Decimal("0.01")
NUMBERING_LOCK = 2020  # advisory lock class of the position numbers (ADR 0020)
MEASURE_ID: dict[Measure, int] = {"fte": FTE, "headcount": HEADCOUNT}
MEASURE_OF = {v: k for k, v in MEASURE_ID.items()}
Line = tuple[int, int]  # staff type, staff category
Key = tuple[int, int, int]  # measure, staff type, staff category
Months = list[Decimal | None]


# --- The sheet ------------------------------------------------------------------------------------


@dataclass
class WorkforceRow:
    kind: Literal["line", "group", "total"]
    measure: Measure
    staff_type_code: str | None
    staff_type_name: str | None
    plan: list[Cell]
    comparisons: list[Months]  # one list of 12 months per comparison column
    staff_category_code: str | None = None
    staff_category_name: str | None = None
    external: bool = False
    editable: bool = False


@dataclass
class PositionRow:
    position_id: int
    code: str | None
    title: str
    person_name: str | None
    staff_type_code: str
    staff_category_code: str
    entry_date: date | None
    leave_date: date | None
    contract_percent: Decimal | None
    is_plan: bool
    is_replacement: bool
    description: str | None
    counted: list[bool]  # per month: active on its last day
    changed_by: str | None
    origin: str = "manual"  # hr: taken from HR's staff list; its number, name, entry date stay
    removable: bool = True  # whether the reader may delete it
    transferred_from: str | None = None  # the cost center it came from (ADR 0021)
    transferred_to: str | None = None  # ended here in a transfer during the year, to this one
    transfer: "Transfer | None" = None  # a proposed transfer, waiting for the receiving side
    transferable: bool = False  # whether the reader may propose a transfer now


@dataclass
class StaffType:
    code: str
    name: str
    external: bool


@dataclass
class WorkforceSheet:
    version_code: str
    version_name: str
    fiscal_year: int
    round_status: RoundStatus
    deadline: date | None
    cost_center_code: str
    cost_center_name: str
    status: Status
    lock_reason: str | None  # None: editable
    mode: Mode
    periods: list[date]
    comparisons: list[ComparisonColumn] = field(default_factory=list)
    rows: list[WorkforceRow] = field(default_factory=list)
    version_kind: str = "budget"
    grain: Grain = "month"
    actuals_through: date | None = None
    closed_months: int = 0
    staff_types: list[StaffType] = field(default_factory=list)
    staff_categories: list[tuple[str, str]] = field(default_factory=list)
    positions: list[PositionRow] = field(default_factory=list)
    build_up: BuildUp | None = None  # positions rounds
    hr_available: int = 0  # people in HR's list not yet among the positions
    incoming: list["Transfer"] = field(default_factory=list)  # proposed to this cost center


@dataclass
class _Context:
    round: RoundRef
    mode: Mode
    actuals_id: int
    cost_center_id: int
    cost_center_code: str
    cost_center_name: str
    entity_id: int
    status: Status
    lock_reason: str | None
    periods: list[date]
    prior_periods: list[date]

    @property
    def version_id(self) -> int:
        return self.round.version_id

    @property
    def closed(self) -> int:
        """A forecast's closed months (ADR 0015): they hold actuals."""
        return closed_months(self.periods, self.round.actuals_through)

    @property
    def open_periods(self) -> list[date]:
        return self.periods[self.closed :]


def mode_of(conn: Connection, version_id: int) -> Mode | None:
    found = conn.execute(
        select(round_module.c.mode).where(
            round_module.c.version_id == version_id, round_module.c.module == MODULE_WORKFORCE
        )
    ).scalar()
    return cast(Mode | None, found)


def _entity(conn: Connection, entity_code: str | None) -> int:
    return int(
        conn.execute(
            select(member.c.member_id).where(
                member.c.dim_id == ENTITY, member.c.code == (entity_code or NO_ENTITY)
            )
        ).scalar_one()
    )


def _actuals_id(conn: Connection) -> int:
    found = conn.execute(
        select(version.c.version_id).where(version.c.code == ACTUALS_CODE)
    ).scalar()
    return -1 if found is None else int(found)


def _context(conn: Connection, user: User, version_code: str, cc_code: str) -> _Context:
    r = get_round(conn, version_code)
    mode = mode_of(conn, r.version_id)
    if mode is None:
        raise NotFoundError(f"{version_code} has no workforce planning")
    cc = conn.execute(
        select(member).where(
            member.c.dim_id == COST_CENTER, member.c.code == cc_code, member.c.is_group.is_(False)
        )
    ).first()
    rights = cost_center_rights(conn, user)
    if cc is None or cc.member_id not in rights:
        raise NotFoundError(f"cost center {cc_code} not found")
    status = status_of(conn, r.version_id, cc.member_id)
    # Controllers prepare a round in setup (ADR 0014).
    if r.status != "open" and not (r.status == "setup" and user.is_controller):
        reason: str | None = f"the planning round is {r.status}"
    elif not enabled_members(conn, r.version_id).get(cc.member_id, False):
        reason = "the cost center is not enabled in this round"
    elif status not in EDITABLE:
        reason = f"the cost center is {status}"
    elif not rights[cc.member_id]:
        reason = "you may only read this cost center"
    else:
        reason = None
    return _Context(
        round=r,
        mode=mode,
        actuals_id=_actuals_id(conn),
        cost_center_id=cc.member_id,
        cost_center_code=cc.code,
        cost_center_name=cc.name,
        entity_id=_entity(conn, cc.entity_code),
        status=status,
        lock_reason=reason,
        periods=fiscal_periods(conn, r.fiscal_year),
        prior_periods=fiscal_periods(conn, r.fiscal_year - 1),
    )


def _plain_context(conn: Connection, r: RoundRef, mode: Mode, cc_id: int) -> _Context:
    """A cost center's context for writing by the controller or the system, without the rights
    and status checks (the store's locks still apply)."""
    cc = conn.execute(select(member).where(member.c.member_id == cc_id)).one()
    return _Context(
        round=r,
        mode=mode,
        actuals_id=-1,
        cost_center_id=cc_id,
        cost_center_code=cc.code,
        cost_center_name=cc.name,
        entity_id=_entity(conn, cc.entity_code),
        status="not_started",
        lock_reason=None,
        periods=fiscal_periods(conn, r.fiscal_year),
        prior_periods=fiscal_periods(conn, r.fiscal_year - 1),
    )


def _values(
    conn: Connection,
    version_id: int,
    cc_id: int,
    periods: list[date],
    *,
    lock: bool = False,
) -> dict[Key, list[Cell]]:
    """Cells per measure x staff type x category over ``periods``."""
    f = fact_workforce
    query = select(
        f.c.measure_id,
        f.c.dim_staff_type,
        f.c.dim_staff_category,
        f.c.period,
        f.c.amount,
        f.c.row_version,
    ).where(
        f.c.version_id == version_id,
        f.c.layer_id == BASE_LAYER,
        f.c.dim_cost_center == cc_id,
        f.c.period.in_(periods),
    )
    if lock:
        query = query.with_for_update()
    index = {p: i for i, p in enumerate(periods)}
    out: dict[Key, list[Cell]] = defaultdict(lambda: [Cell(None, None) for _ in range(MONTHS)])
    for m, t, c, p, amount, rv in conn.execute(query):
        out[(m, t, c)][index[p]] = Cell(amount, rv)
    return dict(out)


def _months(cells: dict[Key, list[Cell]]) -> dict[Key, Months]:
    return {k: [c.value for c in v] for k, v in cells.items()}


def _version_values(conn: Connection, ctx: _Context, version_id: int) -> dict[Key, Months]:
    """Another round's values for the cost center; a forecast as its full year."""
    found = conn.execute(
        select(planning_round.c.fiscal_year, planning_round.c.actuals_through).where(
            planning_round.c.version_id == version_id
        )
    ).first()
    periods = ctx.periods if found is None else fiscal_periods(conn, found.fiscal_year)
    values = _months(_values(conn, version_id, ctx.cost_center_id, periods))
    closed = 0 if found is None else closed_months(periods, found.actuals_through)
    if closed:
        actual = _months(_values(conn, ctx.actuals_id, ctx.cost_center_id, periods[:closed]))
        for key in set(values) | set(actual):
            months = values.get(key, [None] * MONTHS)
            values[key] = actual.get(key, [None] * MONTHS)[:closed] + months[closed:]
    return values


def _compared(conn: Connection, ctx: _Context) -> list[dict[Key, Months]]:
    """The round's comparison columns in FTE and headcount (ADR 0016, ADR 0020)."""
    out: list[dict[Key, Months]] = []
    for kind, other_id in conn.execute(
        select(comparison.c.kind, comparison.c.compare_version_id)
        .where(
            comparison.c.version_id == ctx.version_id,
            comparison.c.module == MODULE_COST_CENTER,
        )
        .order_by(comparison.c.position)
    ):
        match kind:
            case "prior_year_actuals":
                out.append(
                    _months(_values(conn, ctx.actuals_id, ctx.cost_center_id, ctx.prior_periods))
                )
            case "actuals":
                periods = ctx.periods[: ctx.closed] if ctx.closed else ctx.periods
                out.append(_months(_values(conn, ctx.actuals_id, ctx.cost_center_id, periods)))
            case _:
                out.append(_version_values(conn, ctx, other_id))
    return out


def _sheet_context(ctx: _Context) -> _SheetContext:
    return _SheetContext(
        ctx.round,
        ctx.actuals_id,
        ctx.cost_center_id,
        ctx.cost_center_name,
        ctx.entity_id,
        ctx.status,
        ctx.lock_reason,
        ctx.periods,
        ctx.prior_periods,
    )


def _added_lines(conn: Connection, ctx: _Context) -> set[Line]:
    return {
        (t, c)
        for t, c in conn.execute(
            select(workforce_line.c.staff_type_id, workforce_line.c.staff_category_id).where(
                workforce_line.c.version_id == ctx.version_id,
                workforce_line.c.cost_center_id == ctx.cost_center_id,
            )
        )
    }


def _positions(conn: Connection, ctx: _Context, *, lock: bool = False) -> list[Row[Any]]:
    query = (
        select(workforce_position)
        .where(
            workforce_position.c.version_id == ctx.version_id,
            workforce_position.c.cost_center_id == ctx.cost_center_id,
        )
        .order_by(
            workforce_position.c.is_plan,
            workforce_position.c.title,
            workforce_position.c.person_name,
            workforce_position.c.position_id,
        )
    )
    if lock:
        query = query.with_for_update()
    return list(conn.execute(query))


def _domain(r: Row[Any], moved_in: bool = False) -> Position:
    return Position(
        r.staff_type_id,
        r.staff_category_id,
        r.entry_date,
        r.leave_date,
        r.contract_percent,
        r.is_plan,
        r.is_replacement,
        moved_in,
        r.transferred_to_id is not None,
    )


def _staff_types(conn: Connection) -> list[StaffType]:
    return [
        StaffType(code, name, bool(external))
        for code, name, external in conn.execute(
            select(member.c.code, member.c.name, member.c.external)
            .where(member.c.dim_id == STAFF_TYPE, member.c.is_group.is_(False))
            .order_by(member.c.code)
        )
    ]


def _categories(conn: Connection) -> list[tuple[str, str]]:
    return [
        (c, n)
        for c, n in conn.execute(
            select(member.c.code, member.c.name)
            .where(member.c.dim_id == STAFF_CATEGORY, member.c.is_group.is_(False))
            .order_by(member.c.name)
        )
    ]


def _hr_candidates(conn: Connection, ctx: _Context) -> list[Row[Any]]:
    """People in HR's list for the cost center, active at some point of the fiscal year, whose
    number isn't among the round's positions yet."""
    taken = select(workforce_position.c.code).where(
        workforce_position.c.version_id == ctx.version_id,
        workforce_position.c.code.is_not(None),
    )
    return list(
        conn.execute(
            select(hr_staff)
            .where(
                hr_staff.c.cost_center_id == ctx.cost_center_id,
                hr_staff.c.leave_date.is_(None) | (hr_staff.c.leave_date >= ctx.periods[0]),
                hr_staff.c.entry_date.is_(None)
                | (hr_staff.c.entry_date <= month_end(ctx.periods[-1])),
                hr_staff.c.code.not_in(taken),
            )
            .order_by(hr_staff.c.code)
        )
    )


def read_sheet(engine: Engine, user: User, version_code: str, cc_code: str) -> WorkforceSheet:
    with unit_of_work(engine, user) as conn:
        ctx = _context(conn, user, version_code, cc_code)
        plan = _values(conn, ctx.version_id, ctx.cost_center_id, ctx.periods)
        if ctx.closed:  # a forecast's closed months show the actuals, read only (ADR 0015)
            booked = _values(conn, ctx.actuals_id, ctx.cost_center_id, ctx.periods[: ctx.closed])
            for key in set(plan) | set(booked):
                cells = list(plan.get(key, [Cell(None, None)] * MONTHS))
                actual = booked.get(key, [Cell(None, None)] * MONTHS)
                cells[: ctx.closed] = [Cell(c.value, None) for c in actual[: ctx.closed]]
                plan[key] = cells
        columns, _ = _comparisons(conn, _sheet_context(ctx))
        compared = _compared(conn, ctx)
        prior = _values(conn, ctx.actuals_id, ctx.cost_center_id, ctx.prior_periods)
        positions = _positions(conn, ctx) if ctx.mode == "positions" else []
        lines = (
            _added_lines(conn, ctx)
            | {(t, c) for _, t, c in plan}
            | {(t, c) for _, t, c in prior}
            | {(p.staff_type_id, p.staff_category_id) for p in positions}
        )
        types = _staff_types(conn)
        categories = _categories(conn)
        ids = (
            {t for t, _ in lines}
            | {c for _, c in lines}
            | {i for p in positions for i in (p.staff_type_id, p.staff_category_id)}
        )
        meta = {
            r.member_id: r
            for r in conn.execute(
                select(member.c.member_id, member.c.code, member.c.name, member.c.external).where(
                    member.c.member_id.in_(ids)
                )
            )
        }
        hr = len(_hr_candidates(conn, ctx)) if ctx.mode == "positions" else 0
        transfers = _transfers(
            conn,
            user,
            (workforce_transfer.c.version_id == ctx.version_id)
            & (workforce_transfer.c.status == "proposed")
            & (
                (workforce_transfer.c.from_cost_center_id == ctx.cost_center_id)
                | (workforce_transfer.c.to_cost_center_id == ctx.cost_center_id)
            ),
        )
        moved_in = set(
            conn.execute(
                select(workforce_transfer.c.new_position_id).where(
                    workforce_transfer.c.version_id == ctx.version_id,
                    workforce_transfer.c.to_cost_center_id == ctx.cost_center_id,
                    workforce_transfer.c.status == "accepted",
                    workforce_transfer.c.new_position_id.is_not(None),
                )
            ).scalars()
        )
        cc_codes = dict(
            conn.execute(
                select(member.c.member_id, member.c.code).where(
                    member.c.member_id.in_(
                        {
                            i
                            for p in positions
                            for i in (p.transferred_from_id, p.transferred_to_id)
                            if i is not None
                        }
                    )
                )
            ).all()
        )

    r = ctx.round
    sheet = WorkforceSheet(
        r.code,
        r.name,
        r.fiscal_year,
        r.status,
        r.deadline,
        ctx.cost_center_code,
        ctx.cost_center_name,
        ctx.status,
        ctx.lock_reason,
        ctx.mode,
        ctx.periods,
        columns,
        version_kind=r.kind,
        grain=r.grain,
        actuals_through=r.actuals_through,
        closed_months=ctx.closed,
        staff_types=types,
        staff_categories=categories,
        hr_available=hr,
        incoming=[t for t in transfers if t.to_code == ctx.cost_center_code],
    )
    outgoing = {t.position_id: t for t in transfers if t.from_code == ctx.cost_center_code}
    typed = ctx.mode != "positions" and ctx.lock_reason is None
    none: Months = [None] * MONTHS
    blank = [Cell(None, None)] * MONTHS
    by_type: dict[int, list[Line]] = defaultdict(list)
    for line in lines:
        by_type[line[0]].append(line)
    type_order = sorted(by_type, key=lambda t: meta[t].code)
    for measure in measures(ctx.mode):
        mid = MEASURE_ID[measure]
        total = WorkforceRow("total", measure, None, None, list(blank), [none for _ in columns])
        for t in type_order:
            group = WorkforceRow(
                "group",
                measure,
                meta[t].code,
                meta[t].name,
                list(blank),
                [none for _ in columns],
                external=bool(meta[t].external),
            )
            sheet.rows.append(group)
            for line in sorted(by_type[t], key=lambda ln: meta[ln[1]].name):
                key = (mid, *line)
                cells = plan.get(key, blank)
                mine = [values.get(key, none) for values in compared]
                sheet.rows.append(
                    WorkforceRow(
                        "line",
                        measure,
                        meta[t].code,
                        meta[t].name,
                        cells,
                        mine,
                        meta[line[1]].code,
                        meta[line[1]].name,
                        bool(meta[t].external),
                        typed,
                    )
                )
                for row in (group, total):
                    row.plan = [
                        Cell(_add(a.value, b.value), None)
                        for a, b in zip(row.plan, cells, strict=True)
                    ]
                    row.comparisons = [
                        [_add(a, b) for a, b in zip(rm, lm, strict=True)]
                        for rm, lm in zip(row.comparisons, mine, strict=True)
                    ]
        sheet.rows.append(total)
    if ctx.mode == "positions":
        sheet.positions = [
            PositionRow(
                p.position_id,
                p.code,
                p.title,
                p.person_name,
                meta[p.staff_type_id].code,
                meta[p.staff_category_id].code,
                p.entry_date,
                p.leave_date,
                p.contract_percent,
                p.is_plan,
                p.is_replacement,
                p.description,
                [_domain(p).counts(period) for period in ctx.periods],
                p.changed_by,
                p.origin,
                p.transferred_to_id is None and (p.origin != "hr" or user.is_controller),
                cc_codes.get(p.transferred_from_id),
                cc_codes.get(p.transferred_to_id),
                outgoing.get(p.position_id),
                ctx.lock_reason is None
                and p.transferred_to_id is None
                and p.position_id not in outgoing,
            )
            for p in positions
        ]
        sheet.build_up = build_up(
            [_domain(p, p.position_id in moved_in) for p in positions], ctx.periods
        )
    return sheet


def _add(a: Decimal | None, b: Decimal | None) -> Decimal | None:
    if a is None:
        return b
    return a if b is None else a + b


# --- Writing --------------------------------------------------------------------------------------


def _run(engine: Engine, actor: User, work: Callable[[Connection], None]) -> str:
    """Run ``work`` in one transaction under a new change batch; returns the batch id."""
    batch_id = uuid.uuid4().hex
    try:
        with unit_of_work(engine, actor, batch_id) as conn:
            work(conn)
    except DBAPIError as e:
        locked = _locked(e)
        if locked is None:
            raise
        raise locked from None
    return batch_id


def _editable(conn: Connection, actor: User, version_code: str, cc_code: str) -> _Context:
    ctx = _context(conn, actor, version_code, cc_code)
    if ctx.lock_reason is not None:
        raise LockedError(ctx.lock_reason)
    return ctx


def _member_id(conn: Connection, dim: int, code: str, what: str) -> int:
    found = conn.execute(
        select(member.c.member_id).where(
            member.c.dim_id == dim, member.c.code == code, member.c.is_group.is_(False)
        )
    ).scalar()
    if found is None:
        raise NotFoundError(f"{what} {code} not found")
    return int(found)


@dataclass
class WorkforceEdit:
    staff_type_code: str
    staff_category_code: str
    measure: Measure
    month: int | None  # 0-11; None with no quarter: the year (fills the open months)
    value: str | None  # None = blank
    expected: list[int | None]  # row_version of each month as the client saw it
    quarter: int | None = None  # 0-3 in a round planned by quarter


def _targets(ctx: _Context, e: WorkforceEdit, label: str) -> list[int]:
    """The months an edit fills: one month, a quarter's three, or the year's open months."""
    if e.month is not None and e.quarter is not None:
        raise EditError("an edit names a month or a quarter, not both")
    if e.month is not None:
        if not 0 <= e.month < MONTHS:
            raise EditError(f"month {e.month} out of range")
        if ctx.round.grain == "quarter":
            raise EditError("this round is planned by quarter")
        months = [e.month]
    elif e.quarter is not None:
        if not 0 <= e.quarter < QUARTERS:
            raise EditError(f"quarter {e.quarter} out of range")
        if ctx.round.grain != "quarter":
            raise EditError("this round is planned by month")
        months = list(quarter_months(e.quarter))
    else:
        return list(range(ctx.closed, MONTHS))
    if months[0] < ctx.closed:
        raise EditError(f"{label}: {ctx.periods[months[0]]:%B %Y} is closed in this forecast")
    return months


def apply_edits(
    engine: Engine, actor: User, version_code: str, cc_code: str, edits: list[WorkforceEdit]
) -> str:
    """Write typed FTE and headcount, all or none; returns the change batch."""

    def work(conn: Connection) -> None:
        ctx = _editable(conn, actor, version_code, cc_code)
        if ctx.mode == "positions":
            raise EditError("in this round, FTE and headcount come from the positions")
        shown = measures(ctx.mode)
        types: dict[str, int] = {}
        categories: dict[str, int] = {}
        locked = _values(conn, ctx.version_id, ctx.cost_center_id, ctx.periods, lock=True)
        current = {
            (key, ctx.periods[m]): c.row_version
            for key, cells in locked.items()
            for m, c in enumerate(cells)
            if c.row_version is not None
        }
        conflicts: list[Conflict] = []
        writes: dict[tuple[Key, date], Decimal | None] = {}
        for e in edits:
            if len(e.expected) != MONTHS:
                raise EditError("expected must list 12 row versions")
            if e.measure not in shown:
                raise EditError(f"this round doesn't plan {e.measure}")
            if e.staff_type_code not in types:
                types[e.staff_type_code] = _member_id(
                    conn, STAFF_TYPE, e.staff_type_code, "staff type"
                )
            if e.staff_category_code not in categories:
                categories[e.staff_category_code] = _member_id(
                    conn, STAFF_CATEGORY, e.staff_category_code, "staff category"
                )
            key = (
                MEASURE_ID[e.measure],
                types[e.staff_type_code],
                categories[e.staff_category_code],
            )
            label = f"{e.staff_type_code} / {e.staff_category_code} {e.measure}"
            months = _targets(ctx, e, label)
            for m in months:
                cell = (key, ctx.periods[m])
                if cell not in writes and current.get(cell) != e.expected[m]:
                    conflicts.append(Conflict(label, m))
            value = _parse(e.value)
            if value is not None:
                if value < 0:
                    raise EditError(f"{label}: can't be negative")
                if value != value.quantize(CENT):
                    raise EditError(f"{label}: at most two decimal places")
            for m in months:
                writes[(key, ctx.periods[m])] = value
        if conflicts:
            raise ConflictError(conflicts)
        _write(conn, ctx, writes, current)

    return _run(engine, actor, work)


def _write(
    conn: Connection,
    ctx: _Context,
    writes: dict[tuple[Key, date], Decimal | None],
    current: dict[tuple[Key, date], int],
) -> None:
    f = fact_workforce
    deletes = [
        (m, t, c, p)
        for ((m, t, c), p), v in writes.items()
        if v is None and ((m, t, c), p) in current
    ]
    if deletes:
        conn.execute(
            delete(f).where(
                f.c.version_id == ctx.version_id,
                f.c.layer_id == BASE_LAYER,
                f.c.dim_cost_center == ctx.cost_center_id,
                tuple_(f.c.measure_id, f.c.dim_staff_type, f.c.dim_staff_category, f.c.period).in_(
                    deletes
                ),
            )
        )
    upserts = [
        {
            "version_id": ctx.version_id,
            "layer_id": BASE_LAYER,
            "measure_id": m,
            "period": p,
            "dim_entity": ctx.entity_id,
            "dim_cost_center": ctx.cost_center_id,
            "dim_staff_type": t,
            "dim_staff_category": c,
            "amount": v,
        }
        for ((m, t, c), p), v in writes.items()
        if v is not None
    ]
    if upserts:
        stmt = pg_insert(f).values(upserts)
        conn.execute(
            stmt.on_conflict_do_update(
                index_elements=list(f.primary_key.columns),
                set_={"amount": stmt.excluded.amount, "row_version": f.c.row_version + 1},
                where=f.c.amount != stmt.excluded.amount,
            )
        )


def undo_batch(engine: Engine, actor: User, version_code: str, cc_code: str, batch_id: str) -> str:
    """Revert one of the actor's change batches of typed values (ADR 0010); refused as a
    conflict if any of its cells changed since. Returns the new batch, which redoes it."""

    def work(conn: Connection) -> None:
        ctx = _editable(conn, actor, version_code, cc_code)
        if ctx.mode == "positions":
            raise EditError("in this round, FTE and headcount come from the positions")
        log = workforce_change_log
        logged = conn.execute(
            select(
                log.c.measure_id,
                log.c.dim_staff_type,
                log.c.dim_staff_category,
                log.c.period,
                log.c.old_amount,
                log.c.new_amount,
            )
            .where(
                log.c.batch_id == batch_id,
                log.c.actor == actor.login,
                log.c.version_id == ctx.version_id,
                log.c.dim_cost_center == ctx.cost_center_id,
            )
            .order_by(log.c.id)
        ).all()
        if not logged:
            raise NotFoundError(f"no change of yours to undo on {cc_code}")
        before: dict[tuple[Key, date], Decimal | None] = {}
        after: dict[tuple[Key, date], Decimal | None] = {}
        for m, t, c, p, old, new in logged:
            before.setdefault(((m, t, c), p), old)
            after[((m, t, c), p)] = new
        locked = _values(conn, ctx.version_id, ctx.cost_center_id, ctx.periods, lock=True)
        blank = [Cell(None, None)] * MONTHS
        current = {
            (key, ctx.periods[m]): c.row_version
            for key, cells in locked.items()
            for m, c in enumerate(cells)
            if c.row_version is not None
        }
        changed = [
            (key, ctx.periods.index(p))
            for (key, p), new in after.items()
            if locked.get(key, blank)[ctx.periods.index(p)].value != new
        ]
        if changed:
            codes = dict(
                conn.execute(
                    select(member.c.member_id, member.c.code).where(
                        member.c.member_id.in_({i for (_, t, c), _ in changed for i in (t, c)})
                    )
                ).all()
            )
            raise ConflictError(
                [
                    Conflict(f"{codes[t]} / {codes[c]} {MEASURE_OF[m]}", month)
                    for (m, t, c), month in sorted(changed)
                ]
            )
        _write(conn, ctx, before, current)

    return _run(engine, actor, work)


def add_line(
    engine: Engine, actor: User, version_code: str, cc_code: str, type_code: str, category: str
) -> str:
    """Add a staff type x category line to the cost center's sheet in this round."""

    def work(conn: Connection) -> None:
        ctx = _editable(conn, actor, version_code, cc_code)
        if ctx.mode == "positions":
            raise EditError("in this round, lines come from the positions")
        t = _member_id(conn, STAFF_TYPE, type_code, "staff type")
        c = _member_id(conn, STAFF_CATEGORY, category, "staff category")
        plan = _values(conn, ctx.version_id, ctx.cost_center_id, ctx.periods)
        if (t, c) in _added_lines(conn, ctx) or any(k[1:] == (t, c) for k in plan):
            raise EditError(f"{type_code} / {category} is on the sheet already")
        conn.execute(
            insert(workforce_line).values(
                version_id=ctx.version_id,
                module=MODULE_WORKFORCE,
                cost_center_id=ctx.cost_center_id,
                staff_type_id=t,
                staff_category_id=c,
                changed_by=actor.login,
            )
        )

    return _run(engine, actor, work)


# --- Positions ------------------------------------------------------------------------------------


@dataclass
class PositionIn:
    title: str
    staff_type_code: str
    staff_category_code: str
    person_name: str | None = None
    entry_date: date | None = None
    leave_date: date | None = None
    contract_percent: Decimal | None = None
    is_plan: bool = False
    is_replacement: bool = False
    description: str | None = None


def _blank(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _position_values(conn: Connection, p: PositionIn) -> dict[str, object]:
    title = p.title.strip()
    if not title:
        raise EditError("a position needs a title")
    t = conn.execute(
        select(member.c.member_id, member.c.external).where(
            member.c.dim_id == STAFF_TYPE,
            member.c.code == p.staff_type_code,
            member.c.is_group.is_(False),
        )
    ).first()
    if t is None:
        raise NotFoundError(f"staff type {p.staff_type_code} not found")
    c = _member_id(conn, STAFF_CATEGORY, p.staff_category_code, "staff category")
    percent = p.contract_percent
    if percent is not None and percent != percent.quantize(CENT):
        raise EditError("a contract percentage has at most two decimal places")
    try:
        check(
            Position(
                t.member_id, c, p.entry_date, p.leave_date, percent, p.is_plan, p.is_replacement
            ),
            bool(t.external),
        )
    except PositionError as e:
        raise EditError(str(e)) from None
    return {
        "title": title,
        "person_name": _blank(p.person_name),
        "staff_type_id": t.member_id,
        "staff_category_id": c,
        "entry_date": p.entry_date,
        "leave_date": p.leave_date,
        "contract_percent": percent,
        "is_plan": p.is_plan,
        "is_replacement": p.is_replacement,
        "description": _blank(p.description),
    }


# A position taken from HR keeps these as HR has them (ADR 0020); the store refuses changes too.
HR_FIELDS = ("person_name", "entry_date", "is_plan", "is_replacement")


def _check_hr_fields(old: Row[Any], values: dict[str, object]) -> None:
    changed = [f for f in HR_FIELDS if values[f] != getattr(old, f)]
    if changed:
        raise EditError(
            f"{old.code} is taken from HR: its number, name, entry date and plan flags stay as "
            "HR has them"
        )


def _not_transferred_out(old: Row[Any]) -> None:
    if old.transferred_to_id is not None:
        raise EditError(
            f"{old.code} ended here in a transfer to another cost center; it stays as it was "
            "transferred"
        )


def _next_number(conn: Connection, version_id: int) -> str:
    """The next of Planlayer's position numbers in the round: N00001, N00002, ... One
    transaction at a time takes a number (an advisory lock on the round)."""
    conn.execute(select(func.pg_advisory_xact_lock(NUMBERING_LOCK, version_id)))
    p = workforce_position
    last = conn.execute(
        select(func.max(func.substring(p.c.code, 2).cast(Integer))).where(
            p.c.version_id == version_id, p.c.origin == "manual"
        )
    ).scalar()
    return f"N{(last or 0) + 1:05d}"


def _positions_context(conn: Connection, actor: User, version_code: str, cc_code: str) -> _Context:
    ctx = _editable(conn, actor, version_code, cc_code)
    if ctx.mode != "positions":
        raise EditError("this round plans FTE and headcount without positions")
    return ctx


def save_position(
    engine: Engine,
    actor: User,
    version_code: str,
    cc_code: str,
    position: PositionIn,
    position_id: int | None = None,
) -> tuple[str, int]:
    """Add a position (``position_id`` None) or change one, and roll the cost center's
    positions up into its FTE and headcount. Returns the change batch and the position's id."""
    saved: list[int] = []

    def work(conn: Connection) -> None:
        ctx = _positions_context(conn, actor, version_code, cc_code)
        values = _position_values(conn, position) | {"changed_by": actor.login}
        p = workforce_position
        if position_id is None:
            saved.append(
                conn.execute(
                    insert(p)
                    .values(
                        version_id=ctx.version_id,
                        module=MODULE_WORKFORCE,
                        cost_center_id=ctx.cost_center_id,
                        code=_next_number(conn, ctx.version_id),
                        origin="manual",
                        **values,
                    )
                    .returning(p.c.position_id)
                ).scalar_one()
            )
        else:
            where = (
                p.c.position_id == position_id,
                p.c.version_id == ctx.version_id,
                p.c.cost_center_id == ctx.cost_center_id,
            )
            old = conn.execute(select(p).where(*where).with_for_update()).first()
            if old is None:
                raise NotFoundError(f"position {position_id} not found on {cc_code}")
            _not_transferred_out(old)
            if old.origin == "hr":
                _check_hr_fields(old, values)
            conn.execute(update(p).where(*where).values(**values, changed_at=func.now()))
            saved.append(position_id)
        write_rollup(conn, ctx)

    return _run(engine, actor, work), saved[0]


def delete_position(
    engine: Engine, actor: User, version_code: str, cc_code: str, position_id: int
) -> str:
    """Remove a position. One taken from HR only a controller removes, e.g. a person HR has
    moved to another cost center; planners set its leave date instead."""

    def work(conn: Connection) -> None:
        ctx = _positions_context(conn, actor, version_code, cc_code)
        old = conn.execute(
            select(workforce_position).where(
                workforce_position.c.position_id == position_id,
                workforce_position.c.version_id == ctx.version_id,
                workforce_position.c.cost_center_id == ctx.cost_center_id,
            )
        ).first()
        if old is not None:
            _not_transferred_out(old)
        if old is not None and old.origin == "hr" and not actor.is_controller:
            raise AccessError(
                "only controllers remove a position taken from HR; set its leave date instead"
            )
        found = conn.execute(
            delete(workforce_position)
            .where(
                workforce_position.c.position_id == position_id,
                workforce_position.c.version_id == ctx.version_id,
                workforce_position.c.cost_center_id == ctx.cost_center_id,
            )
            .returning(workforce_position.c.position_id)
        ).scalar()
        if found is None:
            raise NotFoundError(f"position {position_id} not found on {cc_code}")
        write_rollup(conn, ctx)

    return _run(engine, actor, work)


def _take_hr(conn: Connection, ctx: _Context, actor: User) -> int:
    """HR's people of the cost center not yet among the positions, as positions of people
    employed today (no plan flag). Returns how many were added."""
    rows = [
        {
            "version_id": ctx.version_id,
            "module": MODULE_WORKFORCE,
            "cost_center_id": ctx.cost_center_id,
            "code": h.code,
            "title": h.title,
            "person_name": h.person_name,
            "staff_type_id": h.staff_type_id,
            "staff_category_id": h.staff_category_id,
            "entry_date": h.entry_date,
            "leave_date": h.leave_date,
            "contract_percent": h.contract_percent,
            "origin": "hr",
            "changed_by": actor.login,
        }
        for h in _hr_candidates(conn, ctx)
    ]
    if rows:
        conn.execute(insert(workforce_position), rows)
        write_rollup(conn, ctx)
    return len(rows)


def take_from_hr(engine: Engine, actor: User, version_code: str, cc_code: str) -> tuple[str, int]:
    """*Add staff from HR* on one cost center (ADR 0020)."""
    added: list[int] = []

    def work(conn: Connection) -> None:
        added.append(_take_hr(conn, _positions_context(conn, actor, version_code, cc_code), actor))

    return _run(engine, actor, work), added[0]


def take_all_from_hr(engine: Engine, actor: User, version_code: str) -> int:
    """*Add staff from HR* for every cost center planned in the round; controllers only, in setup
    or while the round is open. Cost centers whose status no longer allows it are skipped."""
    actor.require_controller()
    added: list[int] = []

    def work(conn: Connection) -> None:
        r = get_round(conn, version_code, lock="share")
        if mode_of(conn, r.version_id) != "positions":
            raise RoundError("the round doesn't plan positions")
        if r.status not in ("setup", "open"):
            raise RoundError(f"the planning round is {r.status}")
        planned = [m for m, on in enabled_members(conn, r.version_id).items() if on]
        total = 0
        for cc_id in sorted(planned):
            if status_of(conn, r.version_id, cc_id) not in EDITABLE:
                continue
            total += _take_hr(conn, _plain_context(conn, r, "positions", cc_id), actor)
        added.append(total)

    _run(engine, actor, work)
    return added[0]


def write_rollup(conn: Connection, ctx: _Context) -> None:
    """The cost center's FTE and headcount in the open months, from its positions; stored
    values that differ are replaced, and values of lines without positions removed."""
    positions = [_domain(p) for p in _positions(conn, ctx)]
    periods = ctx.open_periods
    wanted: dict[tuple[Key, date], Decimal] = {}
    for (t, c), by_measure in rollup(positions, periods).items():
        for measure, months in by_measure.items():
            for p, v in zip(periods, months, strict=True):
                if v is not None:
                    wanted[((MEASURE_ID[measure], t, c), p)] = v.quantize(CENT, ROUND_HALF_UP)
    stored = _values(conn, ctx.version_id, ctx.cost_center_id, ctx.periods, lock=True)
    current: dict[tuple[Key, date], int] = {}
    writes: dict[tuple[Key, date], Decimal | None] = {}
    for key, cells in stored.items():
        for m, cell in enumerate(cells):
            if cell.row_version is None or m < ctx.closed:
                continue
            current[(key, ctx.periods[m])] = cell.row_version
            if (key, ctx.periods[m]) not in wanted:
                writes[(key, ctx.periods[m])] = None
            elif wanted[(key, ctx.periods[m])] == cell.value:
                del wanted[(key, ctx.periods[m])]
    writes |= wanted
    _write(conn, ctx, writes, current)


# --- Transfers (ADR 0021) -------------------------------------------------------------------------


@dataclass
class Transfer:
    """A position's transfer to another cost center, as both sides see it."""

    transfer_id: int
    version_code: str
    version_name: str
    status: Literal["proposed", "accepted", "declined", "cancelled"]
    kind: TransferKind
    starts: date | None  # the first day on the receiving cost center; None: the whole year
    position_id: int
    position_code: str
    title: str
    person_name: str | None
    staff_type_name: str
    staff_category_name: str
    contract_percent: Decimal | None
    from_code: str
    from_name: str
    to_code: str
    to_name: str
    note: str | None
    proposed_by: str
    proposed_at: datetime
    decided_by: str | None
    decided_at: datetime | None
    reason: str | None
    can_decide: bool  # the reader writes the receiving cost center
    can_cancel: bool  # the reader writes the sending cost center


class TransferError(EditError):
    """A transfer that can't be proposed or decided as asked (422, like an edit)."""


def _transfers(conn: Connection, user: User, where: Any) -> list[Transfer]:
    """Transfers the user may read (either side), oldest first. The position is read with the
    system's rights: the receiving side sees whom it is to take over."""
    t = workforce_transfer
    rows = conn.execute(select(t).where(where).order_by(t.c.proposed_at, t.c.transfer_id)).all()
    if not rows:
        return []
    with system_rights(conn, actor=user.login):
        positions = {
            p.position_id: p
            for p in conn.execute(
                select(workforce_position).where(
                    workforce_position.c.position_id.in_({r.position_id for r in rows})
                )
            )
        }
    ids = {i for r in rows for i in (r.from_cost_center_id, r.to_cost_center_id)} | {
        i for p in positions.values() for i in (p.staff_type_id, p.staff_category_id)
    }
    meta = dict(
        conn.execute(
            select(member.c.member_id, func.json_build_array(member.c.code, member.c.name)).where(
                member.c.member_id.in_(ids)
            )
        ).all()
    )
    people = dict(
        conn.execute(
            select(app_user.c.user_id, app_user.c.display_name).where(
                app_user.c.user_id.in_(
                    {r.proposed_by for r in rows} | {r.decided_by for r in rows if r.decided_by}
                )
            )
        ).all()
    )
    rounds = dict(
        conn.execute(
            select(version.c.version_id, func.json_build_array(version.c.code, version.c.name))
            .where(version.c.version_id.in_({r.version_id for r in rows}))
        ).all()
    )  # fmt: skip
    rights = cost_center_rights(conn, user)
    out: list[Transfer] = []
    for r in rows:
        p = positions.get(r.position_id)
        if p is None:
            continue
        out.append(
            Transfer(
                r.transfer_id,
                rounds[r.version_id][0],
                rounds[r.version_id][1],
                r.status,
                r.kind,
                r.starts,
                r.position_id,
                p.code,
                p.title,
                p.person_name,
                meta[p.staff_type_id][1],
                meta[p.staff_category_id][1],
                p.contract_percent,
                meta[r.from_cost_center_id][0],
                meta[r.from_cost_center_id][1],
                meta[r.to_cost_center_id][0],
                meta[r.to_cost_center_id][1],
                r.note,
                people[r.proposed_by],
                r.proposed_at,
                people.get(r.decided_by) if r.decided_by else None,
                r.decided_at,
                r.reason,
                rights.get(r.to_cost_center_id, False),
                rights.get(r.from_cost_center_id, False),
            )
        )
    return out


@dataclass
class TransferTarget:
    code: str
    name: str
    status: Status
    editable: bool  # its status still takes positions


def transfer_targets(
    engine: Engine, user: User, version_code: str, cc_code: str
) -> list[TransferTarget]:
    """The cost centers planned in the round that a position of ``cc_code`` may go to."""
    with unit_of_work(engine, user) as conn:
        ctx = _context(conn, user, version_code, cc_code)
        planned = [m for m, on in enabled_members(conn, ctx.version_id).items() if on]
        rows = conn.execute(
            select(member.c.member_id, member.c.code, member.c.name)
            .where(member.c.member_id.in_(planned), member.c.member_id != ctx.cost_center_id)
            .order_by(member.c.code)
        ).all()
        out = []
        for m in rows:
            status = status_of(conn, ctx.version_id, m.member_id)
            out.append(TransferTarget(m.code, m.name, status, status in EDITABLE))
    return out


def propose_transfer(
    engine: Engine,
    actor: User,
    version_code: str,
    cc_code: str,
    position_id: int,
    to_code: str,
    kind: TransferKind,
    starts: date | None,
    note: str | None = None,
) -> int:
    """Propose to transfer a position to another cost center planned in the round. Nothing
    moves until the receiving side accepts. Returns the transfer's id."""
    created: list[int] = []

    def work(conn: Connection) -> None:
        ctx = _positions_context(conn, actor, version_code, cc_code)
        p = conn.execute(
            select(workforce_position).where(
                workforce_position.c.position_id == position_id,
                workforce_position.c.version_id == ctx.version_id,
                workforce_position.c.cost_center_id == ctx.cost_center_id,
            )
        ).first()
        if p is None:
            raise NotFoundError(f"position {position_id} not found on {cc_code}")
        _not_transferred_out(p)
        pending = conn.execute(
            select(workforce_transfer.c.transfer_id).where(
                workforce_transfer.c.position_id == position_id,
                workforce_transfer.c.status == "proposed",
            )
        ).first()
        if pending is not None:
            raise TransferError(f"{p.code} has a transfer waiting already")
        to_id = conn.execute(
            select(member.c.member_id).where(
                member.c.dim_id == COST_CENTER,
                member.c.code == to_code,
                member.c.is_group.is_(False),
            )
        ).scalar()
        if to_id is None:
            raise NotFoundError(f"cost center {to_code} not found")
        if to_id == ctx.cost_center_id:
            raise TransferError("a position transfers to another cost center")
        if not enabled_members(conn, ctx.version_id).get(to_id, False):
            raise TransferError(f"{to_code} isn't planned in this round")
        try:
            check_transfer(p.entry_date, p.leave_date, kind, starts, ctx.periods, ctx.closed)
        except PositionError as e:
            raise TransferError(str(e)) from None
        created.append(
            conn.execute(
                insert(workforce_transfer)
                .values(
                    version_id=ctx.version_id,
                    module=MODULE_WORKFORCE,
                    position_id=position_id,
                    from_cost_center_id=ctx.cost_center_id,
                    to_cost_center_id=to_id,
                    kind=kind,
                    starts=starts,
                    note=_blank(note),
                    proposed_by=actor.user_id,
                )
                .returning(workforce_transfer.c.transfer_id)
            ).scalar_one()
        )

    _run(engine, actor, work)
    return created[0]


def _proposed(conn: Connection, user: User, transfer_id: int, side: str) -> Row[Any]:
    """The proposed transfer, locked, for a user who writes its ``side`` cost center."""
    query = select(workforce_transfer).where(workforce_transfer.c.transfer_id == transfer_id)
    seen = conn.execute(query).first()
    if seen is None:
        raise NotFoundError(f"transfer {transfer_id} not found")
    if not cost_center_rights(conn, user).get(getattr(seen, f"{side}_cost_center_id"), False):
        raise AccessError(
            "only the receiving cost center's planners accept or decline a transfer"
            if side == "to"
            else "only the sending cost center's planners withdraw a transfer"
        )
    t = conn.execute(query.with_for_update()).one()
    if t.status != "proposed":
        raise TransferError(f"the transfer is {t.status} already")
    return t


def _close(conn: Connection, actor: User, t: Row[Any], status: str, **values: object) -> None:
    conn.execute(
        update(workforce_transfer)
        .where(workforce_transfer.c.transfer_id == t.transfer_id)
        .values(status=status, decided_by=actor.user_id, decided_at=func.now(), **values)
    )


def decide_transfer(
    engine: Engine, actor: User, transfer_id: int, accept: bool, reason: str | None = None
) -> str:
    """Accept or decline a proposed transfer, as the receiving side (its planners, or a
    controller). Accepting moves the position in one change batch:

    - **full:** the position moves with its whole year and remembers where it came from;
    - **during the year:** the sending position ends the day before ``starts``, marked as
      transferred, and the receiving cost center gets the person from ``starts``, with the same
      number.

    Both cost centers' FTE and headcount follow. Either side locked refuses it (423)."""

    def work(conn: Connection) -> None:
        t = _proposed(conn, actor, transfer_id, "to")
        if not accept:
            _close(conn, actor, t, "declined", reason=_blank(reason))
            return
        code = conn.execute(
            select(version.c.code).where(version.c.version_id == t.version_id)
        ).scalar_one()
        to_code = conn.execute(
            select(member.c.code).where(member.c.member_id == t.to_cost_center_id)
        ).scalar_one()
        target = _positions_context(conn, actor, code, to_code)
        with system_rights(conn, actor=actor.login):
            source = _plain_context(conn, target.round, "positions", t.from_cost_center_id)
            status = status_of(conn, t.version_id, t.from_cost_center_id)
            if status not in EDITABLE:
                raise LockedError(f"the sending cost center {source.cost_center_code} is {status}")
            if not enabled_members(conn, t.version_id).get(t.from_cost_center_id, False):
                raise LockedError("the sending cost center is no longer planned in the round")
            p = conn.execute(
                select(workforce_position)
                .where(workforce_position.c.position_id == t.position_id)
                .with_for_update()
            ).one()
            try:
                check_transfer(
                    p.entry_date, p.leave_date, t.kind, t.starts, target.periods, target.closed
                )
            except PositionError as e:
                raise TransferError(f"{p.code} can no longer transfer like this: {e}") from None
            wp = workforce_position
            new_id: int | None = None
            if t.kind == "full":
                conn.execute(
                    update(wp)
                    .where(wp.c.position_id == p.position_id)
                    .values(
                        cost_center_id=t.to_cost_center_id,
                        transferred_from_id=t.from_cost_center_id,
                        changed_by=actor.login,
                        changed_at=func.now(),
                    )
                )
            else:
                conn.execute(
                    update(wp)
                    .where(wp.c.position_id == p.position_id)
                    .values(
                        leave_date=t.starts - timedelta(days=1),
                        transferred_to_id=t.to_cost_center_id,
                        changed_by=actor.login,
                        changed_at=func.now(),
                    )
                )
                kept = {
                    c.name: p._mapping[c.name]
                    for c in wp.c
                    if c.name
                    not in ("position_id", "changed_at", "transferred_to_id", "transferred_from_id")
                }
                new_id = conn.execute(
                    insert(wp)
                    .values(
                        {
                            **kept,
                            "cost_center_id": t.to_cost_center_id,
                            "entry_date": t.starts,
                            "transferred_from_id": t.from_cost_center_id,
                            "changed_by": actor.login,
                        }
                    )
                    .returning(wp.c.position_id)
                ).scalar_one()
            write_rollup(conn, source)
            write_rollup(conn, target)
            _close(conn, actor, t, "accepted", new_position_id=new_id)

    return _run(engine, actor, work)


def cancel_transfer(engine: Engine, actor: User, transfer_id: int) -> None:
    """Withdraw a proposed transfer, as the sending side (its planners, or a controller)."""

    def work(conn: Connection) -> None:
        t = _proposed(conn, actor, transfer_id, "from")
        _close(conn, actor, t, "cancelled")

    _run(engine, actor, work)


RECENT = timedelta(days=30)


def my_transfers(engine: Engine, user: User) -> list[Transfer]:
    """The transfers the user decides (proposed to a cost center they write), those waiting that
    they proposed, and those they proposed that were decided in the last 30 days."""
    t = workforce_transfer
    with unit_of_work(engine, user) as conn:
        writable = [cc for cc, w in cost_center_rights(conn, user).items() if w]
        return _transfers(
            conn,
            user,
            ((t.c.status == "proposed") & t.c.to_cost_center_id.in_(writable))
            | (
                (t.c.proposed_by == user.user_id)
                & ((t.c.status == "proposed") | (t.c.decided_at > func.now() - RECENT))
            ),
        )


# --- Rounds ---------------------------------------------------------------------------------------


def has_workforce(conn: Connection, version_id: int) -> bool:
    """Whether the round holds any workforce values, lines or positions."""
    found = 0
    for table, column in (
        (fact_workforce, fact_workforce.c.version_id),
        (workforce_line, workforce_line.c.version_id),
        (workforce_position, workforce_position.c.version_id),
    ):
        found += conn.execute(
            select(func.count()).select_from(table).where(column == version_id)
        ).scalar_one()
    return found > 0


def set_mode(conn: Connection, r: RoundRef, mode: Mode | None) -> None:
    """Turn workforce planning on with a mode, change the mode, or turn it off (``None``). A
    change clears the round's workforce in setup; an open round with any is refused."""
    if mode is not None and mode not in MODES:
        raise RoundError(f"unknown workforce mode {mode}")
    now = mode_of(conn, r.version_id)
    if now == mode:
        return
    if now is not None and has_workforce(conn, r.version_id):
        if r.status != "setup":
            raise RoundError(
                "the round has workforce values or positions; its workforce mode can change "
                "only in setup, where they are cleared"
            )
        for table, column in (
            (fact_workforce, fact_workforce.c.version_id),
            (workforce_line, workforce_line.c.version_id),
            (workforce_position, workforce_position.c.version_id),
        ):
            conn.execute(delete(table).where(column == r.version_id))
    if mode is None:
        conn.execute(
            delete(round_module).where(
                round_module.c.version_id == r.version_id,
                round_module.c.module == MODULE_WORKFORCE,
            )
        )
        return
    stmt = pg_insert(round_module).values(
        version_id=r.version_id, module=MODULE_WORKFORCE, mode=mode
    )
    conn.execute(
        stmt.on_conflict_do_update(
            index_elements=[round_module.c.version_id, round_module.c.module],
            set_={"mode": stmt.excluded.mode},
        )
    )


def carry(
    conn: Connection, source: RoundRef, target: RoundRef, planned: Iterable[int], values: bool
) -> int:
    """A new round's workforce from the round it is set up like (ADR 0020): the lines added to
    sheets; positions that haven't left before the new year, rolled up again; with ``values``,
    typed FTE and headcount month by month into the target's open months. Returns the number of
    positions and value cells copied. Runs in the round's creating transaction."""
    target_mode, source_mode = mode_of(conn, target.version_id), mode_of(conn, source.version_id)
    ids = set(planned)
    if target_mode is None or source_mode is None or not ids:
        return 0
    old, new = fiscal_periods(conn, source.fiscal_year), fiscal_periods(conn, target.fiscal_year)
    line_columns = [
        c for c in workforce_line.c if c.name not in ("version_id", "changed_by", "changed_at")
    ]
    conn.execute(
        insert(workforce_line).from_select(
            ["version_id", *(c.name for c in line_columns)],
            select(
                literal(target.version_id, workforce_line.c.version_id.type), *line_columns
            ).where(
                workforce_line.c.version_id == source.version_id,
                workforce_line.c.cost_center_id.in_(ids),
            ),
        )
    )
    copied = 0
    if target_mode == "positions" and source_mode == "positions":
        kept = [
            c
            for c in workforce_position.c
            if c.name not in ("position_id", "version_id", "changed_at")
        ]
        rows = [
            {"version_id": target.version_id, **{c.name: r._mapping[c.name] for c in kept}}
            for r in conn.execute(
                select(*kept).where(
                    workforce_position.c.version_id == source.version_id,
                    workforce_position.c.cost_center_id.in_(ids),
                    workforce_position.c.leave_date.is_(None)
                    | (workforce_position.c.leave_date >= new[0]),
                )
            )
        ]
        insert_all(conn, workforce_position, rows)
        for cc_id in sorted({r["cost_center_id"] for r in rows}):
            write_rollup(conn, _plain_context(conn, target, "positions", cast(int, cc_id)))
        copied += len(rows)
    elif values and target_mode != "positions" and source_mode != "positions":
        wanted = [MEASURE_ID[m] for m in measures(target_mode)]
        moved = {
            o: n
            for o, n in zip(old, new, strict=True)
            if target.actuals_through is None or n > target.actuals_through
        }
        f = fact_workforce
        rows = [
            {
                "version_id": target.version_id,
                "layer_id": BASE_LAYER,
                "measure_id": r.measure_id,
                "period": moved[r.period],
                "dim_entity": r.dim_entity,
                "dim_cost_center": r.dim_cost_center,
                "dim_staff_type": r.dim_staff_type,
                "dim_staff_category": r.dim_staff_category,
                "amount": r.amount,
            }
            for r in conn.execute(
                select(f).where(
                    f.c.version_id == source.version_id,
                    f.c.layer_id == BASE_LAYER,
                    f.c.measure_id.in_(wanted),
                    f.c.period.in_(list(moved)),
                    f.c.dim_cost_center.in_(ids),
                )
            )
        ]
        insert_all(conn, f, rows)
        copied += len(rows)
    return copied


def planned_cost_centers(conn: Connection, version_id: int) -> set[int]:
    return set(
        conn.execute(
            select(round_member.c.member_id).where(
                round_member.c.version_id == version_id,
                round_member.c.module == MODULE_COST_CENTER,
                round_member.c.enabled.is_(True),
            )
        ).scalars()
    )
