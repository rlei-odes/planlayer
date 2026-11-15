"""What the capacity modules share: the context a cost center's capacity is read and written
in, its lines and planned hours, and the change batch every write runs in (ADR 0013)."""

import uuid
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import Connection, Engine, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError

from planlayer.access import User, capacity_rights
from planlayer.domain.entry_forms import Basis
from planlayer.domain.periods import closed_months
from planlayer.domain.spreading import Profile
from planlayer.domain.workflow import EDITABLE, Status
from planlayer.rounds import NotFoundError, RoundRef, fiscal_periods, get_round, not_open
from planlayer.sheet import (
    MONTHS,
    Cell,
    EditError,
    LockedError,
    locked_error,
)
from planlayer.store.schema import (
    ACTIVITY_TYPE,
    BASE_LAYER,
    COST_CENTER,
    ENTITY,
    HOURS,
    MODULE_CAPACITY,
    NO_ENTITY,
    WORK_CENTER,
    capacity_line,
    fact_capacity,
    member,
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
        reason: str | None = not_open(r.status)
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


Hours = dict[tuple[int, int], list[Decimal | None]]


def _code(codes: dict[int, str], profile_id: int | None) -> str | None:
    return None if profile_id is None else codes.get(profile_id)


def _add(a: Decimal | None, b: Decimal | None) -> Decimal | None:
    if a is None:
        return b
    return a if b is None else a + b


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
