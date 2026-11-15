"""Who reads pay in a round, as HR controls it (ADR 0023).

Per round and cost center, HR keeps a list of readers. It starts from the owners the source
names; a source owner HR hasn't decided on yet shows as a proposal. Nobody but HR reads pay in a
round until HR releases it. Every change of the lists, of the release and of the HR role is in
``pay_access_log``, written by triggers and read by every HR user."""

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import Connection, Engine, delete, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from planlayer.access import User, approvers
from planlayer.personnel.common import pay_round, run
from planlayer.rounds import NotFoundError, RoundRef
from planlayer.sheet import EditError
from planlayer.store.schema import (
    COST_CENTER,
    H_COST_CENTER,
    app_user,
    closure,
    cost_center_owner,
    member,
    pay_access_log,
    pay_owner_seen,
    pay_reader,
    pay_release,
    planner_grant,
    version,
)
from planlayer.store.uow import system_rights, unit_of_work
from planlayer.workflow import enabled_members

LOG_LIMIT = 300


def _planned(conn: Connection, version_id: int) -> list[int]:
    return sorted(m for m, on in enabled_members(conn, version_id).items() if on)


def _owners(conn: Connection, cc_ids: list[int]) -> dict[int, int | None]:
    """The owner the source names per cost center (the matched user, not an override)."""
    return dict(
        conn.execute(
            select(cost_center_owner.c.cost_center_id, cost_center_owner.c.matched_user_id).where(
                cost_center_owner.c.cost_center_id.in_(cc_ids)
            )
        ).all()
    )


def seed(conn: Connection, r: RoundRef, actor: str) -> None:
    """When personnel costs come on: each planned cost center's source owner as its reader, and
    as the owner decided on. Not released."""
    planned = _planned(conn, r.version_id)
    owners = _owners(conn, planned)
    if not planned:
        return
    with system_rights(conn, actor=actor):
        conn.execute(
            pg_insert(pay_owner_seen)
            .values(
                [
                    {"version_id": r.version_id, "cost_center_id": cc, "owner_id": owners.get(cc)}
                    for cc in planned
                ]
            )
            .on_conflict_do_nothing()
        )
        readers = [
            {"version_id": r.version_id, "cost_center_id": cc, "user_id": o, "added_by": actor}
            for cc, o in owners.items()
            if o is not None
        ]
        if readers:
            conn.execute(pg_insert(pay_reader).values(readers).on_conflict_do_nothing())


def carry(conn: Connection, source: RoundRef, target: RoundRef, actor: str) -> None:
    """A new round's readers and decided owners from the round it is set up like, for the
    cost centers it plans; not released, so HR confirms them again."""
    planned = _planned(conn, target.version_id)
    with system_rights(conn, actor=actor):
        for table, columns in (
            (pay_owner_seen, ("cost_center_id", "owner_id")),
            (pay_reader, ("cost_center_id", "user_id")),
        ):
            rows = conn.execute(
                select(*(table.c[c] for c in columns)).where(
                    table.c.version_id == source.version_id,
                    table.c.cost_center_id.in_(planned),
                )
            ).all()
            if rows:
                extra = {"added_by": actor} if table is pay_reader else {}
                conn.execute(
                    pg_insert(table)
                    .values(
                        [{"version_id": target.version_id, **r._mapping, **extra} for r in rows]
                    )
                    .on_conflict_do_nothing()
                )


def released(conn: Connection, version_id: int) -> bool:
    return (
        conn.execute(
            select(pay_release.c.version_id).where(pay_release.c.version_id == version_id)
        ).first()
        is not None
    )


def listed(conn: Connection, user: User, version_id: int) -> set[int]:
    """The cost centers HR lists the user for in a round, released or not."""
    return set(
        conn.execute(
            select(pay_reader.c.cost_center_id).where(
                pay_reader.c.version_id == version_id, pay_reader.c.user_id == user.user_id
            )
        ).scalars()
    )


# --- HR's page --------------------------------------------------------------------------------


@dataclass
class Person:
    login: str
    name: str


@dataclass
class RightsRow:
    code: str
    name: str
    readers: list[Person]
    suggested: list[str]  # logins: deputies, approvers above, the source owner
    source_owner: Person | None  # as the source names it today
    proposal: bool  # the source owner differs from the one HR decided on, or none decided yet


@dataclass
class LogEntry:
    at: datetime
    actor: str | None
    action: str
    round_code: str | None
    cost_center: str | None
    user: str | None


@dataclass
class RightsPage:
    round_code: str
    round_status: str
    released_by: str | None
    released_at: datetime | None
    rows: list[RightsRow]
    users: list[Person]  # every active user, for "More…"
    log: list[LogEntry] = field(default_factory=list)


def read_rights(engine: Engine, user: User, version_code: str) -> RightsPage:
    user.require_hr()
    with unit_of_work(engine, user) as conn:
        r = pay_round(conn, version_code)
        planned = _planned(conn, r.version_id)
        people = {
            u.user_id: Person(u.login, u.display_name)
            for u in conn.execute(
                select(app_user.c.user_id, app_user.c.login, app_user.c.display_name).where(
                    app_user.c.is_system.is_(False)
                )
            )
        }
        active = {
            u
            for u in conn.execute(
                select(app_user.c.user_id).where(
                    app_user.c.is_system.is_(False), app_user.c.disabled.is_(False)
                )
            ).scalars()
        }
        names = {
            m.member_id: (m.code, m.name)
            for m in conn.execute(
                select(member.c.member_id, member.c.code, member.c.name).where(
                    member.c.dim_id == COST_CENTER
                )
            )
        }
        readers: dict[int, list[int]] = {}
        for cc, u in conn.execute(
            select(pay_reader.c.cost_center_id, pay_reader.c.user_id).where(
                pay_reader.c.version_id == r.version_id
            )
        ):
            readers.setdefault(cc, []).append(u)
        seen = dict(
            conn.execute(
                select(pay_owner_seen.c.cost_center_id, pay_owner_seen.c.owner_id).where(
                    pay_owner_seen.c.version_id == r.version_id
                )
            ).all()
        )
        owners = _owners(conn, planned)
        deputies: dict[int, set[int]] = {}
        for cc, u in conn.execute(
            select(closure.c.descendant_id, planner_grant.c.user_id)
            .join(planner_grant, planner_grant.c.member_id == closure.c.ancestor_id)
            .where(closure.c.hier_id == H_COST_CENTER, closure.c.descendant_id.in_(planned))
        ):
            deputies.setdefault(cc, set()).add(u)
        above = approvers(conn, planned)
        release = conn.execute(
            select(pay_release).where(pay_release.c.version_id == r.version_id)
        ).first()
        rounds = dict(conn.execute(select(version.c.version_id, version.c.code)).all())
        log = [
            LogEntry(
                e.at,
                e.actor,
                e.action,
                rounds.get(e.version_id) if e.version_id else None,
                names[e.cost_center_id][0] if e.cost_center_id in names else None,
                people[e.user_id].login if e.user_id in people else None,
            )
            for e in conn.execute(
                select(pay_access_log).order_by(pay_access_log.c.id.desc()).limit(LOG_LIMIT)
            )
        ]
    rows = []
    for cc in sorted(planned, key=lambda c: names[c][0]):
        owner = owners.get(cc)
        suggested = (
            ({owner} if owner else set())
            | deputies.get(cc, set())
            | {u for u, _ in above.get(cc, [])}
        ) & active
        rows.append(
            RightsRow(
                *names[cc],
                sorted((people[u] for u in readers.get(cc, [])), key=lambda p: p.login),
                sorted(people[u].login for u in suggested),
                people[owner] if owner else None,
                cc not in seen or seen[cc] != owner,
            )
        )
    return RightsPage(
        r.code,
        r.status,
        release.released_by if release else None,
        release.released_at if release else None,
        rows,
        sorted((people[u] for u in active), key=lambda p: p.login),
        log,
    )


def set_readers(
    engine: Engine,
    user: User,
    version_code: str,
    cc_code: str,
    logins: list[str],
    *,
    owner_decided: bool = False,
) -> None:
    """Replace a cost center's readers in a round (HR). With ``owner_decided``, the source's
    current owner counts as decided on, whether HR listed them or not."""
    user.require_hr()

    def work(conn: Connection) -> None:
        r = pay_round(conn, version_code)
        cc = conn.execute(
            select(member.c.member_id).where(
                member.c.dim_id == COST_CENTER,
                member.c.code == cc_code,
                member.c.is_group.is_(False),
            )
        ).scalar()
        if cc is None or not enabled_members(conn, r.version_id).get(cc, False):
            raise NotFoundError(f"cost center {cc_code} not planned in {version_code}")
        wanted_logins = {x.strip().lower() for x in logins if x.strip()}
        found = dict(
            conn.execute(
                select(app_user.c.login, app_user.c.user_id).where(
                    app_user.c.login.in_(wanted_logins),
                    app_user.c.is_system.is_(False),
                    app_user.c.disabled.is_(False),
                )
            ).all()
        )
        missing = sorted(wanted_logins - set(found))
        if missing:
            raise EditError(f"no active user {', '.join(missing)}")
        wanted = set(found.values())
        now = set(
            conn.execute(
                select(pay_reader.c.user_id).where(
                    pay_reader.c.version_id == r.version_id, pay_reader.c.cost_center_id == cc
                )
            ).scalars()
        )
        gone = now - wanted
        if gone:
            conn.execute(
                delete(pay_reader).where(
                    pay_reader.c.version_id == r.version_id,
                    pay_reader.c.cost_center_id == cc,
                    pay_reader.c.user_id.in_(gone),
                )
            )
        new = wanted - now
        if new:
            conn.execute(
                insert(pay_reader),
                [
                    {
                        "version_id": r.version_id,
                        "cost_center_id": cc,
                        "user_id": u,
                        "added_by": user.login,
                    }
                    for u in sorted(new)
                ],
            )
        if owner_decided:
            owner = _owners(conn, [cc]).get(cc)
            stmt = pg_insert(pay_owner_seen).values(
                version_id=r.version_id, cost_center_id=cc, owner_id=owner
            )
            conn.execute(
                stmt.on_conflict_do_update(
                    index_elements=[pay_owner_seen.c.version_id, pay_owner_seen.c.cost_center_id],
                    set_={"owner_id": stmt.excluded.owner_id},
                )
            )

    run(engine, user, work)


def release(engine: Engine, user: User, version_code: str, on: bool) -> None:
    """Release the round's pay to its readers (HR), or take the release back: then only HR
    reads pay in the round again."""
    user.require_hr()

    def work(conn: Connection) -> None:
        r = pay_round(conn, version_code)
        if released(conn, r.version_id) == on:
            return
        if on:
            conn.execute(
                insert(pay_release).values(version_id=r.version_id, released_by=user.login)
            )
        else:
            conn.execute(delete(pay_release).where(pay_release.c.version_id == r.version_id))

    run(engine, user, work)
