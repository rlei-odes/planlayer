"""The planning grid for one cost center in a planning round (T-25): read it, and apply edits.

Rows are the accounts with comparison values or plan values for the cost center, the accounts
the planner added, and the accounts of the cost center's account set (ADR 0007, ADR 0014). An
account not planned in the round, or outside the cost center's set, shows read only where it
has values, so every total includes it. Rows are grouped by their parent in the account
hierarchy, with group subtotals computed here (ADR 0003).

Each account row carries its entry form, which decides the editable cells and how a year total
is spread. Each plan cell carries its ``row_version``; an edit states the versions it was made
against, and a cell changed by someone else in the meantime is a conflict (D-19). Blank and
zero stay distinct: a blank cell has no row.

Whether the cost center can be edited at all (round open, or in setup for a controller; cost
center enabled; status not submitted or approved) is checked here for a clear message, and
enforced by the lock trigger.
"""

import uuid
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal, cast

from sqlalchemy import Connection, Engine, and_, delete, func, select, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError

from planlayer.access import User, cost_center_rights
from planlayer.domain.entry_forms import Basis, EntryForm, Form, resolve, weights
from planlayer.domain.periods import closed_months, quarter_months
from planlayer.domain.spreading import Profile, spread
from planlayer.domain.workflow import EDITABLE, RoundStatus, Status
from planlayer.rounds import (
    Grain,
    NotFoundError,
    RoundRef,
    account_scopes,
    fiscal_periods,
    get_round,
)
from planlayer.store.schema import (
    ACCOUNT,
    AMOUNT,
    BASE_LAYER,
    COST_CENTER,
    ENTITY,
    H_ACCOUNT,
    MODULE_CAPACITY,
    MODULE_COST_CENTER,
    change_log,
    closure,
    comparison,
    credit_account,
    entry_form,
    fact_cost_center,
    hier_edge,
    line_spread,
    member,
    not_planned_account,
    planning_round,
    round_member,
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
MONTH_NAME = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
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
    note: str | None = None  # the controller's note to planners
    # Actuals columns: the last month holding actuals, and whether months after it are missing,
    # as for the current year's actuals in a budget prepared mid-year.
    actuals_until: date | None = None
    partial: bool = False


@dataclass(frozen=True)
class LineSpread:
    """How one line spreads, chosen on the sheet: even, prior-year actuals (``reference``) or a
    profile."""

    basis: Basis
    profile_code: str | None = None


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
    spread: Basis | None = None  # the kind of basis, chosen on the sheet or by the entry form
    chosen_spread: LineSpread | None = None  # chosen on the sheet for this line
    reference_version: str | None = None  # a reference basis other than prior-year actuals
    profile: str | None = None  # the profile a profile basis spreads by
    months_visible: bool = True
    months_editable: bool = False
    total_editable: bool = False
    capacity: bool = False  # credited by capacity planning: read only (ADR 0013)
    # Why the line takes no plan values in this round (ADR 0014); None: it is planned.
    not_planned: Literal["round", "set"] | None = None


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
    version_kind: str = "budget"
    grain: Grain = "month"
    # A forecast's closed months (ADR 0015): the first ``closed_months`` plan cells hold actuals.
    actuals_through: date | None = None
    closed_months: int = 0


@dataclass
class Edit:
    account_code: str
    month: int | None  # 0-11; None with no quarter: the full year (spread by the line's basis)
    value: str | None  # canonical decimal string ("1234.5", "-3"); None = blank
    expected: list[int | None]  # row_version of each month as the client saw it
    quarter: int | None = None  # 0-3 in a round planned by quarter: spread over its months


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

    @property
    def closed(self) -> int:
        """How many months, from the year's first, are closed (ADR 0015)."""
        return closed_months(self.periods, self.round.actuals_through)

    @property
    def open_months(self) -> range:
        return range(self.closed, MONTHS)


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
    chosen: LineSpread | None
    reference_code: str | None  # the version a reference basis spreads like; None: actuals
    profile_code: str | None  # the profile a profile basis spreads by


def _context(conn: Connection, user: User, version_code: str, cc_code: str) -> _Context:
    r = get_round(conn, version_code)
    cc = conn.execute(
        select(member).where(
            member.c.dim_id == COST_CENTER, member.c.code == cc_code, member.c.is_group.is_(False)
        )
    ).first()
    rights = cost_center_rights(conn, user)
    # A cost center the user may not read is reported as not found, like a missing one.
    if cc is None or cc.member_id not in rights:
        raise NotFoundError(f"cost center {cc_code} not found")
    entity_id = conn.execute(
        select(member.c.member_id).where(member.c.dim_id == ENTITY, member.c.code == cc.entity_code)
    ).scalar_one()
    actuals_id = conn.execute(
        select(version.c.version_id).where(version.c.code == ACTUALS_CODE)
    ).scalar()
    status = status_of(conn, r.version_id, cc.member_id)
    enabled = enabled_members(conn, r.version_id).get(cc.member_id, False)
    if r.status != "open" and not (r.status == "setup" and user.is_controller):
        reason: str | None = f"the planning round is {r.status}"
    elif not enabled:
        reason = "the cost center is not enabled in this round"
    elif status not in EDITABLE:
        reason = f"the cost center is {status}"
    elif not rights[cc.member_id]:
        reason = "you may only read this cost center"
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


def _version_values(
    conn: Connection, ctx: _Context, version_id: int
) -> dict[int, list[Decimal | None]]:
    """Another version's values for the cost center over its round's year. A forecast's closed
    months are its actuals, so it reads as its full year: actuals to date plus forecast (ADR
    0015)."""
    found = conn.execute(
        select(planning_round.c.fiscal_year, planning_round.c.actuals_through).where(
            planning_round.c.version_id == version_id
        )
    ).first()
    if found is None:
        return _amounts(_values(conn, version_id, ctx.cost_center_id, ctx.periods))
    periods = fiscal_periods(conn, found.fiscal_year)
    values = _amounts(_values(conn, version_id, ctx.cost_center_id, periods))
    closed = closed_months(periods, found.actuals_through)
    if closed:
        actuals = _amounts(_values(conn, ctx.actuals_id, ctx.cost_center_id, periods[:closed]))
        for account in set(values) | set(actuals):
            months = values.get(account, [None] * MONTHS)
            values[account] = actuals.get(account, [None] * MONTHS)[:closed] + months[closed:]
    return values


def _comparisons(
    conn: Connection, ctx: _Context
) -> tuple[list[ComparisonColumn], list[dict[int, list[Decimal | None]]]]:
    columns, values = [], []
    for kind, other_id, other_name, note in conn.execute(
        select(
            comparison.c.kind, comparison.c.compare_version_id, version.c.name, comparison.c.note
        )
        .outerjoin(version, version.c.version_id == comparison.c.compare_version_id)
        .where(
            comparison.c.version_id == ctx.version_id,
            comparison.c.module == MODULE_COST_CENTER,
        )
        .order_by(comparison.c.position)
    ):
        match kind:
            case "actuals" if ctx.closed:
                # A forecast's actuals of the year stop at its closed months (ADR 0015).
                periods = ctx.periods[: ctx.closed]
                amounts = _amounts(_values(conn, ctx.actuals_id, ctx.cost_center_id, periods))
                column = ComparisonColumn(
                    kind,
                    f"YTD {_period_name(ctx)} {ctx.round.fiscal_year}",
                    note,
                    _actuals_until(conn, ctx.actuals_id, periods),
                    True,
                )
            case "prior_year_actuals" | "actuals":
                year = ctx.round.fiscal_year - (1 if kind == "prior_year_actuals" else 0)
                periods = ctx.prior_periods if kind == "prior_year_actuals" else ctx.periods
                amounts = _amounts(_values(conn, ctx.actuals_id, ctx.cost_center_id, periods))
                until = _actuals_until(conn, ctx.actuals_id, periods)
                column = ComparisonColumn(
                    kind, f"Actual {year}", note, until, until is not None and until < periods[-1]
                )
            case _:
                amounts = _version_values(conn, ctx, other_id)
                column = ComparisonColumn(kind, other_name, note)
        columns.append(column)
        values.append(amounts)
    return columns, values


def _period_name(ctx: _Context) -> str:
    """The last closed month or quarter, as in "YTD Jun" or "YTD Q2"."""
    if ctx.round.grain == "quarter":
        return f"Q{ctx.closed // 3}"
    return MONTH_NAME[ctx.periods[ctx.closed - 1].month - 1]


def _actuals_until(conn: Connection, actuals_id: int, periods: list[date]) -> date | None:
    """The last of ``periods`` holding actuals on any cost center the user sees: actuals are
    synced up to a month, so the months after it are missing, not zero."""
    f = fact_cost_center
    last: date | None = conn.execute(
        select(func.max(f.c.period)).where(
            f.c.version_id == actuals_id,
            f.c.layer_id == BASE_LAYER,
            f.c.measure_id == AMOUNT,
            f.c.period.between(periods[0], periods[-1]),
        )
    ).scalar()
    return last


def _not_planned(conn: Connection, version_id: int) -> set[int]:
    return set(
        conn.execute(
            select(not_planned_account.c.account_id).where(
                not_planned_account.c.version_id == version_id,
                not_planned_account.c.module == MODULE_COST_CENTER,
            )
        ).scalars()
    )


@dataclass
class _Scope:
    """Which accounts the cost center plans in the round (ADR 0014)."""

    not_planned: set[int]  # not planned in the round
    account_set: set[int] | None  # the leaves of its account set; None: every account
    credited: set[int]  # written by capacity planning, outside any set

    def why_not(self, account_id: int) -> Literal["round", "set"] | None:
        if account_id in self.not_planned:
            return "round"
        if (
            self.account_set is not None
            and account_id not in self.account_set
            and account_id not in self.credited
        ):
            return "set"
        return None

    def offered(self, account_id: int) -> bool:
        """Whether a planner may add the account to the sheet."""
        return self.why_not(account_id) is None and account_id not in self.credited


def _scope(conn: Connection, ctx: _Context) -> _Scope:
    return _Scope(
        _not_planned(conn, ctx.version_id),
        account_scopes(conn, ctx.version_id, [ctx.cost_center_id])[ctx.cost_center_id],
        _credited(conn, ctx),
    )


_WHY = {"round": "not planned in this round", "set": "not in this cost center's accounts"}


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
    # A spread chosen on the sheet replaces the form's basis; year-only lines keep theirs.
    chosen: dict[int, LineSpread] = {}
    for account_id, basis, profile_id, profile_code in conn.execute(
        select(
            line_spread.c.account_id,
            line_spread.c.basis,
            line_spread.c.profile_id,
            seasonality_profile.c.code,
        )
        .outerjoin(
            seasonality_profile, seasonality_profile.c.profile_id == line_spread.c.profile_id
        )
        .where(
            line_spread.c.version_id == ctx.version_id,
            line_spread.c.module == MODULE_COST_CENTER,
            line_spread.c.cost_center_id == ctx.cost_center_id,
            line_spread.c.account_id.in_(ids),
        )
    ):
        if forms[account_id].spreads:
            chosen[account_id] = LineSpread(basis, profile_code)
            forms[account_id] = forms[account_id].with_spread(basis, profile_id)

    profile_ids = {f.profile_id for f in forms.values() if f.profile_id is not None}
    profiles = {
        p.profile_id: (p.name, p.code, Profile(tuple(p.values), p.entered_as))
        for p in conn.execute(
            select(seasonality_profile).where(seasonality_profile.c.profile_id.in_(profile_ids))
        )
    }
    prior_actuals = _amounts(_values(conn, ctx.actuals_id, ctx.cost_center_id, ctx.prior_periods))
    references: dict[int | None, dict[int, list[Decimal | None]]] = {None: prior_actuals}
    names: dict[int | None, str] = {None: "prior-year actuals"}
    codes: dict[int | None, str | None] = {None: None}
    for ref_id in {f.reference_version_id for f in forms.values()} - {None}:
        assert ref_id is not None
        references[ref_id] = _version_values(conn, ctx, ref_id)
        names[ref_id], codes[ref_id] = conn.execute(
            select(version.c.name, version.c.code).where(version.c.version_id == ref_id)
        ).one()

    lines = []
    for account_id, code, name, gcode, gname in meta:
        form = forms[account_id]
        profile = None
        profile_code = None
        match form.basis:
            case "even":
                basis = "even"
                reference: list[Decimal | None] = [None] * MONTHS
            case "profile":
                assert form.profile_id is not None
                pname, profile_code, profile = profiles[form.profile_id]
                basis = f"profile {pname}"
                reference = [None] * MONTHS
            case "reference":
                basis = names[form.reference_version_id]
                reference = references[form.reference_version_id].get(account_id, [None] * MONTHS)
        lines.append(
            _Line(
                account_id,
                code,
                name,
                gcode,
                gname,
                form,
                basis,
                profile,
                reference,
                chosen.get(account_id),
                codes[form.reference_version_id] if form.basis == "reference" else None,
                profile_code,
            )
        )
    return lines


def read_grid(engine: Engine, user: User, version_code: str, cc_code: str) -> Grid:
    with unit_of_work(engine, user) as conn:
        ctx = _context(conn, user, version_code, cc_code)
        columns, compared = _comparisons(conn, ctx)
        plan = _values(conn, ctx.version_id, ctx.cost_center_id, ctx.periods)
        # A forecast's closed months show actuals in the plan cells, read only (ADR 0015).
        closed = ctx.periods[: ctx.closed]
        actual = _values(conn, ctx.actuals_id, ctx.cost_center_id, closed) if closed else {}
        for account, booked in actual.items():
            for m in range(ctx.closed):
                plan[account][m] = Cell(booked[m].value, None)
        scope = _scope(conn, ctx)
        # Accounts with values always show; added and set accounts only while planned.
        listed = _added(conn, ctx) | (scope.account_set or set())
        shown = set(plan) | {a for a in listed if scope.why_not(a) is None}
        for values in compared:
            shown |= set(values)
        lines = _lines(conn, ctx, shown)
        credited = scope.credited

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
        version_kind=r.kind,
        grain=r.grain,
        actuals_through=r.actuals_through,
        closed_months=ctx.closed,
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
        w = weights(line.form.basis, MONTHS, line.profile, line.reference, ctx.open_months)
        from_capacity = line.account_id in credited
        why_not = scope.why_not(line.account_id)
        planned = why_not is None
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
            spread=line.form.basis,
            chosen_spread=line.chosen,
            reference_version=line.reference_code,
            profile=line.profile_code,
            months_visible=line.form.months_visible or from_capacity,
            months_editable=editable
            and planned
            and line.form.months_editable
            and not from_capacity,
            total_editable=editable and planned and not from_capacity,
            capacity=from_capacity,
            not_planned=why_not,
        )
        grid.rows.append(row)
        for i, months in enumerate(row.comparisons):
            for m in range(MONTHS):
                group_row.comparisons[i][m] = _add(group_row.comparisons[i][m], months[m])
        for m in range(MONTHS):
            group_row.plan[m] = Cell(_add(group_row.plan[m].value, row.plan[m].value), None)
    return grid


def _credited(conn: Connection, ctx: _Context) -> set[int]:
    """Accounts credited by activity types, if the cost center is productive in the round: its
    capacity planning writes them (ADR 0013)."""
    productive = conn.execute(
        select(round_member.c.member_id).where(
            round_member.c.version_id == ctx.version_id,
            round_member.c.module == MODULE_CAPACITY,
            round_member.c.member_id == ctx.cost_center_id,
            round_member.c.enabled.is_(True),
        )
    ).first()
    if productive is None:
        return set()
    return set(conn.execute(select(credit_account.c.account_id)).scalars())


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
    engine: Engine, actor: User, version_code: str, cc_code: str, edits: list[Edit]
) -> str:
    """Apply all edits in one transaction, or none: any conflict or invalid edit rolls back.
    Returns the change batch the edits were written as, which undo reverts."""
    batch_id = uuid.uuid4().hex
    try:
        with unit_of_work(engine, actor, batch_id) as conn:
            _apply(conn, actor, version_code, cc_code, edits)
    except DBAPIError as e:
        locked = _locked(e)
        if locked is None:
            raise
        raise locked from None
    return batch_id


def undo_batch(engine: Engine, actor: User, version_code: str, cc_code: str, batch_id: str) -> str:
    """Revert one of the actor's change batches on a cost center (E-05): every cell it wrote gets
    its old value back, blank where it had none, as a new batch in the change log. Refused as a
    conflict, with nothing written, if any of those cells changed since. Returns the new batch, so
    undoing that one redoes the change.

    The cells are written as they were stored, whatever the line's entry form now allows: undo
    restores a state, it doesn't enter values."""
    undo_id = uuid.uuid4().hex
    log = change_log
    try:
        with unit_of_work(engine, actor, undo_id) as conn:
            ctx = _context(conn, actor, version_code, cc_code)
            if ctx.lock_reason is not None:
                raise LockedError(ctx.lock_reason)
            logged = conn.execute(
                select(log.c.dim_account, log.c.period, log.c.old_amount, log.c.new_amount)
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
            locked = _values(conn, ctx.version_id, ctx.cost_center_id, ctx.periods, lock=True)
            current = {
                (a, ctx.periods[m]): c.row_version
                for a, cells in locked.items()
                for m, c in enumerate(cells)
                if c.row_version is not None
            }
            # A cell gets the value it had before the batch, and must still hold the batch's last.
            before: dict[tuple[int, date], Decimal | None] = {}
            after: dict[tuple[int, date], Decimal | None] = {}
            for account_id, period, old, new in logged:
                before.setdefault((account_id, period), old)
                after[(account_id, period)] = new
            blank = [Cell(None, None)] * MONTHS
            changed = sorted(
                (a, ctx.periods.index(p))
                for (a, p), new in after.items()
                if locked.get(a, blank)[ctx.periods.index(p)].value != new
            )
            if changed:
                codes = dict(
                    conn.execute(
                        select(member.c.member_id, member.c.code).where(
                            member.c.member_id.in_({a for a, _ in changed})
                        )
                    ).all()
                )
                raise ConflictError([Conflict(codes[a], m) for a, m in changed])
            _write(conn, ctx, before, current)
    except DBAPIError as e:
        locked_error = _locked(e)
        if locked_error is None:
            raise
        raise locked_error from None
    return undo_id


def _apply(
    conn: Connection, actor: User, version_code: str, cc_code: str, edits: list[Edit]
) -> None:
    ctx = _context(conn, actor, version_code, cc_code)
    if ctx.lock_reason is not None:
        raise LockedError(ctx.lock_reason)
    accounts = _leaf_accounts(conn, {e.account_code for e in edits})
    scope = _scope(conn, ctx)
    for why in ("round", "set"):
        refused = sorted(c for c, a in accounts.items() if scope.why_not(a) == why)
        if refused:
            raise EditError(f"{_WHY[why]}: {', '.join(refused)}")
    credited = sorted(c for c, a in accounts.items() if a in scope.credited)
    if credited:
        raise EditError(f"credited by capacity planning: {', '.join(credited)}")
    lines = {line.account_id: line for line in _lines(conn, ctx, accounts.values())}

    # Lock the cells this request may touch, then compare versions (D-19).
    locked = _values(conn, ctx.version_id, ctx.cost_center_id, ctx.periods, lock=True)
    current = {
        (a, ctx.periods[m]): c.row_version
        for a, cells in locked.items()
        if a in lines
        for m, c in enumerate(cells)
        if c.row_version is not None
    }

    # A year total typed in a forecast is the full year: the open months take what the closed
    # months' actuals leave (ADR 0015).
    to_date: dict[int, Decimal] = defaultdict(Decimal)
    if ctx.closed:
        for account, cells in _values(
            conn, ctx.actuals_id, ctx.cost_center_id, ctx.periods[: ctx.closed]
        ).items():
            to_date[account] = sum((c.value for c in cells if c.value is not None), Decimal(0))

    conflicts: list[Conflict] = []
    writes: dict[tuple[int, date], Decimal | None] = {}
    for e in edits:
        if len(e.expected) != MONTHS:
            raise EditError("expected must list 12 row versions")
        line = lines[accounts[e.account_code]]
        months = _targets(ctx, line, e)
        for m in months:
            key = (line.account_id, ctx.periods[m])
            if key not in writes and current.get(key) != e.expected[m]:
                conflicts.append(Conflict(e.account_code, m))
        value = _parse(e.value)
        if value is None or e.month is not None:
            for m in months:
                writes[(line.account_id, ctx.periods[m])] = value
            continue
        if e.month is None and e.quarter is None:
            value -= to_date[line.account_id]
        w = weights(line.form.basis, MONTHS, line.profile, line.reference, months)
        for m, amount in zip(months, spread(value, w.weights), strict=True):
            writes[(line.account_id, ctx.periods[m])] = amount
    if conflicts:
        raise ConflictError(conflicts)
    _write(conn, ctx, writes, current)


def _targets(ctx: _Context, line: _Line, e: Edit) -> list[int]:
    """The months an edit writes: one month, a quarter's three, or the year's open months. A
    round planned by quarter takes quarters, not single months (ADR 0015)."""
    grain = ctx.round.grain
    if e.month is not None and e.quarter is not None:
        raise EditError("an edit names a month or a quarter, not both")
    if e.month is not None:
        if not 0 <= e.month < MONTHS:
            raise EditError(f"month {e.month} out of range")
        if grain == "quarter":
            raise EditError("this round is planned by quarter")
        months = [e.month]
    elif e.quarter is not None:
        if not 0 <= e.quarter < 4:
            raise EditError(f"quarter {e.quarter} out of range")
        if grain != "quarter":
            raise EditError("this round is planned by month")
        months = list(quarter_months(e.quarter))
    else:
        return list(ctx.open_months)
    if not line.form.months_editable:
        raise EditError(f"{e.account_code} is entered as a year total")
    if months[0] < ctx.closed:
        raise EditError(
            f"{MONTH_NAME[ctx.periods[months[0]].month - 1]} {ctx.periods[months[0]].year}"
            " is closed: it shows actuals"
        )
    return months


def _write(
    conn: Connection,
    ctx: _Context,
    writes: dict[tuple[int, date], Decimal | None],
    current: dict[tuple[int, date], int],
) -> None:
    """Write cells: blank deletes the row, a value inserts it or updates it with the next
    ``row_version``. ``current`` holds the cells that have a row now."""
    f = fact_cost_center
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
    engine: Engine, actor: User, version_code: str, cc_code: str, account_code: str
) -> None:
    """Add an account to a cost center's sheet (ADR 0007): one the cost center plans."""
    with unit_of_work(engine, actor) as conn:
        ctx = _context(conn, actor, version_code, cc_code)
        if ctx.lock_reason is not None:
            raise LockedError(ctx.lock_reason)
        account_id = _leaf_accounts(conn, {account_code})[account_code]
        why = _scope(conn, ctx).why_not(account_id)
        if why is not None:
            raise EditError(f"{account_code} is {_WHY[why]}")
        conn.execute(
            pg_insert(sheet_account)
            .values(
                version_id=ctx.version_id,
                module=MODULE_COST_CENTER,
                cost_center_id=ctx.cost_center_id,
                account_id=account_id,
                added_by=actor.login,
            )
            .on_conflict_do_nothing()
        )


def set_line_spread(
    engine: Engine,
    actor: User,
    version_code: str,
    cc_code: str,
    account_code: str,
    chosen: LineSpread | None,
) -> None:
    """Choose how one line of a cost center spreads its year totals from now on, or (None) go
    back to the entry form's basis. Months already stored keep their values (ADR 0007)."""
    try:
        with unit_of_work(engine, actor) as conn:
            ctx = _context(conn, actor, version_code, cc_code)
            if ctx.lock_reason is not None:
                raise LockedError(ctx.lock_reason)
            account_id = _leaf_accounts(conn, {account_code})[account_code]
            why = _scope(conn, ctx).why_not(account_id)
            if why is not None:
                raise EditError(f"{account_code} is {_WHY[why]}")
            (line,) = _lines(conn, ctx, [account_id])
            if not line.form.spreads:
                raise EditError(f"{account_code} is entered as a year total only")
            key = {
                "version_id": ctx.version_id,
                "module": MODULE_COST_CENTER,
                "cost_center_id": ctx.cost_center_id,
                "account_id": account_id,
            }
            if chosen is None:
                conn.execute(
                    delete(line_spread).where(*(line_spread.c[k] == v for k, v in key.items()))
                )
                return
            profile_id = None
            if chosen.basis == "profile":
                profile_id = conn.execute(
                    select(seasonality_profile.c.profile_id).where(
                        seasonality_profile.c.code == chosen.profile_code
                    )
                ).scalar()
                if profile_id is None:
                    raise NotFoundError(f"seasonality profile {chosen.profile_code} not found")
            elif chosen.profile_code is not None:
                raise EditError("only a profile basis names a profile")
            values = {"basis": chosen.basis, "profile_id": profile_id, "changed_by": actor.login}
            stmt = pg_insert(line_spread).values(**key, **values)
            conn.execute(
                stmt.on_conflict_do_update(
                    index_elements=list(line_spread.primary_key.columns),
                    set_={**values, "changed_at": func.now()},
                )
            )
    except DBAPIError as e:
        locked = _locked(e)
        if locked is None:
            raise
        raise locked from None


def addable_accounts(
    engine: Engine, user: User, version_code: str, cc_code: str
) -> list[dict[str, object]]:
    """Leaf accounts a planner may add to the cost center's sheet: those it plans, i.e. not
    excluded from the round, inside its account set and not credited by capacity planning."""
    with unit_of_work(engine, user) as conn:
        ctx = _context(conn, user, version_code, cc_code)
        scope = _scope(conn, ctx)
        rows = conn.execute(
            select(member.c.member_id, member.c.code, member.c.name)
            .where(member.c.dim_id == ACCOUNT, member.c.is_group.is_(False))
            .order_by(member.c.code)
        ).all()
    return [{"code": c, "name": n} for m, c, n in rows if scope.offered(m)]


def planning_units(engine: Engine, user: User, version_code: str) -> list[dict[str, object]]:
    """Cost centers enabled in the round that the user may read, with their status and whether
    the user may write them (T-25 switcher)."""
    with unit_of_work(engine, user) as conn:
        r = get_round(conn, version_code)
        rights = cost_center_rights(conn, user)
        enabled = [m for m, e in enabled_members(conn, r.version_id).items() if e and m in rights]
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
            {
                **row._mapping,
                "status": cast(str, status_of(conn, r.version_id, row.member_id)),
                "can_write": rights[row.member_id],
            }
            for row in rows
        ]
