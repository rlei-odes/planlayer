"""Transfers of positions between cost centers (ADR 0021): proposed by the sending side,
accepted or declined by the receiving side."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import (
    Connection,
    Engine,
    Row,
    func,
    insert,
    select,
    update,
)

from planlayer.access import AccessError, User, cost_center_rights
from planlayer.domain.workflow import EDITABLE, Status
from planlayer.domain.workforce import (
    PositionError,
    TransferKind,
    check_transfer,
)
from planlayer.personnel import recompute
from planlayer.personnel.pay import continue_pay
from planlayer.rounds import NotFoundError
from planlayer.sheet import (
    EditError,
    LockedError,
)
from planlayer.store.schema import (
    COST_CENTER,
    MODULE_WORKFORCE,
    app_user,
    member,
    version,
    workforce_position,
    workforce_transfer,
)
from planlayer.store.uow import system_rights, unit_of_work
from planlayer.workflow import enabled_members, status_of
from planlayer.workforce.common import _blank, _context, _plain_context, _run
from planlayer.workforce.positions import _not_transferred_out, _positions_context, write_rollup


@dataclass
class Transfer:
    """A position's transfer to another cost center, as both sides see it."""

    transfer_id: int
    version_code: str
    version_name: str
    status: Literal["proposed", "accepted", "declined", "cancelled"]
    kind: TransferKind
    starts: date | None  # the first day on the receiving cost center; None: the whole year
    position_id: int
    position_code: str
    title: str
    person_name: str | None
    staff_type_name: str
    staff_category_name: str
    contract_percent: Decimal | None
    from_code: str
    from_name: str
    to_code: str
    to_name: str
    note: str | None
    proposed_by: str
    proposed_at: datetime
    decided_by: str | None
    decided_at: datetime | None
    reason: str | None
    can_decide: bool  # the reader writes the receiving cost center
    can_cancel: bool  # the reader writes the sending cost center


class TransferError(EditError):
    """A transfer that can't be proposed or decided as asked (422, like an edit)."""


def _transfers(conn: Connection, user: User, where: Any) -> list[Transfer]:
    """Transfers the user may read (either side), oldest first. The position is read with the
    system's rights: the receiving side sees whom it is to take over."""
    t = workforce_transfer
    rows = conn.execute(select(t).where(where).order_by(t.c.proposed_at, t.c.transfer_id)).all()
    if not rows:
        return []
    with system_rights(conn, actor=user.login):
        positions = {
            p.position_id: p
            for p in conn.execute(
                select(workforce_position).where(
                    workforce_position.c.position_id.in_({r.position_id for r in rows})
                )
            )
        }
    ids = {i for r in rows for i in (r.from_cost_center_id, r.to_cost_center_id)} | {
        i for p in positions.values() for i in (p.staff_type_id, p.staff_category_id)
    }
    meta = dict(
        conn.execute(
            select(member.c.member_id, func.json_build_array(member.c.code, member.c.name)).where(
                member.c.member_id.in_(ids)
            )
        ).all()
    )
    people = dict(
        conn.execute(
            select(app_user.c.user_id, app_user.c.display_name).where(
                app_user.c.user_id.in_(
                    {r.proposed_by for r in rows} | {r.decided_by for r in rows if r.decided_by}
                )
            )
        ).all()
    )
    rounds = dict(
        conn.execute(
            select(version.c.version_id, func.json_build_array(version.c.code, version.c.name))
            .where(version.c.version_id.in_({r.version_id for r in rows}))
        ).all()
    )  # fmt: skip
    rights = cost_center_rights(conn, user)
    out: list[Transfer] = []
    for r in rows:
        p = positions.get(r.position_id)
        if p is None:
            continue
        out.append(
            Transfer(
                r.transfer_id,
                rounds[r.version_id][0],
                rounds[r.version_id][1],
                r.status,
                r.kind,
                r.starts,
                r.position_id,
                p.code,
                p.title,
                p.person_name,
                meta[p.staff_type_id][1],
                meta[p.staff_category_id][1],
                p.contract_percent,
                meta[r.from_cost_center_id][0],
                meta[r.from_cost_center_id][1],
                meta[r.to_cost_center_id][0],
                meta[r.to_cost_center_id][1],
                r.note,
                people[r.proposed_by],
                r.proposed_at,
                people.get(r.decided_by) if r.decided_by else None,
                r.decided_at,
                r.reason,
                rights.get(r.to_cost_center_id, False),
                rights.get(r.from_cost_center_id, False),
            )
        )
    return out


@dataclass
class TransferTarget:
    code: str
    name: str
    status: Status
    editable: bool  # its status still takes positions


def transfer_targets(
    engine: Engine, user: User, version_code: str, cc_code: str
) -> list[TransferTarget]:
    """The cost centers planned in the round that a position of ``cc_code`` may go to."""
    with unit_of_work(engine, user) as conn:
        ctx = _context(conn, user, version_code, cc_code)
        planned = [m for m, on in enabled_members(conn, ctx.version_id).items() if on]
        rows = conn.execute(
            select(member.c.member_id, member.c.code, member.c.name)
            .where(member.c.member_id.in_(planned), member.c.member_id != ctx.cost_center_id)
            .order_by(member.c.code)
        ).all()
        out = []
        for m in rows:
            status = status_of(conn, ctx.version_id, m.member_id)
            out.append(TransferTarget(m.code, m.name, status, status in EDITABLE))
    return out


def propose_transfer(
    engine: Engine,
    actor: User,
    version_code: str,
    cc_code: str,
    position_id: int,
    to_code: str,
    kind: TransferKind,
    starts: date | None,
    note: str | None = None,
) -> int:
    """Propose to transfer a position to another cost center planned in the round. Nothing
    moves until the receiving side accepts. Returns the transfer's id."""
    created: list[int] = []

    def work(conn: Connection) -> None:
        ctx = _positions_context(conn, actor, version_code, cc_code)
        p = conn.execute(
            select(workforce_position).where(
                workforce_position.c.position_id == position_id,
                workforce_position.c.version_id == ctx.version_id,
                workforce_position.c.cost_center_id == ctx.cost_center_id,
            )
        ).first()
        if p is None:
            raise NotFoundError(f"position {position_id} not found on {cc_code}")
        _not_transferred_out(p)
        pending = conn.execute(
            select(workforce_transfer.c.transfer_id).where(
                workforce_transfer.c.position_id == position_id,
                workforce_transfer.c.status == "proposed",
            )
        ).first()
        if pending is not None:
            raise TransferError(f"{p.code} has a transfer waiting already")
        to_id = conn.execute(
            select(member.c.member_id).where(
                member.c.dim_id == COST_CENTER,
                member.c.code == to_code,
                member.c.is_group.is_(False),
            )
        ).scalar()
        if to_id is None:
            raise NotFoundError(f"cost center {to_code} not found")
        if to_id == ctx.cost_center_id:
            raise TransferError("a position transfers to another cost center")
        if not enabled_members(conn, ctx.version_id).get(to_id, False):
            raise TransferError(f"{to_code} isn't planned in this round")
        try:
            check_transfer(p.entry_date, p.leave_date, kind, starts, ctx.periods, ctx.closed)
        except PositionError as e:
            raise TransferError(str(e)) from None
        created.append(
            conn.execute(
                insert(workforce_transfer)
                .values(
                    version_id=ctx.version_id,
                    module=MODULE_WORKFORCE,
                    position_id=position_id,
                    from_cost_center_id=ctx.cost_center_id,
                    to_cost_center_id=to_id,
                    kind=kind,
                    starts=starts,
                    note=_blank(note),
                    proposed_by=actor.user_id,
                )
                .returning(workforce_transfer.c.transfer_id)
            ).scalar_one()
        )

    _run(engine, actor, work)
    return created[0]


def _proposed(conn: Connection, user: User, transfer_id: int, side: str) -> Row[Any]:
    """The proposed transfer, locked, for a user who writes its ``side`` cost center."""
    query = select(workforce_transfer).where(workforce_transfer.c.transfer_id == transfer_id)
    seen = conn.execute(query).first()
    if seen is None:
        raise NotFoundError(f"transfer {transfer_id} not found")
    if not cost_center_rights(conn, user).get(getattr(seen, f"{side}_cost_center_id"), False):
        raise AccessError(
            "only the receiving cost center's planners accept or decline a transfer"
            if side == "to"
            else "only the sending cost center's planners withdraw a transfer"
        )
    t = conn.execute(query.with_for_update()).one()
    if t.status != "proposed":
        raise TransferError(f"the transfer is {t.status} already")
    return t


def _close(conn: Connection, actor: User, t: Row[Any], status: str, **values: object) -> None:
    conn.execute(
        update(workforce_transfer)
        .where(workforce_transfer.c.transfer_id == t.transfer_id)
        .values(status=status, decided_by=actor.user_id, decided_at=func.now(), **values)
    )


def decide_transfer(
    engine: Engine, actor: User, transfer_id: int, accept: bool, reason: str | None = None
) -> str:
    """Accept or decline a proposed transfer, as the receiving side (its planners, or a
    controller). Accepting moves the position in one change batch:

    - **full:** the position moves with its whole year and remembers where it came from;
    - **during the year:** the sending position ends the day before ``starts``, marked as
      transferred, and the receiving cost center gets the person from ``starts``, with the same
      number.

    Both cost centers' FTE and headcount follow. Either side locked refuses it (423)."""

    def work(conn: Connection) -> None:
        t = _proposed(conn, actor, transfer_id, "to")
        if not accept:
            _close(conn, actor, t, "declined", reason=_blank(reason))
            return
        code = conn.execute(
            select(version.c.code).where(version.c.version_id == t.version_id)
        ).scalar_one()
        to_code = conn.execute(
            select(member.c.code).where(member.c.member_id == t.to_cost_center_id)
        ).scalar_one()
        target = _positions_context(conn, actor, code, to_code)
        with system_rights(conn, actor=actor.login):
            source = _plain_context(conn, target.round, "positions", t.from_cost_center_id)
            status = status_of(conn, t.version_id, t.from_cost_center_id)
            if status not in EDITABLE:
                raise LockedError(f"the sending cost center {source.cost_center_code} is {status}")
            if not enabled_members(conn, t.version_id).get(t.from_cost_center_id, False):
                raise LockedError("the sending cost center is no longer planned in the round")
            p = conn.execute(
                select(workforce_position)
                .where(workforce_position.c.position_id == t.position_id)
                .with_for_update()
            ).one()
            try:
                check_transfer(
                    p.entry_date, p.leave_date, t.kind, t.starts, target.periods, target.closed
                )
            except PositionError as e:
                raise TransferError(f"{p.code} can no longer transfer like this: {e}") from None
            wp = workforce_position
            new_id: int | None = None
            if t.kind == "full":
                conn.execute(
                    update(wp)
                    .where(wp.c.position_id == p.position_id)
                    .values(
                        cost_center_id=t.to_cost_center_id,
                        transferred_from_id=t.from_cost_center_id,
                        changed_by=actor.login,
                        changed_at=func.now(),
                    )
                )
            else:
                conn.execute(
                    update(wp)
                    .where(wp.c.position_id == p.position_id)
                    .values(
                        leave_date=t.starts - timedelta(days=1),
                        transferred_to_id=t.to_cost_center_id,
                        changed_by=actor.login,
                        changed_at=func.now(),
                    )
                )
                kept = {
                    c.name: p._mapping[c.name]
                    for c in wp.c
                    if c.name
                    not in ("position_id", "changed_at", "transferred_to_id", "transferred_from_id")
                }
                new_id = conn.execute(
                    insert(wp)
                    .values(
                        {
                            **kept,
                            "cost_center_id": t.to_cost_center_id,
                            "entry_date": t.starts,
                            "transferred_from_id": t.from_cost_center_id,
                            "changed_by": actor.login,
                        }
                    )
                    .returning(wp.c.position_id)
                ).scalar_one()
                continue_pay(conn, p.position_id, new_id)
            write_rollup(conn, source)
            write_rollup(conn, target)
            _close(conn, actor, t, "accepted", new_position_id=new_id)
        recompute(conn, target.round, actor.login, by_hr=actor.is_hr)

    return _run(engine, actor, work)


def cancel_transfer(engine: Engine, actor: User, transfer_id: int) -> None:
    """Withdraw a proposed transfer, as the sending side (its planners, or a controller)."""

    def work(conn: Connection) -> None:
        t = _proposed(conn, actor, transfer_id, "from")
        _close(conn, actor, t, "cancelled")

    _run(engine, actor, work)


RECENT = timedelta(days=30)


def my_transfers(engine: Engine, user: User) -> list[Transfer]:
    """The transfers the user decides (proposed to a cost center they write), those waiting that
    they proposed, and those they proposed that were decided in the last 30 days."""
    t = workforce_transfer
    with unit_of_work(engine, user) as conn:
        writable = [cc for cc, w in cost_center_rights(conn, user).items() if w]
        return _transfers(
            conn,
            user,
            ((t.c.status == "proposed") & t.c.to_cost_center_id.in_(writable))
            | (
                (t.c.proposed_by == user.user_id)
                & ((t.c.status == "proposed") | (t.c.decided_at > func.now() - RECENT))
            ),
        )
