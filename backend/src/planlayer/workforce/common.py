"""What the workforce modules share: the context a cost center's workforce is read and
written in, its stored values, and the change batch every write runs in (ADR 0020)."""

import uuid
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import cast

from sqlalchemy import (
    Connection,
    Engine,
    delete,
    select,
    tuple_,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError

from planlayer.access import User, cost_center_rights
from planlayer.domain.periods import closed_months
from planlayer.domain.workflow import EDITABLE, Status
from planlayer.domain.workforce import (
    Measure,
    Mode,
)
from planlayer.rounds import NotFoundError, RoundRef, fiscal_periods, get_round, not_open
from planlayer.sheet import (
    MONTHS,
    Cell,
    LockedError,
    locked_error,
)
from planlayer.store.schema import (
    BASE_LAYER,
    COST_CENTER,
    ENTITY,
    FTE,
    HEADCOUNT,
    MODULE_WORKFORCE,
    NO_ENTITY,
    fact_workforce,
    member,
    round_module,
    version,
)
from planlayer.store.uow import unit_of_work
from planlayer.sync import ACTUALS_CODE
from planlayer.workflow import enabled_members, status_of

CENT = Decimal("0.01")


MEASURE_ID: dict[Measure, int] = {"fte": FTE, "headcount": HEADCOUNT}


MEASURE_OF = {v: k for k, v in MEASURE_ID.items()}


Line = tuple[int, int]  # staff type, staff category


Key = tuple[int, int, int]  # measure, staff type, staff category


Months = list[Decimal | None]


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


def _add(a: Decimal | None, b: Decimal | None) -> Decimal | None:
    if a is None:
        return b
    return a if b is None else a + b


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


def _blank(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None
