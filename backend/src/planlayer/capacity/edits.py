"""Writing capacity (ADR 0013): planned hours, a line's rate and state, lines added to the
round, and undo."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import Connection, Engine, delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from planlayer.access import User
from planlayer.capacity.common import (
    _Context,
    _editable,
    _hours,
    _keep,
    _line_of,
    _lines,
    _member_id,
    _profiles,
    _run,
    _spread_of,
    _unit,
    _write_hours,
)
from planlayer.capacity.credits import _follow, _write_credits
from planlayer.capacity.staff import refresh
from planlayer.domain.entry_forms import weights
from planlayer.domain.periods import QUARTERS, quarter_months
from planlayer.domain.spreading import spread
from planlayer.planning import LineSpread
from planlayer.rounds import NotFoundError
from planlayer.sheet import (
    MONTHS,
    Cell,
    Conflict,
    ConflictError,
    EditError,
    parse_amount,
)
from planlayer.store.schema import (
    ACTIVITY_TYPE,
    MODULE_CAPACITY,
    WORK_CENTER,
    capacity_change_log,
    capacity_line,
    capacity_line_log,
    member,
    seasonality_profile,
)


@dataclass
class HoursEdit:
    work_center_code: str
    activity_type_code: str
    month: int | None  # 0-11; None with no quarter: the year total (spread by the line's basis)
    value: str | None  # None = blank
    expected: list[int | None]  # row_version of each month as the client saw it
    quarter: int | None = None  # 0-3 in a round planned by quarter (ADR 0016)


def apply_edits(
    engine: Engine, actor: User, version_code: str, cc_code: str, edits: list[HoursEdit]
) -> str:
    """Write planned hours, all or none, and recompute the credits; returns the change batch."""

    def work(conn: Connection) -> None:
        ctx = _editable(conn, actor, version_code, cc_code)
        if not _unit(conn, ctx.version_id, ctx.cost_center_id).typed_hours:
            raise EditError(f"{ctx.cost_center_code}'s hours come from its positions")
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
            if line.staffed:
                raise EditError(f"{label}: its hours come from the positions")
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
        refresh(conn, ctx)  # the line's spread and state move hours from positions

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
        refresh(conn, ctx)  # hours from positions follow the restored lines

    return _run(engine, actor, work)
