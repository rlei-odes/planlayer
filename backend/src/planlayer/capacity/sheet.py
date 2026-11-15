"""A productive cost center's capacity sheet (ADR 0013): reading it, and the cost centers
whose capacity a user reads."""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Literal

from sqlalchemy import ColumnElement, Connection, Engine, select

from planlayer.access import User, capacity_rights
from planlayer.capacity.common import (
    Hours,
    _add,
    _code,
    _Context,
    _context,
    _hours,
    _Line,
    _lines,
    _profiles,
    _spread_of,
    productive,
)
from planlayer.capacity.credits import _stored_credits
from planlayer.domain.entry_forms import Basis, weights
from planlayer.domain.periods import closed_months
from planlayer.domain.workflow import RoundStatus, Status
from planlayer.notes import Marker
from planlayer.notes import markers as note_markers
from planlayer.planning import LineSpread, comparison_columns, cost_values
from planlayer.rounds import Grain, fiscal_periods, get_round
from planlayer.sheet import (
    MONTHS,
    Cell,
    ComparisonColumn,
    SheetContext,
)
from planlayer.store.schema import (
    ACCOUNT,
    ACTIVITY_TYPE,
    MODULE_COST_CENTER,
    WORK_CENTER,
    comparison,
    member,
    planning_round,
)
from planlayer.store.uow import unit_of_work
from planlayer.workflow import status_of


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
