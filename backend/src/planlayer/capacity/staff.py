"""Capacity from the workforce (ADR 0027): a productive cost center's hours from its positions.

Each position's productive hours go to one line of the cost center: the cost center's default
line, or the position's own. They are the round's hours per full-time position, times the
position's contract percentage and productive share (its own, or the cost center's default),
spread over the months by the line's weights and counted for the days the position is active.
A position without a contract percentage (e.g. a freelancer) has its year's hours typed.

The lines that take hours from positions are marked ``staffed``. Every recompute writes their
hours in the open months, clears the hours of lines no longer staffed, and recomputes the
credits, in the same transaction as the change that set it off."""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import Connection, Engine, and_, delete, insert, literal, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError

from planlayer.access import User
from planlayer.capacity.common import (
    CENT,
    _Context,
    _context,
    _editable,
    _hours,
    _keep,
    _Line,
    _lines,
    _member_id,
    _plain_context,
    _profiles,
    _run,
    _spread_of,
    _Unit,
    _unit,
    _write_hours,
    productive,
)
from planlayer.capacity.credits import _follow, _write_credits
from planlayer.domain.capacity import StaffPosition, hours
from planlayer.domain.entry_forms import weights
from planlayer.rounds import NotFoundError, RoundError, RoundRef
from planlayer.sheet import MONTHS, Cell, EditError, locked_error, parse_amount
from planlayer.store.schema import (
    ACTIVITY_TYPE,
    MODULE_CAPACITY,
    MODULE_WORKFORCE,
    WORK_CENTER,
    capacity_line,
    capacity_position,
    capacity_unit,
    fact_capacity,
    member,
    round_module,
    version,
    workforce_position,
)
from planlayer.store.uow import system_rights, unit_of_work
from planlayer.sync import ACTUALS_CODE

Key = tuple[int, int]  # work center, activity type


def fte_hours(conn: Connection, version_id: int) -> Decimal | None:
    """The round's hours per full-time position a year."""
    found = conn.execute(
        select(round_module.c.fte_hours).where(
            round_module.c.version_id == version_id, round_module.c.module == MODULE_CAPACITY
        )
    ).scalar()
    return None if found is None else Decimal(found)


@dataclass
class _Staff:
    position_id: int
    code: str
    title: str
    person_name: str | None
    staff_type: str
    entry_date: date | None
    leave_date: date | None
    contract_percent: Decimal | None
    own_percent: Decimal | None
    own_hours: Decimal | None  # typed, without a contract %
    own_line: Key | None
    line: Key | None = None  # the line its hours go to: its own, or the default
    percent: Decimal | None = None  # its productive share: its own, or the default
    months: list[Decimal] | None = None  # its hours per month, unrounded


def _staff(conn: Connection, ctx: _Context, unit: _Unit) -> list[_Staff]:
    p, c, t = workforce_position, capacity_position, member.alias("staff_type")
    out = []
    for r in conn.execute(
        select(
            p.c.position_id,
            p.c.code,
            p.c.title,
            p.c.person_name,
            t.c.name.label("staff_type"),
            p.c.entry_date,
            p.c.leave_date,
            p.c.contract_percent,
            c.c.productive_percent,
            c.c.hours,
            c.c.work_center_id,
            c.c.activity_type_id,
        )
        .join(t, t.c.member_id == p.c.staff_type_id)
        .outerjoin(c, c.c.position_id == p.c.position_id)
        .where(p.c.version_id == ctx.version_id, p.c.cost_center_id == ctx.cost_center_id)
        .order_by(p.c.code, p.c.entry_date.nulls_first(), p.c.position_id)
    ):
        own = (
            None
            if r.work_center_id is None or r.activity_type_id is None
            else (r.work_center_id, r.activity_type_id)
        )
        out.append(
            _Staff(
                r.position_id,
                r.code,
                r.title,
                r.person_name,
                r.staff_type,
                r.entry_date,
                r.leave_date,
                r.contract_percent,
                r.productive_percent,
                r.hours,
                own,
                own or unit.default_line,
                # A position without a contract % has its hours typed: no share applies.
                None
                if r.contract_percent is None
                else r.productive_percent
                if r.productive_percent is not None
                else unit.default_percent,
            )
        )
    return out


def _actuals_id(conn: Connection) -> int:
    found = conn.execute(
        select(version.c.version_id).where(version.c.code == ACTUALS_CODE)
    ).scalar()
    return -1 if found is None else int(found)


def _computed(
    conn: Connection, ctx: _Context, unit: _Unit, lines: dict[Key, _Line]
) -> tuple[list[_Staff], dict[Key, list[Decimal | None]]]:
    """The positions with their hours, and the hours of each staffed line per month, rounded
    per cell: blank where none of its positions has a productive share."""
    if not unit.staff_hours:
        return [], {}
    staff = _staff(conn, ctx, unit)
    wanted = {unit.default_line} | {s.line for s in staff}
    fed = {k for k in wanted if k is not None and k in lines and lines[k].active}
    fte = fte_hours(conn, ctx.version_id)
    prior = _hours(conn, _actuals_id(conn), None, ctx.prior_periods)
    profiles = _profiles(conn, [lines[k] for k in fed])
    blank = [Cell(None, None)] * MONTHS
    month_weights: dict[Key, list[Decimal]] = {}
    for k in fed:
        line = lines[k]
        line.staffed = True  # its default spread is even
        before = [c.value for c in prior.get(k, blank)]
        basis, _, profile = _spread_of(line, profiles, before)
        month_weights[k] = [Decimal(w) for w in weights(basis, MONTHS, profile, before).weights]
    cells: dict[Key, list[Decimal | None]] = {k: [None] * MONTHS for k in fed}
    for s in staff:
        if s.line not in fed or fte is None:
            continue
        p = StaffPosition(s.contract_percent, s.percent, s.entry_date, s.leave_date, s.own_hours)
        s.months = hours(p, fte, month_weights[s.line], ctx.periods)
        if s.months is None:
            continue
        row = cells[s.line]
        cells[s.line] = [(v or Decimal(0)) + h for v, h in zip(row, s.months, strict=True)]
    return staff, {
        k: [None if v is None else v.quantize(CENT, ROUND_HALF_UP) for v in row]
        for k, row in cells.items()
    }


def refresh(conn: Connection, ctx: _Context) -> None:
    """Write the staffed lines' hours in the open months, clear the hours of lines no longer
    staffed, and recompute the credits."""
    unit = _unit(conn, ctx.version_id, ctx.cost_center_id)
    lines = _lines(conn, ctx)
    was = {k for k, line in lines.items() if line.staffed}
    _, cells = _computed(conn, ctx, unit, lines)
    locked = _hours(conn, ctx.version_id, ctx.cost_center_id, ctx.periods, lock=True)
    current = {
        (key, ctx.periods[m]): c.row_version
        for key, row in locked.items()
        for m, c in enumerate(row)
        if c.row_version is not None
    }
    blank = [Cell(None, None)] * MONTHS
    writes: dict[tuple[Key, date], Decimal | None] = {}
    for key in set(cells) | was:
        target = cells.get(key, [None] * MONTHS)
        stored = locked.get(key, blank)
        for m in ctx.open_months:
            if stored[m].value != target[m]:
                writes[(key, ctx.periods[m])] = target[m]
    _write_hours(conn, ctx, writes, current)
    for key, staffed in [(k, True) for k in set(cells) - was] + [
        (k, False) for k in was - set(cells)
    ]:
        conn.execute(
            update(capacity_line)
            .where(
                capacity_line.c.version_id == ctx.version_id,
                capacity_line.c.work_center_id == key[0],
                capacity_line.c.activity_type_id == key[1],
            )
            .values(staffed=staffed)
        )
    _write_credits(conn, ctx)
    _follow(conn, ctx)


def _needs_refresh(conn: Connection, version_id: int) -> set[int]:
    """Cost centers with hours from positions, or lines still marked so."""
    on = select(capacity_unit.c.cost_center_id).where(
        capacity_unit.c.version_id == version_id, capacity_unit.c.staff_hours.is_(True)
    )
    marked = select(capacity_line.c.cost_center_id).where(
        capacity_line.c.version_id == version_id, capacity_line.c.staffed.is_(True)
    )
    return set(conn.execute(on.union(marked)).scalars()) & productive(conn, version_id)


def follow_positions(
    conn: Connection, r: RoundRef, actor: str, cost_centers: Iterable[int] | None = None
) -> None:
    """Recompute the hours from positions after positions changed: on ``cost_centers``, or the
    whole round. Runs with the system's rights, as a transfer changes two cost centers."""
    todo = _needs_refresh(conn, r.version_id)
    if cost_centers is not None:
        todo &= set(cost_centers)
    if not todo:
        return
    with system_rights(conn, actor=actor):
        for cc in sorted(todo):
            refresh(conn, _plain_context(conn, r, cc))


# --- The workforce tab ---------------------------------------------------------------------


@dataclass
class StaffLine:
    """A line of the cost center positions' hours can go to."""

    work_center_code: str
    work_center_name: str
    activity_type_code: str
    activity_type_name: str
    rate: Decimal
    hours: Decimal | None  # the hours positions give it in the open months
    typed: bool = False  # it has work center hours: positions can't take it


@dataclass
class StaffRow:
    position_id: int
    code: str
    title: str
    person_name: str | None
    staff_type: str
    entry_date: date | None
    leave_date: date | None
    contract_percent: Decimal | None
    productive_percent: Decimal | None  # its own; None takes the default
    typed_hours: Decimal | None  # without a contract %: the year's hours, typed
    work_center_code: str | None  # its own line; None takes the default
    activity_type_code: str | None
    hours: Decimal | None  # in the open months; None: no productive share, or no line


@dataclass
class StaffView:
    cost_center_code: str
    cost_center_name: str
    lock_reason: str | None
    fte_hours: Decimal | None
    typed_hours: bool
    default_work_center_code: str | None
    default_activity_type_code: str | None
    default_percent: Decimal | None
    lines: list[StaffLine] = field(default_factory=list)
    positions: list[StaffRow] = field(default_factory=list)


def _staffed_context(conn: Connection, user: User, version_code: str, cc_code: str) -> _Context:
    ctx = _context(conn, user, version_code, cc_code)
    if not _unit(conn, ctx.version_id, ctx.cost_center_id).staff_hours:
        raise NotFoundError(f"{cc_code} plans no hours from its workforce in this round")
    return ctx


def read_staff(engine: Engine, user: User, version_code: str, cc_code: str) -> StaffView:
    with unit_of_work(engine, user) as conn:
        ctx = _staffed_context(conn, user, version_code, cc_code)
        unit = _unit(conn, ctx.version_id, ctx.cost_center_id)
        lines = _lines(conn, ctx)
        typed = _typed(conn, ctx, lines)
        staff, cells = _computed(conn, ctx, unit, lines)
        ids = {i for k in lines for i in k}
        meta = {
            r.member_id: (r.code, r.name)
            for r in conn.execute(
                select(member.c.member_id, member.c.code, member.c.name).where(
                    member.c.member_id.in_(ids)
                )
            )
        }
        fte = fte_hours(conn, ctx.version_id)
    open_months = ctx.open_months

    def total(months: Iterable[Decimal | None]) -> Decimal | None:
        picked = [v for m, v in enumerate(months) if m in open_months and v is not None]
        return sum(picked, Decimal(0)) if picked else None

    def code(key: Key | None, i: int) -> str | None:
        return None if key is None else meta[key[i]][0]

    view = StaffView(
        ctx.cost_center_code,
        ctx.cost_center_name,
        ctx.lock_reason,
        fte,
        unit.typed_hours,
        code(unit.default_line, 0),
        code(unit.default_line, 1),
        unit.default_percent,
    )
    for key in sorted(
        (k for k, ln in lines.items() if ln.active), key=lambda k: (meta[k[0]], meta[k[1]])
    ):
        wc, at = meta[key[0]], meta[key[1]]
        hours_of = total(cells[key]) if key in cells else None
        view.lines.append(
            StaffLine(wc[0], wc[1], at[0], at[1], lines[key].rate, hours_of, key in typed)
        )
    for s in staff:
        year = None if s.months is None else total(s.months)
        view.positions.append(
            StaffRow(
                s.position_id,
                s.code,
                s.title,
                s.person_name,
                s.staff_type,
                s.entry_date,
                s.leave_date,
                s.contract_percent,
                s.own_percent,
                s.own_hours,
                code(s.own_line, 0),
                code(s.own_line, 1),
                None if year is None else year.quantize(CENT, ROUND_HALF_UP),
            )
        )
    return view


def _percent(value: str | None) -> Decimal | None:
    amount = parse_amount(value)
    if amount is None:
        return None
    if not 0 <= amount <= 100:
        raise EditError("a productive share is between 0 and 100 %")
    return amount


def _typed(conn: Connection, ctx: _Context, lines: dict[Key, _Line]) -> set[Key]:
    """Lines with hours of their own in the open months: typed, not from positions. Read from the
    stored lines, before a computation marks the ones positions feed."""
    stored = _hours(conn, ctx.version_id, ctx.cost_center_id, ctx.periods)
    return {
        k
        for k, row in stored.items()
        if k in lines
        and not lines[k].staffed
        and any(row[m].value is not None for m in ctx.open_months)
    }


def _line(
    conn: Connection, ctx: _Context, actor: User, codes: tuple[str, str] | None
) -> Key | None:
    """An active line of the cost center without work center hours of its own, kept with the
    round; None: no line. Positions' hours would replace the hours typed on a line."""
    if codes is None:
        return None
    wc, at = _member_id(conn, WORK_CENTER, codes[0]), _member_id(conn, ACTIVITY_TYPE, codes[1])
    lines = _lines(conn, ctx)
    line = lines.get((wc, at)) if wc is not None and at is not None else None
    if line is None:
        raise EditError(f"{codes[0]} / {codes[1]} is not a line of {ctx.cost_center_code}")
    if not line.active:
        raise EditError(f"{codes[0]} / {codes[1]} is inactive in this round")
    if (line.work_center_id, line.activity_type_id) in _typed(conn, ctx, lines):
        raise EditError(
            f"{codes[0]} / {codes[1]} has work center hours; clear them, or add a line for the "
            "workforce"
        )
    _keep(conn, ctx, line, actor)
    return (line.work_center_id, line.activity_type_id)


def save_defaults(
    engine: Engine,
    actor: User,
    version_code: str,
    cc_code: str,
    line: tuple[str, str] | None,
    percent: str | None,
) -> str:
    """The cost center's default line and productive share for its positions; the hours
    follow. Returns the change batch."""

    def work(conn: Connection) -> None:
        ctx = _editable(conn, actor, version_code, cc_code)
        if not _unit(conn, ctx.version_id, ctx.cost_center_id).staff_hours:
            raise NotFoundError(f"{cc_code} plans no hours from its workforce in this round")
        key = _line(conn, ctx, actor, line)
        conn.execute(
            update(capacity_unit)
            .where(
                capacity_unit.c.version_id == ctx.version_id,
                capacity_unit.c.cost_center_id == ctx.cost_center_id,
            )
            .values(
                default_work_center_id=None if key is None else key[0],
                default_activity_type_id=None if key is None else key[1],
                default_percent=_percent(percent),
                changed_by=actor.login,
            )
        )
        refresh(conn, ctx)

    return _run(engine, actor, work)


def save_position(
    engine: Engine,
    actor: User,
    version_code: str,
    cc_code: str,
    position_id: int,
    line: tuple[str, str] | None,
    percent: str | None,
    hours: str | None = None,
) -> str:
    """A position's own line and productive share, or for a position without a contract % its
    year's hours; None takes the cost center's default. The hours follow. Returns the change
    batch."""

    def work(conn: Connection) -> None:
        ctx = _editable(conn, actor, version_code, cc_code)
        if not _unit(conn, ctx.version_id, ctx.cost_center_id).staff_hours:
            raise NotFoundError(f"{cc_code} plans no hours from its workforce in this round")
        found = conn.execute(
            select(workforce_position.c.position_id, workforce_position.c.contract_percent).where(
                workforce_position.c.position_id == position_id,
                workforce_position.c.version_id == ctx.version_id,
                workforce_position.c.cost_center_id == ctx.cost_center_id,
            )
        ).first()
        if found is None:
            raise NotFoundError(f"position {position_id} not found on {cc_code}")
        key, share, typed = _line(conn, ctx, actor, line), _percent(percent), parse_amount(hours)
        if found.contract_percent is None and share is not None:
            raise EditError("a position without a contract % takes its hours, not a share")
        if found.contract_percent is not None and typed is not None:
            raise EditError("a position with a contract % takes a productive share, not hours")
        if typed is not None and typed < 0:
            raise EditError("hours can't be negative")
        if key is None and share is None and typed is None:
            conn.execute(
                delete(capacity_position).where(
                    capacity_position.c.position_id == found.position_id
                )
            )
        else:
            values = {
                "productive_percent": share,
                "hours": typed,
                "work_center_id": None if key is None else key[0],
                "activity_type_id": None if key is None else key[1],
                "changed_by": actor.login,
            }
            stmt = pg_insert(capacity_position).values(
                position_id=found.position_id,
                version_id=ctx.version_id,
                module=MODULE_CAPACITY,
                cost_center_id=ctx.cost_center_id,
                **values,
            )
            conn.execute(stmt.on_conflict_do_update(index_elements=["position_id"], set_=values))
        refresh(conn, ctx)

    return _run(engine, actor, work)


# --- Setup and new rounds -----------------------------------------------------------------


def drop_sources(conn: Connection, r: RoundRef, kept: set[int]) -> None:
    """Cost centers no longer productive leave their settings for hours from positions; before
    they leave the round's productive cost centers, whose lock guards the settings."""
    for table in (capacity_position, capacity_unit):
        conn.execute(
            delete(table).where(
                table.c.version_id == r.version_id, table.c.cost_center_id.not_in(kept)
            )
        )


def set_sources(
    conn: Connection, r: RoundRef, actor: str, sources: dict[int, tuple[bool, bool]]
) -> None:
    """Where the productive cost centers' hours come from: (typed, from positions) per cost
    center; then the hours from positions follow. A cost center whose hours are no longer typed
    must have no typed hours."""
    stored = {
        row.cost_center_id: (row.typed_hours, row.staff_hours)
        for row in conn.execute(
            select(capacity_unit).where(capacity_unit.c.version_id == r.version_id)
        )
    }
    try:
        for cc, (typed, staff) in sorted(sources.items()):
            if stored.get(cc, (True, False)) == (typed, staff):
                continue
            values = {"typed_hours": typed, "staff_hours": staff, "changed_by": actor}
            stmt = pg_insert(capacity_unit).values(
                version_id=r.version_id, module=MODULE_CAPACITY, cost_center_id=cc, **values
            )
            conn.execute(
                stmt.on_conflict_do_update(
                    index_elements=["version_id", "cost_center_id"], set_=values
                )
            )
        follow_positions(conn, r, actor)
    except DBAPIError as e:
        locked = locked_error(e)
        if locked is None:
            raise
        raise RoundError(f"hours from the workforce can't change: {locked}") from None
    untyped = {cc for cc, (typed, _) in sources.items() if not typed}
    f, ln = fact_capacity, capacity_line
    typed_left = (
        conn.execute(
            select(member.c.code)
            .where(
                member.c.member_id.in_(
                    select(f.c.dim_cost_center)
                    .join(
                        ln,
                        and_(
                            ln.c.version_id == f.c.version_id,
                            ln.c.work_center_id == f.c.dim_work_center,
                            ln.c.activity_type_id == f.c.dim_activity_type,
                        ),
                        isouter=True,
                    )
                    .where(
                        f.c.version_id == r.version_id,
                        f.c.dim_cost_center.in_(untyped),
                        ln.c.staffed.is_not(True),
                    )
                )
            )
            .order_by(member.c.code)
        )
        .scalars()
        .all()
    )
    if typed_left:
        raise RoundError(
            f"typed hours on {', '.join(typed_left)}; clear them before their hours come "
            "from the workforce only"
        )


def sources(conn: Connection, version_id: int) -> dict[int, tuple[bool, bool]]:
    """(typed, from positions) per productive cost center."""
    stored = {
        row.cost_center_id: (row.typed_hours, row.staff_hours)
        for row in conn.execute(
            select(capacity_unit).where(capacity_unit.c.version_id == version_id)
        )
    }
    return {cc: stored.get(cc, (True, False)) for cc in productive(conn, version_id)}


def carry_staff(conn: Connection, source: RoundRef, target: RoundRef, actor: str) -> None:
    """A new round's settings for hours from positions, from the round it is set up like: the
    hours per full-time position, where each productive cost center's hours come from, its
    default line and share, and its positions' own, matched by number and cost center. Runs in
    the round's creating transaction, after the workforce."""
    kept = productive(conn, target.version_id)
    if not kept:
        return
    hours = fte_hours(conn, source.version_id)
    if hours is not None:
        conn.execute(
            update(round_module)
            .where(
                round_module.c.version_id == target.version_id,
                round_module.c.module == MODULE_CAPACITY,
            )
            .values(fte_hours=hours)
        )
    t = target.version_id
    lines = {
        (r.work_center_id, r.activity_type_id)
        for r in conn.execute(
            select(capacity_line.c.work_center_id, capacity_line.c.activity_type_id).where(
                capacity_line.c.version_id == t
            )
        )
    }

    def line(wc: int | None, at: int | None) -> tuple[int | None, int | None]:
        return (wc, at) if (wc, at) in lines else (None, None)

    # Hours from positions need positions in the new round.
    positions = (
        conn.execute(
            select(round_module.c.mode).where(
                round_module.c.version_id == t, round_module.c.module == MODULE_WORKFORCE
            )
        ).scalar()
        == "positions"
    )
    units = [
        {
            "version_id": t,
            "module": MODULE_CAPACITY,
            "cost_center_id": u.cost_center_id,
            "typed_hours": u.typed_hours or not positions,
            "staff_hours": u.staff_hours and positions,
            "default_work_center_id": line(u.default_work_center_id, u.default_activity_type_id)[0],
            "default_activity_type_id": line(u.default_work_center_id, u.default_activity_type_id)[
                1
            ],
            "default_percent": u.default_percent,
            "changed_by": actor,
        }
        for u in conn.execute(
            select(capacity_unit).where(
                capacity_unit.c.version_id == source.version_id,
                capacity_unit.c.cost_center_id.in_(kept),
            )
        )
    ]
    if units:
        conn.execute(insert(capacity_unit), units)
    old_p, new_p = workforce_position.alias("old_p"), workforce_position.alias("new_p")
    c = capacity_position
    rows = conn.execute(
        select(
            new_p.c.position_id,
            new_p.c.cost_center_id,
            c.c.productive_percent,
            c.c.hours,
            c.c.work_center_id,
            c.c.activity_type_id,
        )
        .join(old_p, old_p.c.position_id == c.c.position_id)
        .join(
            new_p,
            and_(
                new_p.c.code == old_p.c.code,
                new_p.c.cost_center_id == old_p.c.cost_center_id,
                new_p.c.version_id == literal(t),
            ),
        )
        .where(
            c.c.version_id == source.version_id,
            old_p.c.transferred_to_id.is_(None),
            c.c.cost_center_id.in_(kept),
        )
    ).all()
    if rows:
        conn.execute(
            insert(capacity_position),
            [
                {
                    "position_id": x.position_id,
                    "version_id": t,
                    "module": MODULE_CAPACITY,
                    "cost_center_id": x.cost_center_id,
                    "productive_percent": x.productive_percent,
                    "hours": x.hours,
                    "work_center_id": line(x.work_center_id, x.activity_type_id)[0],
                    "activity_type_id": line(x.work_center_id, x.activity_type_id)[1],
                    "changed_by": actor,
                }
                for x in rows
            ],
        )
    follow_positions(conn, target, actor)
