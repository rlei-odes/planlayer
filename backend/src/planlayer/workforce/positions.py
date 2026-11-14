"""Positions (ADR 0020): adding, changing and removing them, *Add staff from HR*, and the
roll-up of a cost center's positions into its FTE and headcount."""

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import (
    Connection,
    Engine,
    Integer,
    Row,
    delete,
    func,
    insert,
    select,
    update,
)

from planlayer.access import AccessError, User
from planlayer.domain.workflow import EDITABLE
from planlayer.domain.workforce import (
    Position,
    PositionError,
    check,
    month_end,
    rollup,
)
from planlayer.rounds import NotFoundError, RoundError, get_round
from planlayer.sheet import (
    EditError,
)
from planlayer.store.schema import (
    MODULE_WORKFORCE,
    STAFF_CATEGORY,
    STAFF_TYPE,
    hr_staff,
    member,
    workforce_position,
)
from planlayer.workflow import enabled_members, status_of
from planlayer.workforce.common import (
    CENT,
    MEASURE_ID,
    Key,
    _blank,
    _Context,
    _editable,
    _member_id,
    _plain_context,
    _run,
    _values,
    _write,
    mode_of,
)

NUMBERING_LOCK = 2020  # advisory lock class of the position numbers (ADR 0020)


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
