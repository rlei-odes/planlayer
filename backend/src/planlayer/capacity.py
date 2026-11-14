"""Capacity planning (ADR 0013): the capacity sheet of a productive cost center, planned hours,
plan rates, and the credits they give the cost center.

A sheet has one line per work center x activity type: the cost center's work centers with their
source rates, plus lines kept with the round (``capacity_line``). A line enters the round the
first time something is written to it, with the source rate as its plan rate. Every change of
hours, a rate or a line's state recomputes the cost center's credits (hours x rate per activity
type and month, on the activity type's account) in the same transaction, so they share the
request's change batch.
"""

import uuid
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from sqlalchemy import ColumnElement, Connection, Engine, and_, delete, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError

from planlayer.access import User, capacity_rights
from planlayer.domain.entry_forms import Basis, weights
from planlayer.domain.periods import QUARTERS, closed_months, quarter_months
from planlayer.domain.spreading import Profile, spread
from planlayer.domain.workflow import EDITABLE, RoundStatus, Status
from planlayer.notes import Marker
from planlayer.notes import markers as note_markers
from planlayer.planning import LineSpread, comparison_columns, cost_values
from planlayer.rounds import Grain, NotFoundError, RoundRef, fiscal_periods, get_round
from planlayer.sheet import (
    MONTHS,
    Cell,
    ComparisonColumn,
    Conflict,
    ConflictError,
    EditError,
    LockedError,
    SheetContext,
    locked_error,
    parse_amount,
)
from planlayer.store.bulk import insert_all
from planlayer.store.schema import (
    ACCOUNT,
    ACTIVITY_TYPE,
    AMOUNT,
    BASE_LAYER,
    COST_CENTER,
    ENTITY,
    HOURS,
    MODULE_CAPACITY,
    MODULE_COST_CENTER,
    NO_ENTITY,
    WORK_CENTER,
    capacity_change_log,
    capacity_line,
    capacity_line_log,
    comparison,
    credit_account,
    fact_capacity,
    fact_cost_center,
    member,
    planning_round,
    round_member,
    seasonality_profile,
    version,
    work_center_rate,
)
from planlayer.store.uow import unit_of_work
from planlayer.sync import ACTUALS_CODE
from planlayer.workflow import enabled_members, status_of

CENT = Decimal("0.01")


@dataclass
class CapacityRow:
    kind: Literal["group", "line", "credit"]
    activity_type_code: str
    activity_type_name: str
    plan: list[Cell]
    # One list of 12 months per comparison column: hours, or amounts on a credit row.
    comparisons: list[list[Decimal | None]]
    # Lines:
    work_center_code: str | None = None
    work_center_name: str | None = None
    added: bool = False  # a work center added by hand, not from the source
    rate: Decimal | None = None
    basis: str | None = None  # what a year total spreads by, e.g. "prior-year actual hours"
    spread: Basis | None = None
    chosen_spread: LineSpread | None = None
    profile: str | None = None
    fallback: bool = False
    editable: bool = False
    # Credit rows: the account credited, and whether the activity type names none.
    account_code: str | None = None
    account_name: str | None = None
    notes: Marker | None = None  # lines: their notes (ADR 0017)


@dataclass
class InactiveLine:
    work_center_code: str
    work_center_name: str
    activity_type_code: str
    activity_type_name: str


@dataclass
class CapacitySheet:
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
    comparisons: list[ComparisonColumn] = field(default_factory=list)
    rows: list[CapacityRow] = field(default_factory=list)
    inactive: list[InactiveLine] = field(default_factory=list)
    version_kind: str = "budget"
    grain: Grain = "month"
    # A forecast's closed months (ADR 0015): the first ``closed_months`` cells hold actuals.
    actuals_through: date | None = None
    closed_months: int = 0
    # For adding a line: the cost center's work centers and every activity type.
    work_centers: list[tuple[str, str]] = field(default_factory=list)
    activity_types: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class HoursEdit:
    work_center_code: str
    activity_type_code: str
    month: int | None  # 0-11; None with no quarter: the year total (spread by the line's basis)
    value: str | None  # None = blank
    expected: list[int | None]  # row_version of each month as the client saw it
    quarter: int | None = None  # 0-3 in a round planned by quarter (ADR 0016)


@dataclass
class _Context:
    round: RoundRef
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
        """A forecast's closed months (ADR 0015): capacity writes only the months after them."""
        return closed_months(self.periods, self.round.actuals_through)

    @property
    def open_months(self) -> list[int]:
        return list(range(self.closed, MONTHS))


@dataclass
class _Line:
    work_center_id: int
    activity_type_id: int
    rate: Decimal
    active: bool
    basis: Basis | None  # chosen on the line; None: like prior-year actual hours
    profile_id: int | None
    stored: bool  # kept with the round in capacity_line
    added: bool = False  # a work center entered by hand: no history, so it spreads evenly

    @property
    def default_basis(self) -> Basis:
        """How a year total spreads unless the line chooses: like last year's hours, or evenly
        for a work center added by hand (e.g. a machine still to be bought)."""
        return "even" if self.added else "reference"


def productive(conn: Connection, version_id: int) -> set[int]:
    return set(
        conn.execute(
            select(round_member.c.member_id).where(
                round_member.c.version_id == version_id,
                round_member.c.module == MODULE_CAPACITY,
                round_member.c.enabled.is_(True),
            )
        ).scalars()
    )


def _context(conn: Connection, user: User, version_code: str, cc_code: str) -> _Context:
    r = get_round(conn, version_code)
    cc = conn.execute(
        select(member).where(
            member.c.dim_id == COST_CENTER, member.c.code == cc_code, member.c.is_group.is_(False)
        )
    ).first()
    rights = capacity_rights(conn, user)
    if cc is None or cc.member_id not in rights:
        raise NotFoundError(f"cost center {cc_code} not found")
    if cc.member_id not in productive(conn, r.version_id):
        raise NotFoundError(f"{cc_code} is not productive in this round")
    entity_id = conn.execute(
        select(member.c.member_id).where(
            member.c.dim_id == ENTITY, member.c.code == (cc.entity_code or NO_ENTITY)
        )
    ).scalar_one()
    actuals_id = conn.execute(
        select(version.c.version_id).where(version.c.code == ACTUALS_CODE)
    ).scalar()
    status = status_of(conn, r.version_id, cc.member_id)
    # Controllers prepare a round in setup (ADR 0014, ADR 0016).
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
        actuals_id=actuals_id if actuals_id is not None else -1,
        cost_center_id=cc.member_id,
        cost_center_code=cc.code,
        cost_center_name=cc.name,
        entity_id=entity_id,
        status=status,
        lock_reason=reason,
        periods=fiscal_periods(conn, r.fiscal_year),
        prior_periods=fiscal_periods(conn, r.fiscal_year - 1),
    )


def _lines(conn: Connection, ctx: _Context) -> dict[tuple[int, int], _Line]:
    """The cost center's lines: kept with the round, then its work centers' source rates."""
    lines = {
        (r.work_center_id, r.activity_type_id): _Line(
            r.work_center_id,
            r.activity_type_id,
            r.rate,
            r.active,
            r.basis,
            r.profile_id,
            True,
        )
        for r in conn.execute(
            select(capacity_line).where(
                capacity_line.c.version_id == ctx.version_id,
                capacity_line.c.cost_center_id == ctx.cost_center_id,
            )
        )
    }
    for wc, at, rate in conn.execute(
        select(
            work_center_rate.c.work_center_id,
            work_center_rate.c.activity_type_id,
            work_center_rate.c.rate,
        )
        .join(member, member.c.member_id == work_center_rate.c.work_center_id)
        .where(member.c.cost_center_code == ctx.cost_center_code)
    ):
        lines.setdefault((wc, at), _Line(wc, at, rate, True, None, None, False))
    manual = set(
        conn.execute(
            select(member.c.member_id).where(
                member.c.member_id.in_({wc for wc, _ in lines}), member.c.origin == "manual"
            )
        ).scalars()
    )
    for line in lines.values():
        line.added = line.work_center_id in manual
    return lines


def _hours(
    conn: Connection, version_id: int, cc_id: int | None, periods: list[date], *, lock: bool = False
) -> dict[tuple[int, int], list[Cell]]:
    """Hours per work center x activity type over ``periods``; all cost centers when ``cc_id``
    is None (actual hours of a work center that moved)."""
    f = fact_capacity
    query = select(
        f.c.dim_work_center, f.c.dim_activity_type, f.c.period, f.c.amount, f.c.row_version
    ).where(
        f.c.version_id == version_id,
        f.c.layer_id == BASE_LAYER,
        f.c.measure_id == HOURS,
        f.c.period.in_(periods),
    )
    if cc_id is not None:
        query = query.where(f.c.dim_cost_center == cc_id)
    if lock:
        query = query.with_for_update()
    index = {p: i for i, p in enumerate(periods)}
    out: dict[tuple[int, int], list[Cell]] = defaultdict(lambda: [Cell(None, None)] * MONTHS)
    for wc, at, p, amount, rv in conn.execute(query):
        row = list(out[(wc, at)])
        current = row[index[p]].value
        row[index[p]] = Cell(amount if current is None else current + amount, rv)
        out[(wc, at)] = row
    return dict(out)


def _profiles(conn: Connection, lines: Iterable[_Line]) -> dict[int, tuple[str, str, Profile]]:
    """The seasonality profiles the lines spread by: code, name and profile."""
    ids = {ln.profile_id for ln in lines if ln.profile_id is not None}
    return {
        p.profile_id: (p.code, p.name, Profile(tuple(p.values), p.entered_as))
        for p in conn.execute(
            select(seasonality_profile).where(seasonality_profile.c.profile_id.in_(ids))
        )
    }


def _spread_of(
    line: _Line, profiles: dict[int, tuple[str, str, Profile]], prior: list[Decimal | None]
) -> tuple[Basis, str, Profile | None]:
    basis: Basis = line.basis or line.default_basis
    if basis == "profile":
        assert line.profile_id is not None
        _, name, profile = profiles[line.profile_id]
        return basis, f"profile {name}", profile
    return basis, "even" if basis == "even" else "prior-year actual hours", None


def read_sheet(engine: Engine, user: User, version_code: str, cc_code: str) -> CapacitySheet:
    with unit_of_work(engine, user) as conn:
        ctx = _context(conn, user, version_code, cc_code)
        lines = _lines(conn, ctx)
        plan = _hours(conn, ctx.version_id, ctx.cost_center_id, ctx.periods)
        prior = _hours(conn, ctx.actuals_id, None, ctx.prior_periods)
        # A forecast's closed months show the actual hours, read only (ADR 0016).
        if ctx.closed:
            booked = _hours(conn, ctx.actuals_id, None, ctx.periods[: ctx.closed])
            for key, cells in booked.items():
                row = list(plan.get(key, [Cell(None, None)] * MONTHS))
                row[: ctx.closed] = [Cell(c.value, None) for c in cells[: ctx.closed]]
                plan[key] = row
        columns, amounts = comparison_columns(conn, _sheet_context(ctx))
        compared = _hour_comparisons(conn, ctx)
        ids = {wc for wc, _ in lines} | {at for _, at in lines}
        meta = {
            r.member_id: r
            for r in conn.execute(
                select(
                    member.c.member_id,
                    member.c.code,
                    member.c.name,
                    member.c.origin,
                    member.c.account_code,
                ).where(member.c.member_id.in_(ids))
            )
        }
        accounts = dict(
            conn.execute(
                select(member.c.code, member.c.name).where(
                    member.c.dim_id == ACCOUNT,
                    member.c.code.in_({meta[at].account_code for _, at in lines} - {None}),
                )
            ).all()
        )
        credits = _stored_credits(conn, ctx)
        account_ids = dict(
            conn.execute(
                select(member.c.code, member.c.member_id).where(
                    member.c.dim_id == ACCOUNT, member.c.code.in_(set(accounts))
                )
            ).all()
        )
        if ctx.closed:  # a credit's closed months are the cost center's actuals on its account
            actual = cost_values(
                conn, ctx.actuals_id, ctx.cost_center_id, ctx.periods[: ctx.closed]
            )
            for code, account_id in account_ids.items():
                months = list(credits.get(code, [None] * MONTHS))
                booked_credit = actual.get(account_id)
                months[: ctx.closed] = (
                    [c.value for c in booked_credit[: ctx.closed]]
                    if booked_credit
                    else [None] * ctx.closed
                )
                credits[code] = months
        profiles = _profiles(conn, lines.values())
        profile_codes = {pid: code for pid, (code, _, _) in profiles.items()}
        options = _options(conn, ctx)
        note_marks = note_markers(conn, user, ctx.round, "capacity", ctx.cost_center_id)

    r = ctx.round
    sheet = CapacitySheet(
        r.code,
        r.name,
        r.fiscal_year,
        r.status,
        r.deadline,
        ctx.cost_center_code,
        ctx.cost_center_name,
        ctx.status,
        ctx.lock_reason,
        ctx.periods,
        columns,
        work_centers=options[0],
        activity_types=options[1],
        version_kind=r.kind,
        grain=r.grain,
        actuals_through=r.actuals_through,
        closed_months=ctx.closed,
    )
    editable = ctx.lock_reason is None
    blank = [Cell(None, None)] * MONTHS
    none: list[Decimal | None] = [None] * MONTHS
    by_type: dict[int, list[_Line]] = defaultdict(list)
    for line in lines.values():
        if line.active:
            by_type[line.activity_type_id].append(line)
        else:
            sheet.inactive.append(
                InactiveLine(
                    meta[line.work_center_id].code,
                    meta[line.work_center_id].name,
                    meta[line.activity_type_id].code,
                    meta[line.activity_type_id].name,
                )
            )
    for at in sorted(by_type, key=lambda a: meta[a].code):
        t = meta[at]
        group = CapacityRow(
            "group", t.code, t.name, [Cell(None, None)] * MONTHS, [none for _ in columns]
        )
        sheet.rows.append(group)
        for line in sorted(by_type[at], key=lambda ln: meta[ln.work_center_id].code):
            w = meta[line.work_center_id]
            hours = plan.get((line.work_center_id, at), blank)
            before = [c.value for c in prior.get((line.work_center_id, at), blank)]
            basis, label, profile = _spread_of(line, profiles, before)
            spreading = weights(basis, MONTHS, profile, before, ctx.open_months)
            mine = [hours_of.get((line.work_center_id, at), none) for hours_of in compared]
            sheet.rows.append(
                CapacityRow(
                    "line",
                    t.code,
                    t.name,
                    hours,
                    mine,
                    work_center_code=w.code,
                    work_center_name=w.name,
                    added=w.origin == "manual",
                    rate=line.rate,
                    basis=label,
                    spread=basis,
                    chosen_spread=None
                    if line.basis is None
                    else LineSpread(line.basis, _code(profile_codes, line.profile_id)),
                    profile=_code(profile_codes, line.profile_id),
                    fallback=spreading.fallback,
                    editable=editable,
                    notes=note_marks.get((None, line.work_center_id, at)),
                )
            )
            group.plan = [
                Cell(_add(g.value, h.value), None) for g, h in zip(group.plan, hours, strict=True)
            ]
            group.comparisons = [
                [_add(g, h) for g, h in zip(gm, lm, strict=True)]
                for gm, lm in zip(group.comparisons, mine, strict=True)
            ]
        account = t.account_code
        account_id = account_ids.get(account) if account else None
        sheet.rows.append(
            CapacityRow(
                "credit",
                t.code,
                t.name,
                [Cell(v, None) for v in credits.get(account or "", [None] * MONTHS)],
                [v.get(account_id, none) if account_id else none for v in amounts],
                account_code=account,
                account_name=accounts.get(account) if account else None,
            )
        )
    return sheet


def _sheet_context(ctx: _Context) -> SheetContext:
    """The cost center sheet's context for the same cost center, for its comparison columns."""
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


Hours = dict[tuple[int, int], list[Decimal | None]]


def _hour_comparisons(conn: Connection, ctx: _Context) -> list[Hours]:
    """The round's comparison columns in hours (ADR 0016), per work center x activity type."""

    def amounts(cells: dict[tuple[int, int], list[Cell]]) -> Hours:
        return {k: [c.value for c in v] for k, v in cells.items()}

    out: list[Hours] = []
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
                out.append(amounts(_hours(conn, ctx.actuals_id, None, ctx.prior_periods)))
            case "actuals":
                # A forecast's actuals of the year stop at its closed months (ADR 0015).
                periods = ctx.periods[: ctx.closed] if ctx.closed else ctx.periods
                out.append(amounts(_hours(conn, ctx.actuals_id, None, periods)))
            case _:
                out.append(_version_hours(conn, ctx, other_id))
    return out


def _version_hours(conn: Connection, ctx: _Context, version_id: int) -> Hours:
    """Another round's planned hours for the cost center; a forecast as its full year."""
    found = conn.execute(
        select(planning_round.c.fiscal_year, planning_round.c.actuals_through).where(
            planning_round.c.version_id == version_id
        )
    ).first()
    periods = ctx.periods if found is None else fiscal_periods(conn, found.fiscal_year)
    planned = _hours(conn, version_id, ctx.cost_center_id, periods)
    values = {k: [c.value for c in v] for k, v in planned.items()}
    closed = 0 if found is None else closed_months(periods, found.actuals_through)
    if closed:
        booked = _hours(conn, ctx.actuals_id, None, periods[:closed])
        for key in set(values) | set(booked):
            months = values.get(key, [None] * MONTHS)
            actual = [c.value for c in booked.get(key, [Cell(None, None)] * MONTHS)]
            values[key] = actual[:closed] + months[closed:]
    return values


def _code(codes: dict[int, str], profile_id: int | None) -> str | None:
    return None if profile_id is None else codes.get(profile_id)


def _options(
    conn: Connection, ctx: _Context
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    def pairs(dim: int, *where: ColumnElement[bool]) -> list[tuple[str, str]]:
        query = select(member.c.code, member.c.name).where(
            member.c.dim_id == dim, member.c.is_group.is_(False), *where
        )
        return [(c, n) for c, n in conn.execute(query.order_by(member.c.code))]

    return (
        pairs(WORK_CENTER, member.c.cost_center_code == ctx.cost_center_code),
        pairs(ACTIVITY_TYPE),
    )


def _add(a: Decimal | None, b: Decimal | None) -> Decimal | None:
    if a is None:
        return b
    return a if b is None else a + b


def _stored_credits(conn: Connection, ctx: _Context) -> dict[str, list[Decimal | None]]:
    """The credits stored on the cost center, per account code."""
    f = fact_cost_center
    index = {p: i for i, p in enumerate(ctx.periods)}
    out: dict[str, list[Decimal | None]] = defaultdict(lambda: [None] * MONTHS)
    for code, p, amount in conn.execute(
        select(member.c.code, f.c.period, f.c.amount)
        .join(member, member.c.member_id == f.c.dim_account)
        .where(
            f.c.version_id == ctx.version_id,
            f.c.layer_id == BASE_LAYER,
            f.c.measure_id == AMOUNT,
            f.c.dim_cost_center == ctx.cost_center_id,
            f.c.dim_account.in_(select(credit_account.c.account_id)),
            f.c.period.in_(ctx.periods),
        )
    ):
        out[code][index[p]] = amount
    return dict(out)


# --- Writing ------------------------------------------------------------------------------------


def carry_hours(
    conn: Connection, source: RoundRef, target: RoundRef, cost_centers: Iterable[int]
) -> int:
    """A new round's planned hours from the round it is set up like (ADR 0016): month by month
    into the new year, without uplift, for the given productive cost centers, into the target's
    open months only. Credits are recomputed from them and the target's plan rates. Returns the
    number of hour cells written. Runs in the round's creating transaction and change batch."""
    ids = set(cost_centers)
    if not ids:
        return 0
    old, new = fiscal_periods(conn, source.fiscal_year), fiscal_periods(conn, target.fiscal_year)
    moved = {
        o: n
        for o, n in zip(old, new, strict=True)
        if target.actuals_through is None or n > target.actuals_through
    }
    f = fact_capacity
    rows = [
        {
            "version_id": target.version_id,
            "layer_id": BASE_LAYER,
            "measure_id": HOURS,
            "period": moved[r.period],
            "dim_entity": r.dim_entity,
            "dim_cost_center": r.dim_cost_center,
            "dim_work_center": r.dim_work_center,
            "dim_activity_type": r.dim_activity_type,
            "amount": r.amount,
        }
        for r in conn.execute(
            select(f).where(
                f.c.version_id == source.version_id,
                f.c.layer_id == BASE_LAYER,
                f.c.measure_id == HOURS,
                f.c.period.in_(list(moved)),
                f.c.dim_cost_center.in_(ids),
            )
        )
    ]
    insert_all(conn, f, rows)
    for cc_id in sorted({r["dim_cost_center"] for r in rows}):
        _write_credits(conn, _plain_context(conn, target, cc_id))
    return len(rows)


def _plain_context(conn: Connection, r: RoundRef, cc_id: int) -> _Context:
    """A cost center's context for writing by the system, without a user's rights."""
    cc = conn.execute(select(member).where(member.c.member_id == cc_id)).one()
    entity_id = conn.execute(
        select(member.c.member_id).where(
            member.c.dim_id == ENTITY, member.c.code == (cc.entity_code or NO_ENTITY)
        )
    ).scalar_one()
    return _Context(
        round=r,
        actuals_id=-1,
        cost_center_id=cc_id,
        cost_center_code=cc.code,
        cost_center_name=cc.name,
        entity_id=entity_id,
        status="not_started",
        lock_reason=None,
        periods=fiscal_periods(conn, r.fiscal_year),
        prior_periods=fiscal_periods(conn, r.fiscal_year - 1),
    )


def _run(engine: Engine, actor: User, work: Callable[[Connection], None]) -> str:
    """Run ``work`` in one transaction under a new change batch; returns the batch id."""
    batch_id = uuid.uuid4().hex
    try:
        with unit_of_work(engine, actor, batch_id) as conn:
            work(conn)
    except DBAPIError as e:
        locked = locked_error(e)
        if locked is None:
            raise
        raise locked from None
    return batch_id


def _member_id(conn: Connection, dim: int, code: str) -> int | None:
    found = conn.execute(
        select(member.c.member_id).where(
            member.c.dim_id == dim, member.c.code == code, member.c.is_group.is_(False)
        )
    ).scalar()
    return None if found is None else int(found)


def _editable(conn: Connection, actor: User, version_code: str, cc_code: str) -> _Context:
    ctx = _context(conn, actor, version_code, cc_code)
    if ctx.lock_reason is not None:
        raise LockedError(ctx.lock_reason)
    return ctx


def _line_of(
    conn: Connection, ctx: _Context, wc_code: str, at_code: str
) -> tuple[tuple[int, int], _Line]:
    wc, at = _member_id(conn, WORK_CENTER, wc_code), _member_id(conn, ACTIVITY_TYPE, at_code)
    line = _lines(conn, ctx).get((wc, at)) if wc is not None and at is not None else None
    if line is None:
        raise EditError(f"{wc_code} / {at_code} is not a line of {ctx.cost_center_code}")
    return (line.work_center_id, line.activity_type_id), line


def _keep(conn: Connection, ctx: _Context, line: _Line, actor: User) -> None:
    """Keep a line with the round, with its current rate, if it isn't yet."""
    if line.stored:
        return
    conn.execute(
        pg_insert(capacity_line)
        .values(
            version_id=ctx.version_id,
            module=MODULE_CAPACITY,
            cost_center_id=ctx.cost_center_id,
            work_center_id=line.work_center_id,
            activity_type_id=line.activity_type_id,
            rate=line.rate,
            changed_by=actor.login,
        )
        .on_conflict_do_nothing()
    )
    line.stored = True


def apply_edits(
    engine: Engine, actor: User, version_code: str, cc_code: str, edits: list[HoursEdit]
) -> str:
    """Write planned hours, all or none, and recompute the credits; returns the change batch."""

    def work(conn: Connection) -> None:
        ctx = _editable(conn, actor, version_code, cc_code)
        lines = _lines(conn, ctx)
        codes = {
            (dim, c): _member_id(conn, dim, c)
            for e in edits
            for dim, c in ((WORK_CENTER, e.work_center_code), (ACTIVITY_TYPE, e.activity_type_code))
        }
        prior = _hours(conn, ctx.actuals_id, None, ctx.prior_periods)
        to_date = (
            {
                key: sum((c.value for c in cells if c.value is not None), Decimal(0))
                for key, cells in _hours(
                    conn, ctx.actuals_id, None, ctx.periods[: ctx.closed]
                ).items()
            }
            if ctx.closed
            else {}
        )
        profiles = _profiles(conn, lines.values())
        locked = _hours(conn, ctx.version_id, ctx.cost_center_id, ctx.periods, lock=True)
        current = {
            (key, ctx.periods[m]): c.row_version
            for key, cells in locked.items()
            for m, c in enumerate(cells)
            if c.row_version is not None
        }
        conflicts: list[Conflict] = []
        writes: dict[tuple[tuple[int, int], date], Decimal | None] = {}
        for e in edits:
            if len(e.expected) != MONTHS:
                raise EditError("expected must list 12 row versions")
            wc = codes[(WORK_CENTER, e.work_center_code)]
            at = codes[(ACTIVITY_TYPE, e.activity_type_code)]
            line = lines.get((wc, at)) if wc is not None and at is not None else None
            label = f"{e.work_center_code} / {e.activity_type_code}"
            if line is None:
                raise EditError(f"{label} is not a line of {ctx.cost_center_code}")
            if not line.active:
                raise EditError(f"{label} is inactive in this round")
            months = _targets(ctx, e, label)
            key = (line.work_center_id, line.activity_type_id)
            for m in months:
                cell = (key, ctx.periods[m])
                if cell not in writes and current.get(cell) != e.expected[m]:
                    conflicts.append(Conflict(label, m))
            value = parse_amount(e.value)
            if value is not None and value < 0:
                raise EditError(f"{label}: hours can't be negative")
            _keep(conn, ctx, line, actor)
            if value is None or e.month is not None:
                for m in months:
                    writes[(key, ctx.periods[m])] = value
                continue
            if e.month is None and e.quarter is None and key in to_date:
                # A forecast's year total is the full year (ADR 0016).
                value -= to_date[key]
                if value < 0:
                    raise EditError(f"{label}: the year total is below the actual hours to date")
            before = [c.value for c in prior.get(key, [Cell(None, None)] * MONTHS)]
            basis, _, profile = _spread_of(line, profiles, before)
            w = weights(basis, MONTHS, profile, before, months)
            for m, hours in zip(months, spread(value, w.weights), strict=True):
                writes[(key, ctx.periods[m])] = hours
        if conflicts:
            raise ConflictError(conflicts)
        _write_hours(conn, ctx, writes, current)
        _write_credits(conn, ctx)
        _follow(conn, ctx)

    return _run(engine, actor, work)


def _targets(ctx: _Context, e: HoursEdit, label: str) -> list[int]:
    """The months an hours edit writes: one month, a quarter's three, or the year's open months
    (ADR 0016)."""
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
        if not 0 <= e.quarter < QUARTERS:
            raise EditError(f"quarter {e.quarter} out of range")
        if grain != "quarter":
            raise EditError("this round is planned by month")
        months = list(quarter_months(e.quarter))
    else:
        return ctx.open_months
    if months[0] < ctx.closed:
        raise EditError(f"{label}: {ctx.periods[months[0]]:%B %Y} is closed in this forecast")
    return months


def _write_hours(
    conn: Connection,
    ctx: _Context,
    writes: dict[tuple[tuple[int, int], date], Decimal | None],
    current: dict[tuple[tuple[int, int], date], int],
) -> None:
    f = fact_capacity
    deletes = [
        (wc, at, p) for ((wc, at), p), v in writes.items() if v is None and ((wc, at), p) in current
    ]
    if deletes:
        conn.execute(
            delete(f).where(
                f.c.version_id == ctx.version_id,
                f.c.layer_id == BASE_LAYER,
                f.c.measure_id == HOURS,
                f.c.dim_cost_center == ctx.cost_center_id,
                tuple_(f.c.dim_work_center, f.c.dim_activity_type, f.c.period).in_(deletes),
            )
        )
    upserts = [
        {
            "version_id": ctx.version_id,
            "layer_id": BASE_LAYER,
            "measure_id": HOURS,
            "period": p,
            "dim_entity": ctx.entity_id,
            "dim_cost_center": ctx.cost_center_id,
            "dim_work_center": wc,
            "dim_activity_type": at,
            "amount": v,
        }
        for ((wc, at), p), v in writes.items()
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


def _follow(conn: Connection, ctx: _Context) -> None:
    """New credits change what is left on the cost center, which its full forwardings pass on
    (ADR 0018). Imported here: forwarding builds on planning, which this module builds on."""
    from planlayer.forwarding import follow

    follow(conn, ctx.round, ctx.cost_center_id)


def _write_credits(conn: Connection, ctx: _Context) -> None:
    """Credits = hours x rate of the active lines, per activity type's account and month; the
    cost center's credit cells are replaced with them (blank where nothing is credited)."""
    rows = conn.execute(
        select(
            capacity_line.c.work_center_id,
            capacity_line.c.activity_type_id,
            capacity_line.c.rate,
            capacity_line.c.variable_rate,
            member.c.account_code,
            member.c.variable_account_code,
        )
        .join(member, member.c.member_id == capacity_line.c.activity_type_id)
        .where(
            capacity_line.c.version_id == ctx.version_id,
            capacity_line.c.cost_center_id == ctx.cost_center_id,
            capacity_line.c.active.is_(True),
        )
    ).all()
    codes = {c for r in rows for c in (r.account_code, r.variable_account_code) if c}
    account_ids = dict(
        conn.execute(
            select(member.c.code, member.c.member_id).where(
                member.c.dim_id == ACCOUNT, member.c.code.in_(codes)
            )
        ).all()
    )
    hours = _hours(conn, ctx.version_id, ctx.cost_center_id, ctx.periods)
    credit: dict[tuple[int, date], Decimal] = defaultdict(Decimal)
    for r in rows:
        cells = hours.get((r.work_center_id, r.activity_type_id))
        if cells is None:
            continue
        parts = ((r.account_code, r.rate), (r.variable_account_code, r.variable_rate))
        for code, rate in parts:
            if code is None or rate is None or code not in account_ids:
                continue
            for p, c in zip(ctx.periods, cells, strict=True):
                if c.value is not None:
                    credit[(account_ids[code], p)] += c.value * rate
    wanted = {
        k: v.quantize(CENT, ROUND_HALF_UP)
        for k, v in credit.items()
        if k[1] in ctx.periods[ctx.closed :]
    }

    f = fact_cost_center
    scope = and_(
        f.c.version_id == ctx.version_id,
        f.c.layer_id == BASE_LAYER,
        f.c.measure_id == AMOUNT,
        f.c.dim_cost_center == ctx.cost_center_id,
        f.c.period.in_(ctx.periods[ctx.closed :]),  # a forecast's closed months hold actuals
    )
    stored = {
        (a, p): v
        for a, p, v in conn.execute(
            select(f.c.dim_account, f.c.period, f.c.amount)
            .where(scope, f.c.dim_account.in_(select(credit_account.c.account_id)))
            .with_for_update()
        )
    }
    gone = [k for k in stored if k not in wanted]
    if gone:
        conn.execute(delete(f).where(scope, tuple_(f.c.dim_account, f.c.period).in_(gone)))
    changed = [
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
        for (a, p), v in wanted.items()
        if stored.get((a, p)) != v
    ]
    if changed:
        stmt = pg_insert(f).values(changed)
        conn.execute(
            stmt.on_conflict_do_update(
                index_elements=list(f.primary_key.columns),
                set_={"amount": stmt.excluded.amount, "row_version": f.c.row_version + 1},
            )
        )


@dataclass
class LineChange:
    """What to change on a line; unset fields stay."""

    rate: Decimal | None = None
    active: bool | None = None
    set_spread: bool = False
    spread: LineSpread | None = None  # with set_spread: None goes back to prior-year hours


def change_line(
    engine: Engine,
    actor: User,
    version_code: str,
    cc_code: str,
    wc_code: str,
    at_code: str,
    change: LineChange,
) -> str:
    """Change a line's plan rate, state or spread, and recompute the credits. Only a controller
    sets a line inactive or active again."""

    def work(conn: Connection) -> None:
        ctx = _editable(conn, actor, version_code, cc_code)
        key, line = _line_of(conn, ctx, wc_code, at_code)
        values: dict[str, object] = {"changed_by": actor.login}
        if change.rate is not None:
            if change.rate < 0:
                raise EditError("a rate can't be negative")
            exponent = change.rate.as_tuple().exponent
            if isinstance(exponent, int) and exponent < -4:
                raise EditError("a rate has at most 4 decimal places")
            values["rate"] = change.rate
        if change.active is not None:
            actor.require_controller()
            values["active"] = change.active
        if change.set_spread:
            basis, profile_id = None, None
            if change.spread is not None:
                basis = change.spread.basis
                if basis == "profile":
                    profile_id = conn.execute(
                        select(seasonality_profile.c.profile_id).where(
                            seasonality_profile.c.code == change.spread.profile_code
                        )
                    ).scalar()
                    if profile_id is None:
                        raise NotFoundError(
                            f"seasonality profile {change.spread.profile_code} not found"
                        )
                elif change.spread.profile_code is not None:
                    raise EditError("only a profile basis names a profile")
            values |= {"basis": basis, "profile_id": profile_id}
        _keep(conn, ctx, line, actor)
        conn.execute(
            update(capacity_line)
            .where(
                capacity_line.c.version_id == ctx.version_id,
                capacity_line.c.work_center_id == key[0],
                capacity_line.c.activity_type_id == key[1],
            )
            .values(**values)
        )
        _write_credits(conn, ctx)
        _follow(conn, ctx)

    return _run(engine, actor, work)


def add_line(
    engine: Engine,
    actor: User,
    version_code: str,
    cc_code: str,
    wc_code: str,
    at_code: str,
    rate: Decimal,
    new_work_center_name: str | None = None,
) -> str:
    """Add a line to the cost center's sheet in this round. With ``new_work_center_name``, the
    work center is created first, as a manual work center of the cost center (e.g. a machine
    planned to be bought)."""

    def work(conn: Connection) -> None:
        ctx = _editable(conn, actor, version_code, cc_code)
        code = wc_code.strip()
        if not code:
            raise EditError("a work center needs a code")
        if rate < 0:
            raise EditError("a rate can't be negative")
        wc = _member_id(conn, WORK_CENTER, code)
        if new_work_center_name is not None:
            name = new_work_center_name.strip()
            if not name:
                raise EditError("a new work center needs a name")
            if wc is not None:
                raise EditError(f"a work center {code} exists")
            wc = conn.execute(
                pg_insert(member)
                .values(
                    dim_id=WORK_CENTER,
                    code=code,
                    name=name,
                    is_group=False,
                    origin="manual",
                    cost_center_code=ctx.cost_center_code,
                )
                .returning(member.c.member_id)
            ).scalar_one()
        elif wc is None:
            raise NotFoundError(f"work center {code} not found")
        else:
            owner = conn.execute(
                select(member.c.cost_center_code).where(member.c.member_id == wc)
            ).scalar()
            if owner != ctx.cost_center_code:
                raise EditError(f"{code} belongs to cost center {owner or 'none'}")
        at = _member_id(conn, ACTIVITY_TYPE, at_code)
        if at is None:
            raise NotFoundError(f"activity type {at_code} not found")
        if (wc, at) in _lines(conn, ctx):
            raise EditError(f"{code} / {at_code} is on the sheet already")
        conn.execute(
            pg_insert(capacity_line).values(
                version_id=ctx.version_id,
                module=MODULE_CAPACITY,
                cost_center_id=ctx.cost_center_id,
                work_center_id=wc,
                activity_type_id=at,
                rate=rate,
                changed_by=actor.login,
            )
        )

    return _run(engine, actor, work)


LINE_STATE = ("rate", "variable_rate", "active", "basis", "profile_id")


def undo_batch(engine: Engine, actor: User, version_code: str, cc_code: str, batch_id: str) -> str:
    """Revert one of the actor's change batches on a capacity sheet (ADR 0010): planned hours
    get their old values, lines their old rate, state and spread (a line the batch added goes
    again), and the credits follow. Refused as a conflict, with nothing written, if any of it
    changed since. Returns the new batch, so undoing that one redoes the change."""

    def work(conn: Connection) -> None:
        ctx = _editable(conn, actor, version_code, cc_code)
        h, ln = capacity_change_log, capacity_line_log
        hours_log = conn.execute(
            select(
                h.c.dim_work_center,
                h.c.dim_activity_type,
                h.c.period,
                h.c.old_amount,
                h.c.new_amount,
            )
            .where(
                h.c.batch_id == batch_id,
                h.c.actor == actor.login,
                h.c.version_id == ctx.version_id,
                h.c.dim_cost_center == ctx.cost_center_id,
            )
            .order_by(h.c.id)
        ).all()
        line_log = conn.execute(
            select(ln)
            .where(
                ln.c.batch_id == batch_id,
                ln.c.actor == actor.login,
                ln.c.version_id == ctx.version_id,
                ln.c.cost_center_id == ctx.cost_center_id,
            )
            .order_by(ln.c.id)
        ).all()
        if not hours_log and not line_log:
            raise NotFoundError(f"no change of yours to undo on {cc_code}")

        # Lines: the state before the batch (None: it didn't exist), and the batch's last state.
        def state(row: object, when: str) -> tuple[object, ...] | None:
            values = tuple(getattr(row, f"{when}_{n}") for n in LINE_STATE)
            return None if getattr(row, f"{when}_active") is None else values

        line_before: dict[tuple[int, int], tuple[object, ...] | None] = {}
        line_after: dict[tuple[int, int], tuple[object, ...] | None] = {}
        for r in line_log:
            key = (r.work_center_id, r.activity_type_id)
            line_before.setdefault(key, state(r, "old"))
            line_after[key] = state(r, "new")
        stored = {
            (r.work_center_id, r.activity_type_id): tuple(getattr(r, n) for n in LINE_STATE)
            for r in conn.execute(
                select(capacity_line)
                .where(
                    capacity_line.c.version_id == ctx.version_id,
                    capacity_line.c.cost_center_id == ctx.cost_center_id,
                )
                .with_for_update()
            )
        }
        locked = _hours(conn, ctx.version_id, ctx.cost_center_id, ctx.periods, lock=True)
        current = {
            (key, ctx.periods[m]): c.row_version
            for key, cells in locked.items()
            for m, c in enumerate(cells)
            if c.row_version is not None
        }
        hours_before: dict[tuple[tuple[int, int], date], Decimal | None] = {}
        hours_after: dict[tuple[tuple[int, int], date], Decimal | None] = {}
        for wc, at, period, old, new in hours_log:
            hours_before.setdefault(((wc, at), period), old)
            hours_after[((wc, at), period)] = new

        blank = [Cell(None, None)] * MONTHS
        conflicts = [(key, -1) for key, after in line_after.items() if stored.get(key) != after]
        conflicts += [
            (key, ctx.periods.index(p))
            for (key, p), new in hours_after.items()
            if locked.get(key, blank)[ctx.periods.index(p)].value != new
        ]
        if conflicts:
            ids = {i for (wc, at), _ in conflicts for i in (wc, at)}
            codes = dict(
                conn.execute(
                    select(member.c.member_id, member.c.code).where(member.c.member_id.in_(ids))
                ).all()
            )
            raise ConflictError(
                [Conflict(f"{codes[wc]} / {codes[at]}", m) for (wc, at), m in sorted(conflicts)]
            )

        for (wc, at), before in line_before.items():
            where = (
                capacity_line.c.version_id == ctx.version_id,
                capacity_line.c.work_center_id == wc,
                capacity_line.c.activity_type_id == at,
            )
            if before is None:
                conn.execute(delete(capacity_line).where(*where))
                continue
            values = dict(zip(LINE_STATE, before, strict=True)) | {"changed_by": actor.login}
            if (wc, at) in stored:
                conn.execute(update(capacity_line).where(*where).values(**values))
            else:
                conn.execute(
                    pg_insert(capacity_line).values(
                        version_id=ctx.version_id,
                        module=MODULE_CAPACITY,
                        cost_center_id=ctx.cost_center_id,
                        work_center_id=wc,
                        activity_type_id=at,
                        **values,
                    )
                )
        _write_hours(conn, ctx, hours_before, current)
        _write_credits(conn, ctx)
        _follow(conn, ctx)

    return _run(engine, actor, work)


@dataclass
class CapacityUnit:
    code: str
    name: str
    status: Status
    can_write: bool


def units(engine: Engine, user: User, version_code: str) -> list[CapacityUnit]:
    """The round's productive cost centers whose capacity the user may read."""
    with unit_of_work(engine, user) as conn:
        r = get_round(conn, version_code)
        rights = capacity_rights(conn, user)
        ids = sorted(productive(conn, r.version_id) & set(rights))
        rows = conn.execute(
            select(member.c.member_id, member.c.code, member.c.name)
            .where(member.c.member_id.in_(ids))
            .order_by(member.c.code)
        ).all()
        return [
            CapacityUnit(code, name, status_of(conn, r.version_id, mid), rights[mid])
            for mid, code, name in rows
        ]
