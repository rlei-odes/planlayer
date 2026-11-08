"""The planning grid for one cost center in a planning round (T-25): read it, and apply edits.

Rows are the accounts with comparison values or plan values for the cost center, plus the
accounts the planner added, minus the accounts hidden in the round (ADR 0007). They are grouped
by their parent in the account hierarchy, with group subtotals computed here (ADR 0003).

Each account row carries its entry form, which decides the editable cells and how a year total
is spread. Each plan cell carries its ``row_version``; an edit states the versions it was made
against, and a cell changed by someone else in the meantime is a conflict (D-19). Blank and
zero stay distinct: a blank cell has no row.

Whether the cost center can be edited at all (round open, cost center enabled, status not
submitted or approved) is checked here for a clear message, and enforced by the lock trigger.
"""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal, cast

from sqlalchemy import Connection, Engine, and_, delete, select, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError

from planlayer.domain.entry_forms import EntryForm, Form, resolve, weights
from planlayer.domain.spreading import Profile, spread
from planlayer.domain.workflow import EDITABLE, RoundStatus, Status
from planlayer.rounds import NotFoundError, RoundRef, fiscal_periods, get_round
from planlayer.store.schema import (
    ACCOUNT,
    AMOUNT,
    BASE_LAYER,
    COST_CENTER,
    ENTITY,
    H_ACCOUNT,
    MODULE_COST_CENTER,
    closure,
    comparison,
    entry_form,
    fact_cost_center,
    hidden_account,
    hier_edge,
    member,
    planning_round,
    round_module,
    seasonality_profile,
    sheet_account,
    version,
)
from planlayer.store.uow import unit_of_work
from planlayer.sync import ACTUALS_CODE
from planlayer.workflow import enabled_members, status_of

__all__ = ["NotFoundError"]

MONTHS = 12
LOCKED_SQLSTATE = "PL001"  # raised by the lock trigger


class EditError(ValueError):
    """An edit that can't be applied as sent (bad value, unknown account, a form's rule)."""


class LockedError(Exception):
    """The cost center can't be edited in this round now."""


@dataclass
class Conflict:
    account_code: str
    month: int


class ConflictError(Exception):
    def __init__(self, conflicts: list[Conflict]) -> None:
        self.conflicts = conflicts
        super().__init__(f"{len(conflicts)} cell(s) changed by someone else")


@dataclass
class Cell:
    value: Decimal | None
    row_version: int | None


@dataclass
class ComparisonColumn:
    kind: str
    label: str


@dataclass
class Row:
    kind: Literal["group", "account"]
    code: str
    name: str
    group_code: str
    comparisons: list[list[Decimal | None]]  # one list of 12 months per comparison column
    plan: list[Cell]
    # Account rows:
    entry_form: Form | None = None
    basis: str | None = None  # what a year total spreads by, e.g. "prior-year actuals"
    fallback: bool = False  # the reference is empty, so the year total spreads evenly
    months_visible: bool = True
    months_editable: bool = False
    total_editable: bool = False


@dataclass
class Grid:
    version_code: str
    version_name: str
    fiscal_year: int
    round_status: RoundStatus
    deadline: date | None
    cost_center_code: str
    cost_center_name: str
    status: Status
    lock_reason: str | None  # None: editable
    periods: list[date]
    comparisons: list[ComparisonColumn]
    rows: list[Row] = field(default_factory=list)


@dataclass
class Edit:
    account_code: str
    month: int | None  # 0-11, or None for the full year (spread by the line's basis)
    value: str | None  # canonical decimal string ("1234.5", "-3"); None = blank
    expected: list[int | None]  # row_version of each month as the client saw it


@dataclass
class _Context:
    round: RoundRef
    actuals_id: int
    cost_center_id: int
    cost_center_name: str
    entity_id: int
    status: Status
    lock_reason: str | None
    periods: list[date]
    prior_periods: list[date]

    @property
    def version_id(self) -> int:
        return self.round.version_id


@dataclass
class _Line:
    account_id: int
    code: str
    name: str
    group_code: str
    group_name: str
    form: EntryForm
    basis: str
    profile: Profile | None
    reference: list[Decimal | None]


def _context(conn: Connection, version_code: str, cc_code: str) -> _Context:
    r = get_round(conn, version_code)
    cc = conn.execute(
        select(member).where(
            member.c.dim_id == COST_CENTER, member.c.code == cc_code, member.c.is_group.is_(False)
        )
    ).first()
    if cc is None:
        raise NotFoundError(f"cost center {cc_code} not found")
    entity_id = conn.execute(
        select(member.c.member_id).where(member.c.dim_id == ENTITY, member.c.code == cc.entity_code)
    ).scalar_one()
    actuals_id = conn.execute(
        select(version.c.version_id).where(version.c.code == ACTUALS_CODE)
    ).scalar()
    status = status_of(conn, r.version_id, cc.member_id)
    enabled = enabled_members(conn, r.version_id).get(cc.member_id, False)
    if r.status != "open":
        reason: str | None = f"the planning round is {r.status}"
    elif not enabled:
        reason = "the cost center is not enabled in this round"
    elif status not in EDITABLE:
        reason = f"the cost center is {status}"
    else:
        reason = None
    return _Context(
        round=r,
        actuals_id=actuals_id if actuals_id is not None else -1,
        cost_center_id=cc.member_id,
        cost_center_name=cc.name,
        entity_id=entity_id,
        status=status,
        lock_reason=reason,
        periods=fiscal_periods(conn, r.fiscal_year),
        prior_periods=fiscal_periods(conn, r.fiscal_year - 1),
    )


def _values(
    conn: Connection, version_id: int, cc_id: int, periods: list[date], *, lock: bool = False
) -> dict[int, list[Cell]]:
    """Cells per account over ``periods``; blank cells have no value."""
    f = fact_cost_center
    query = select(f.c.dim_account, f.c.period, f.c.amount, f.c.row_version).where(
        f.c.version_id == version_id,
        f.c.layer_id == BASE_LAYER,
        f.c.measure_id == AMOUNT,
        f.c.dim_cost_center == cc_id,
        f.c.period.in_(periods),
    )
    if lock:
        query = query.with_for_update()
    out: dict[int, list[Cell]] = defaultdict(lambda: [Cell(None, None) for _ in range(MONTHS)])
    for acc, period, amount, rv in conn.execute(query):
        out[acc][periods.index(period)] = Cell(amount, rv)
    return out


def _amounts(cells: dict[int, list[Cell]]) -> dict[int, list[Decimal | None]]:
    return {a: [c.value for c in row] for a, row in cells.items()}


def _round_periods(conn: Connection, version_id: int, fallback: list[date]) -> list[date]:
    fy = conn.execute(
        select(planning_round.c.fiscal_year).where(planning_round.c.version_id == version_id)
    ).scalar()
    return fallback if fy is None else fiscal_periods(conn, fy)


def _comparisons(
    conn: Connection, ctx: _Context
) -> tuple[list[ComparisonColumn], list[dict[int, list[Decimal | None]]]]:
    columns, values = [], []
    for kind, other_id, other_name in conn.execute(
        select(comparison.c.kind, comparison.c.compare_version_id, version.c.name)
        .outerjoin(version, version.c.version_id == comparison.c.compare_version_id)
        .where(
            comparison.c.version_id == ctx.version_id,
            comparison.c.module == MODULE_COST_CENTER,
        )
        .order_by(comparison.c.position)
    ):
        match kind:
            case "prior_year_actuals":
                label = f"Actual {ctx.round.fiscal_year - 1}"
                cells = _values(conn, ctx.actuals_id, ctx.cost_center_id, ctx.prior_periods)
            case "actuals":
                label = f"Actual {ctx.round.fiscal_year}"
                cells = _values(conn, ctx.actuals_id, ctx.cost_center_id, ctx.periods)
            case _:
                label = other_name
                periods = _round_periods(conn, other_id, ctx.periods)
                cells = _values(conn, other_id, ctx.cost_center_id, periods)
        columns.append(ComparisonColumn(kind, label))
        values.append(_amounts(cells))
    return columns, values


def _hidden(conn: Connection, version_id: int) -> set[int]:
    return set(
        conn.execute(
            select(hidden_account.c.account_id).where(
                hidden_account.c.version_id == version_id,
                hidden_account.c.module == MODULE_COST_CENTER,
            )
        ).scalars()
    )


def _added(conn: Connection, ctx: _Context) -> set[int]:
    return set(
        conn.execute(
            select(sheet_account.c.account_id).where(
                sheet_account.c.version_id == ctx.version_id,
                sheet_account.c.module == MODULE_COST_CENTER,
                sheet_account.c.cost_center_id == ctx.cost_center_id,
            )
        ).scalars()
    )


def _lines(conn: Connection, ctx: _Context, account_ids: Iterable[int]) -> list[_Line]:
    """The accounts in sheet order, with their resolved entry form and spread basis."""
    ids = sorted(set(account_ids))
    acc, grp = member.alias("acc"), member.alias("grp")
    edge, gedge = hier_edge.alias("edge"), hier_edge.alias("gedge")
    meta = conn.execute(
        select(acc.c.member_id, acc.c.code, acc.c.name, grp.c.code, grp.c.name)
        .join(edge, and_(edge.c.member_id == acc.c.member_id, edge.c.hier_id == H_ACCOUNT))
        .join(grp, grp.c.member_id == edge.c.parent_id)
        .join(gedge, and_(gedge.c.member_id == grp.c.member_id, gedge.c.hier_id == H_ACCOUNT))
        .where(acc.c.member_id.in_(ids))
        .order_by(gedge.c.sort_order, grp.c.code, edge.c.sort_order, acc.c.code)
    ).all()

    default = EntryForm(
        conn.execute(
            select(round_module.c.default_form).where(
                round_module.c.version_id == ctx.version_id,
                round_module.c.module == MODULE_COST_CENTER,
            )
        ).scalar_one()
    )
    # Settings on the account or a group above it; the nearest wins.
    chains: dict[int, list[tuple[int, EntryForm]]] = defaultdict(list)
    for descendant, depth, form, profile_id, ref_id in conn.execute(
        select(
            closure.c.descendant_id,
            closure.c.depth,
            entry_form.c.form,
            entry_form.c.profile_id,
            entry_form.c.reference_version_id,
        )
        .join(entry_form, entry_form.c.account_id == closure.c.ancestor_id)
        .where(
            closure.c.hier_id == H_ACCOUNT,
            closure.c.descendant_id.in_(ids),
            entry_form.c.version_id == ctx.version_id,
            entry_form.c.module == MODULE_COST_CENTER,
        )
    ):
        chains[descendant].append((depth, EntryForm(form, profile_id, ref_id)))
    forms = {
        a: resolve([f for _, f in sorted(chains[a], key=lambda x: x[0])], default) for a in ids
    }

    profile_ids = {f.profile_id for f in forms.values() if f.profile_id is not None}
    profiles = {
        p.profile_id: (p.name, Profile(tuple(p.values), p.entered_as))
        for p in conn.execute(
            select(seasonality_profile).where(seasonality_profile.c.profile_id.in_(profile_ids))
        )
    }
    prior_actuals = _amounts(_values(conn, ctx.actuals_id, ctx.cost_center_id, ctx.prior_periods))
    references: dict[int | None, dict[int, list[Decimal | None]]] = {None: prior_actuals}
    names: dict[int | None, str] = {None: "prior-year actuals"}
    for ref_id in {f.reference_version_id for f in forms.values()} - {None}:
        assert ref_id is not None
        periods = _round_periods(conn, ref_id, ctx.periods)
        references[ref_id] = _amounts(_values(conn, ref_id, ctx.cost_center_id, periods))
        names[ref_id] = conn.execute(
            select(version.c.name).where(version.c.version_id == ref_id)
        ).scalar_one()

    lines = []
    for account_id, code, name, gcode, gname in meta:
        form = forms[account_id]
        profile = None
        match form.basis:
            case "even":
                basis = "even"
                reference: list[Decimal | None] = [None] * MONTHS
            case "profile":
                assert form.profile_id is not None
                pname, profile = profiles[form.profile_id]
                basis = f"profile {pname}"
                reference = [None] * MONTHS
            case "reference":
                basis = names[form.reference_version_id]
                reference = references[form.reference_version_id].get(account_id, [None] * MONTHS)
        lines.append(_Line(account_id, code, name, gcode, gname, form, basis, profile, reference))
    return lines


def read_grid(engine: Engine, version_code: str, cc_code: str) -> Grid:
    with engine.connect() as conn:
        ctx = _context(conn, version_code, cc_code)
        columns, compared = _comparisons(conn, ctx)
        plan = _values(conn, ctx.version_id, ctx.cost_center_id, ctx.periods)
        shown = set(plan) | _added(conn, ctx)
        for values in compared:
            shown |= set(values)
        lines = _lines(conn, ctx, shown - _hidden(conn, ctx.version_id))

    r = ctx.round
    grid = Grid(
        r.code,
        r.name,
        r.fiscal_year,
        r.status,
        r.deadline,
        cc_code,
        ctx.cost_center_name,
        ctx.status,
        ctx.lock_reason,
        ctx.periods,
        columns,
    )
    editable = ctx.lock_reason is None
    blank: list[Decimal | None] = [None] * MONTHS
    group_row: Row | None = None
    for line in lines:
        if group_row is None or group_row.code != line.group_code:
            group_row = Row(
                "group",
                line.group_code,
                line.group_name,
                line.group_code,
                [[None] * MONTHS for _ in columns],
                [Cell(None, None) for _ in range(MONTHS)],
            )
            grid.rows.append(group_row)
        w = weights(line.form.basis, MONTHS, line.profile, line.reference)
        row = Row(
            "account",
            line.code,
            line.name,
            line.group_code,
            [values.get(line.account_id, blank) for values in compared],
            plan[line.account_id],
            entry_form=line.form.form,
            basis=line.basis,
            fallback=w.fallback,
            months_visible=line.form.months_visible,
            months_editable=editable and line.form.months_editable,
            total_editable=editable,
        )
        grid.rows.append(row)
        for i, months in enumerate(row.comparisons):
            for m in range(MONTHS):
                group_row.comparisons[i][m] = _add(group_row.comparisons[i][m], months[m])
        for m in range(MONTHS):
            group_row.plan[m] = Cell(_add(group_row.plan[m].value, row.plan[m].value), None)
    return grid


def _add(a: Decimal | None, b: Decimal | None) -> Decimal | None:
    if a is None:
        return b
    return a if b is None else a + b


def _parse(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        amount = Decimal(value)
    except InvalidOperation:
        raise EditError(f"not a number: {value!r}") from None
    if not amount.is_finite():
        raise EditError(f"not a number: {value!r}")
    exponent = amount.as_tuple().exponent
    if isinstance(exponent, int) and exponent < -2:
        raise EditError(f"{value} has more than 2 decimal places")
    return amount


def _locked(e: DBAPIError) -> LockedError | None:
    if getattr(e.orig, "sqlstate", None) == LOCKED_SQLSTATE:
        return LockedError(str(e.orig).splitlines()[0].removeprefix("plan values are locked: "))
    return None


def _leaf_accounts(conn: Connection, codes: set[str]) -> dict[str, int]:
    accounts = dict(
        conn.execute(
            select(member.c.code, member.c.member_id).where(
                member.c.dim_id == ACCOUNT,
                member.c.is_group.is_(False),
                member.c.code.in_(codes),
            )
        ).all()
    )
    missing = codes - set(accounts)
    if missing:
        raise EditError(f"unknown account(s): {', '.join(sorted(missing))}")
    return accounts


def apply_edits(
    engine: Engine, actor: str, version_code: str, cc_code: str, edits: list[Edit]
) -> None:
    """Apply all edits in one transaction, or none: any conflict or invalid edit rolls back."""
    try:
        with unit_of_work(engine, actor) as conn:
            _apply(conn, version_code, cc_code, edits)
    except DBAPIError as e:
        locked = _locked(e)
        if locked is None:
            raise
        raise locked from None


def _apply(conn: Connection, version_code: str, cc_code: str, edits: list[Edit]) -> None:
    ctx = _context(conn, version_code, cc_code)
    if ctx.lock_reason is not None:
        raise LockedError(ctx.lock_reason)
    accounts = _leaf_accounts(conn, {e.account_code for e in edits})
    hidden = sorted(c for c, a in accounts.items() if a in _hidden(conn, ctx.version_id))
    if hidden:
        raise EditError(f"hidden in this round: {', '.join(hidden)}")
    lines = {line.account_id: line for line in _lines(conn, ctx, accounts.values())}

    f = fact_cost_center
    # Lock the cells this request may touch, then compare versions (D-19).
    locked = _values(conn, ctx.version_id, ctx.cost_center_id, ctx.periods, lock=True)
    current = {
        (a, ctx.periods[m]): c.row_version
        for a, cells in locked.items()
        if a in lines
        for m, c in enumerate(cells)
        if c.row_version is not None
    }

    conflicts: list[Conflict] = []
    writes: dict[tuple[int, date], Decimal | None] = {}
    for e in edits:
        if len(e.expected) != MONTHS:
            raise EditError("expected must list 12 row versions")
        line = lines[accounts[e.account_code]]
        if e.month is not None and not 0 <= e.month < MONTHS:
            raise EditError(f"month {e.month} out of range")
        if e.month is not None and not line.form.months_editable:
            raise EditError(f"{e.account_code} is entered as a year total")
        months = range(MONTHS) if e.month is None else [e.month]
        for m in months:
            key = (line.account_id, ctx.periods[m])
            if key not in writes and current.get(key) != e.expected[m]:
                conflicts.append(Conflict(e.account_code, m))
        value = _parse(e.value)
        if e.month is not None:
            writes[(line.account_id, ctx.periods[e.month])] = value
        elif value is None:
            for p in ctx.periods:
                writes[(line.account_id, p)] = None
        else:
            w = weights(line.form.basis, MONTHS, line.profile, line.reference)
            for p, amount in zip(ctx.periods, spread(value, w.weights), strict=True):
                writes[(line.account_id, p)] = amount
    if conflicts:
        raise ConflictError(conflicts)

    deletes = [k for k, v in writes.items() if v is None and k in current]
    if deletes:
        conn.execute(
            delete(f).where(
                f.c.version_id == ctx.version_id,
                f.c.layer_id == BASE_LAYER,
                f.c.measure_id == AMOUNT,
                f.c.dim_cost_center == ctx.cost_center_id,
                tuple_(f.c.dim_account, f.c.period).in_(deletes),
            )
        )
    upserts = [
        {
            "version_id": ctx.version_id,
            "layer_id": BASE_LAYER,
            "measure_id": AMOUNT,
            "period": p,
            "dim_entity": ctx.entity_id,
            "dim_cost_center": ctx.cost_center_id,
            "dim_account": a,
            "amount": v,
        }
        for (a, p), v in writes.items()
        if v is not None
    ]
    if upserts:
        stmt = pg_insert(f).values(upserts)
        conn.execute(
            stmt.on_conflict_do_update(
                index_elements=list(f.primary_key.columns),
                set_={"amount": stmt.excluded.amount, "row_version": f.c.row_version + 1},
            )
        )


def add_account(
    engine: Engine, actor: str, version_code: str, cc_code: str, account_code: str
) -> None:
    """Add an account to a cost center's sheet (ADR 0007); hidden accounts can't be added."""
    with unit_of_work(engine, actor) as conn:
        ctx = _context(conn, version_code, cc_code)
        if ctx.lock_reason is not None:
            raise LockedError(ctx.lock_reason)
        account_id = _leaf_accounts(conn, {account_code})[account_code]
        if account_id in _hidden(conn, ctx.version_id):
            raise EditError(f"{account_code} is hidden in this round")
        conn.execute(
            pg_insert(sheet_account)
            .values(
                version_id=ctx.version_id,
                module=MODULE_COST_CENTER,
                cost_center_id=ctx.cost_center_id,
                account_id=account_id,
                added_by=actor,
            )
            .on_conflict_do_nothing()
        )


def addable_accounts(engine: Engine, version_code: str) -> list[dict[str, object]]:
    """Leaf accounts a planner may add in the round: every account not hidden."""
    with engine.connect() as conn:
        r = get_round(conn, version_code)
        hidden = _hidden(conn, r.version_id)
        rows = conn.execute(
            select(member.c.member_id, member.c.code, member.c.name)
            .where(member.c.dim_id == ACCOUNT, member.c.is_group.is_(False))
            .order_by(member.c.code)
        ).all()
    return [{"code": c, "name": n} for m, c, n in rows if m not in hidden]


def planning_units(engine: Engine, version_code: str) -> list[dict[str, object]]:
    """Cost centers enabled in the round, with their status (T-25 switcher)."""
    with engine.connect() as conn:
        r = get_round(conn, version_code)
        enabled = [m for m, e in enabled_members(conn, r.version_id).items() if e]
        rows = conn.execute(
            select(
                member.c.member_id,
                member.c.code,
                member.c.name,
                member.c.entity_code,
                member.c.owner,
            )
            .where(member.c.member_id.in_(enabled))
            .order_by(member.c.code)
        ).all()
        return [
            {**row._mapping, "status": cast(str, status_of(conn, r.version_id, row.member_id))}
            for row in rows
        ]
