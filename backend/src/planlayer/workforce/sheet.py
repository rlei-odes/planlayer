"""A cost center's workforce sheet (ADR 0020): reading it, typed FTE and headcount with undo,
and lines added by planners."""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import (
    Connection,
    Engine,
    Row,
    insert,
    select,
)

from planlayer.access import User
from planlayer.domain.periods import QUARTERS, closed_months, quarter_months
from planlayer.domain.projection import project
from planlayer.domain.workflow import RoundStatus, Status
from planlayer.domain.workforce import (
    BuildUp,
    Measure,
    Mode,
    build_up,
    measures,
    month_end,
)
from planlayer.planning import comparison_columns, projected_months
from planlayer.rounds import Grain, NotFoundError, fiscal_periods
from planlayer.sheet import (
    MONTHS,
    Cell,
    ComparisonColumn,
    Conflict,
    ConflictError,
    EditError,
    SheetContext,
    parse_amount,
)
from planlayer.store.schema import (
    MODULE_COST_CENTER,
    MODULE_WORKFORCE,
    STAFF_CATEGORY,
    STAFF_TYPE,
    comparison,
    member,
    planning_round,
    workforce_change_log,
    workforce_line,
    workforce_transfer,
)
from planlayer.store.uow import system_rights, unit_of_work
from planlayer.workforce.common import (
    CENT,
    MEASURE_ID,
    MEASURE_OF,
    Key,
    Line,
    Months,
    _add,
    _Context,
    _context,
    _editable,
    _member_id,
    _months,
    _run,
    _values,
    _write,
)
from planlayer.workforce.positions import _domain, _hr_candidates, _positions
from planlayer.workforce.transfers import Transfer, _transfers


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
    # Per month: counted on the other side of a transfer during the year (after the move on the
    # sending cost center, before it on the receiving one), not here.
    elsewhere: list[bool] = field(default_factory=list)


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
    for kind, other_id, through in conn.execute(
        select(comparison.c.kind, comparison.c.compare_version_id, comparison.c.through)
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
            case "projection":
                # FTE and headcount are stocks: the last booked month carries on.
                periods, booked = projected_months(conn, _sheet_context(ctx), through)
                actual = _months(_values(conn, ctx.actuals_id, ctx.cost_center_id, periods))
                out.append({k: project(v, booked, "last") for k, v in actual.items()})
            case _:
                out.append(_version_values(conn, ctx, other_id))
    return out


def _sheet_context(ctx: _Context) -> SheetContext:
    return SheetContext(
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
        columns, _ = comparison_columns(conn, _sheet_context(ctx))
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
                _elsewhere(p, p.position_id in moved_in, ctx.periods),
            )
            for p in positions
        ]
        sheet.build_up = build_up(
            [_domain(p, p.position_id in moved_in) for p in positions], ctx.periods
        )
    return sheet


def _elsewhere(p: Row[Any], moved_in: bool, periods: list[date]) -> list[bool]:
    """The months a position transferred during the year counts on the other cost center: after
    its leave date where it ended in the transfer, before its entry where it arrived by one."""
    if p.transferred_to_id is not None and p.leave_date is not None:
        return [month_end(m) > p.leave_date for m in periods]
    if moved_in and p.entry_date is not None:
        return [month_end(m) < p.entry_date for m in periods]
    return [False] * len(periods)


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
            value = parse_amount(e.value)
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


def undo_batch(engine: Engine, actor: User, version_code: str, cc_code: str, batch_id: str) -> str:
    """Revert one of the actor's change batches of typed values (ADR 0010); refused as a
    conflict if any of its cells changed since. Returns the new batch, which redoes it."""

    def work(conn: Connection) -> None:
        ctx = _editable(conn, actor, version_code, cc_code)
        if ctx.mode == "positions":
            raise EditError("in this round, FTE and headcount come from the positions")
        log = workforce_change_log
        # The actor's own batch on a sheet they may write: read whole, whatever the change
        # log's row-level security would show.
        with system_rights(conn, actor=actor.login):
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
