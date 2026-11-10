"""Users, sessions and who may do what (ADR 0009).

Roles are three flags on a user: admin (users, connections, syncs), controller (rounds, setup,
workflow, responsibilities; reads and writes every cost center) and viewer (reads every cost
center). Planning and approval rights come from data:

- **planner:** the cost center's owner, matched from the synced owner by e-mail or login, or
  set by the controller instead (override); plus deputies granted on a cost center or a node;
- **approver:** assigned to a node; approves the cost centers whose nearest assigned node it is,
  and reads every cost center below it.

Reads and writes of plan values and actuals are enforced by row-level security over the view
``cost_center_access``; the checks here give clear errors and filter lists.
"""

import hashlib
import secrets
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import Connection, Engine, Table, delete, exists, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from planlayer.store.schema import (
    COST_CENTER,
    H_COST_CENTER,
    app_user,
    approver_assignment,
    capacity_access,
    capacity_grant,
    closure,
    cost_center_access,
    cost_center_owner,
    hier_edge,
    member,
    owner_override,
    planner_grant,
    user_session,
)
from planlayer.store.uow import Actor, unit_of_work

SYSTEM_LOGIN = "system"


class AccessError(PermissionError):
    """The user may not do this."""


class UserError(ValueError):
    """A user or responsibility change that can't be made as asked."""


@dataclass(frozen=True)
class User(Actor):
    display_name: str
    email: str | None = None
    is_admin: bool = False
    is_controller: bool = False
    is_viewer: bool = False

    def require_controller(self) -> None:
        if not self.is_controller:
            raise AccessError("only a controller may do this")

    def require_admin(self) -> None:
        if not self.is_admin:
            raise AccessError("only an admin may do this")


_USER_COLUMNS = (
    app_user.c.user_id,
    app_user.c.login,
    app_user.c.display_name,
    app_user.c.email,
    app_user.c.is_admin,
    app_user.c.is_controller,
    app_user.c.is_viewer,
)


def _user(row: object) -> User:
    return User(**row._mapping)  # type: ignore[attr-defined]


def system_user(engine: Engine) -> User:
    """The actor of syncs, CLI commands and other jobs."""
    with engine.connect() as conn:
        return _user(
            conn.execute(select(*_USER_COLUMNS).where(app_user.c.login == SYSTEM_LOGIN)).one()
        )


def user_by_login(conn: Connection, login: str) -> User:
    row = conn.execute(
        select(*_USER_COLUMNS).where(
            app_user.c.login == login.lower(),
            app_user.c.disabled.is_(False),
            app_user.c.is_system.is_(False),
        )
    ).first()
    if row is None:
        raise UserError(f"no active user {login}")
    return _user(row)


@dataclass
class UserRow:
    login: str
    display_name: str
    email: str | None
    is_admin: bool
    is_controller: bool
    is_viewer: bool
    disabled: bool


def users(engine: Engine, *, active_only: bool = False) -> list[UserRow]:
    query = (
        select(
            app_user.c.login,
            app_user.c.display_name,
            app_user.c.email,
            app_user.c.is_admin,
            app_user.c.is_controller,
            app_user.c.is_viewer,
            app_user.c.disabled,
        )
        .where(app_user.c.is_system.is_(False))
        .order_by(app_user.c.login)
    )
    if active_only:
        query = query.where(app_user.c.disabled.is_(False))
    with engine.connect() as conn:
        return [UserRow(**r._mapping) for r in conn.execute(query)]


def create_user(
    engine: Engine,
    actor: User,
    login: str,
    display_name: str,
    email: str | None = None,
    *,
    is_admin: bool = False,
    is_controller: bool = False,
    is_viewer: bool = False,
) -> None:
    actor.require_admin()
    login = login.strip().lower()
    if not login or login == SYSTEM_LOGIN:
        raise UserError(f"login {login!r} is not allowed")
    with unit_of_work(engine, actor) as conn:
        found = conn.execute(select(app_user.c.user_id).where(app_user.c.login == login)).first()
        if found is not None:
            raise UserError(f"user {login} exists")
        conn.execute(
            insert(app_user).values(
                login=login,
                display_name=display_name,
                email=email,
                is_admin=is_admin,
                is_controller=is_controller,
                is_viewer=is_viewer,
            )
        )


def update_user(engine: Engine, actor: User, login: str, **changes: bool | str | None) -> None:
    """Change a user's roles (``is_admin``, ``is_controller``, ``is_viewer``), ``disabled``,
    ``display_name`` or ``email``. Disabling ends the user's sessions."""
    actor.require_admin()
    allowed = {"is_admin", "is_controller", "is_viewer", "disabled", "display_name", "email"}
    if not changes or set(changes) - allowed:
        raise UserError(f"can change only {', '.join(sorted(allowed))}")
    if login.lower() == actor.login and (
        changes.get("disabled") or changes.get("is_admin") is False
    ):
        raise UserError("you can't disable yourself or remove your own admin role")
    with unit_of_work(engine, actor) as conn:
        user_id = conn.execute(
            update(app_user)
            .where(app_user.c.login == login.lower(), app_user.c.is_system.is_(False))
            .values(**changes)
            .returning(app_user.c.user_id)
        ).scalar()
        if user_id is None:
            raise UserError(f"no user {login}")
        if changes.get("disabled"):
            conn.execute(delete(user_session).where(user_session.c.user_id == user_id))


# --- Sessions -------------------------------------------------------------------------------------


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(engine: Engine, login: str, hours: int) -> str:
    """A new session token for an active user; only its hash is stored."""
    token = "pl_" + secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    with engine.begin() as conn:
        user = user_by_login(conn, login)
        conn.execute(delete(user_session).where(user_session.c.expires_at < now))
        conn.execute(
            insert(user_session).values(
                token_hash=_hash(token),
                user_id=user.user_id,
                expires_at=now + timedelta(hours=hours),
            )
        )
    return token


def session_user(engine: Engine, token: str) -> User | None:
    """The active user of an unexpired session, or None."""
    with engine.connect() as conn:
        row = conn.execute(
            select(*_USER_COLUMNS)
            .join(user_session, user_session.c.user_id == app_user.c.user_id)
            .where(
                user_session.c.token_hash == _hash(token),
                user_session.c.expires_at > func.now(),
                app_user.c.disabled.is_(False),
            )
        ).first()
    return None if row is None else _user(row)


def end_session(engine: Engine, token: str) -> None:
    with engine.begin() as conn:
        conn.execute(delete(user_session).where(user_session.c.token_hash == _hash(token)))


# --- Access ---------------------------------------------------------------------------------------


def cost_center_rights(conn: Connection, user: User) -> dict[int, bool]:
    """Cost centers the user may read, and whether they may write them."""
    rights: dict[int, bool] = {}
    for cc, can_write in conn.execute(
        select(cost_center_access.c.cost_center_id, cost_center_access.c.can_write).where(
            cost_center_access.c.user_id == user.user_id
        )
    ):
        rights[cc] = rights.get(cc, False) or can_write
    return rights


def reads_cost_centers(engine: Engine, user: User) -> bool:
    """Whether the user may read any cost center: through a role, ownership, a deputy grant or
    an approver assignment. The admin role alone gives none (ADR 0009)."""
    with unit_of_work(engine, user) as conn:
        found = conn.execute(
            select(exists().where(cost_center_access.c.user_id == user.user_id))
        ).scalar()
    return bool(found)


def plans(conn: Connection, user: User, cc_ids: list[int]) -> set[int]:
    """The cost centers among ``cc_ids`` the user plans as owner or deputy (not as controller):
    those they can't approve."""
    owned = set(
        conn.execute(
            select(cost_center_owner.c.cost_center_id).where(
                cost_center_owner.c.user_id == user.user_id,
                cost_center_owner.c.cost_center_id.in_(cc_ids),
            )
        ).scalars()
    )
    granted = set(
        conn.execute(
            select(closure.c.descendant_id)
            .join(planner_grant, planner_grant.c.member_id == closure.c.ancestor_id)
            .where(
                closure.c.hier_id == H_COST_CENTER,
                planner_grant.c.user_id == user.user_id,
                closure.c.descendant_id.in_(cc_ids),
            )
        ).scalars()
    )
    return owned | granted


def approvers(conn: Connection, cc_ids: list[int]) -> dict[int, list[tuple[int, str]]]:
    """(user id, login) of the users assigned to the nearest node above each cost center;
    missing: the controller approves."""
    rows = conn.execute(
        select(closure.c.descendant_id, closure.c.depth, app_user.c.user_id, app_user.c.login)
        .join(approver_assignment, approver_assignment.c.member_id == closure.c.ancestor_id)
        .join(app_user, app_user.c.user_id == approver_assignment.c.user_id)
        .where(
            closure.c.hier_id == H_COST_CENTER,
            closure.c.depth >= 1,
            closure.c.descendant_id.in_(cc_ids),
            app_user.c.disabled.is_(False),
        )
        .order_by(closure.c.descendant_id, closure.c.depth, app_user.c.login)
    ).all()
    nearest: dict[int, int] = {}
    found: dict[int, list[tuple[int, str]]] = defaultdict(list)
    for cc, depth, user_id, login in rows:
        if nearest.setdefault(cc, depth) == depth:
            found[cc].append((user_id, login))
    return dict(found)


# --- Responsibilities page ------------------------------------------------------------------------


@dataclass
class ResponsibilityRow:
    kind: str  # node / cost_center
    code: str
    name: str
    depth: int
    owner: str | None = None  # synced owner value
    matched: str | None = None  # login matched from it
    planner: str | None = None  # effective owner-planner (login)
    overridden: bool = False
    deputies: list[str] = field(default_factory=list)  # granted here
    capacity_planners: list[str] = field(default_factory=list)  # granted here (ADR 0013)
    approvers: list[str] = field(default_factory=list)  # assigned here (nodes)


@dataclass
class Responsibilities:
    rows: list[ResponsibilityRow]
    unmatched_owners: list[str]  # synced owner values that match no active user


def responsibilities(engine: Engine, user: User) -> Responsibilities:
    user.require_controller()
    with engine.connect() as conn:
        logins = dict(conn.execute(select(app_user.c.user_id, app_user.c.login)).all())
        info = {
            r.member_id: r
            for r in conn.execute(
                select(member.c.member_id, member.c.code, member.c.name, member.c.owner).where(
                    member.c.dim_id == COST_CENTER
                )
            )
        }
        owners = {r.cost_center_id: r for r in conn.execute(select(cost_center_owner))}
        deputies: dict[int, list[str]] = defaultdict(list)
        for m, u in conn.execute(
            select(planner_grant.c.member_id, planner_grant.c.user_id).order_by(
                planner_grant.c.user_id
            )
        ):
            deputies[m].append(logins[u])
        capacity: dict[int, list[str]] = defaultdict(list)
        for m, u in conn.execute(select(capacity_grant.c.member_id, capacity_grant.c.user_id)):
            capacity[m].append(logins[u])
        assigned: dict[int, list[str]] = defaultdict(list)
        for m, u in conn.execute(
            select(approver_assignment.c.member_id, approver_assignment.c.user_id)
        ):
            assigned[m].append(logins[u])
        children: dict[int | None, list[int]] = defaultdict(list)
        for m, parent in conn.execute(
            select(hier_edge.c.member_id, hier_edge.c.parent_id)
            .join(member, member.c.member_id == hier_edge.c.member_id)
            .where(hier_edge.c.hier_id == H_COST_CENTER)
            .order_by(hier_edge.c.sort_order, member.c.code)
        ):
            children[parent].append(m)

    rows: list[ResponsibilityRow] = []

    def visit(node: int, depth: int) -> None:
        m = info[node]
        if node in children:
            rows.append(
                ResponsibilityRow(
                    "node",
                    m.code,
                    m.name,
                    depth,
                    deputies=sorted(deputies[node]),
                    capacity_planners=sorted(capacity[node]),
                    approvers=sorted(assigned[node]),
                )
            )
            for child in children[node]:
                visit(child, depth + 1)
            return
        o = owners.get(node)
        rows.append(
            ResponsibilityRow(
                "cost_center",
                m.code,
                m.name,
                depth,
                owner=m.owner,
                matched=logins.get(o.matched_user_id) if o and o.matched_user_id else None,
                planner=logins.get(o.user_id) if o and o.user_id else None,
                overridden=bool(o and o.overridden),
                deputies=sorted(deputies[node]),
                capacity_planners=sorted(capacity[node]),
            )
        )

    for root in children[None]:
        visit(root, 0)
    unmatched = sorted(
        {
            info[cc].owner
            for cc, o in owners.items()
            if info[cc].owner is not None and o.matched_user_id is None
        }
    )
    return Responsibilities(rows, unmatched)


def _member(conn: Connection, code: str, *, leaf: bool | None = None) -> int:
    query = select(member.c.member_id).where(member.c.dim_id == COST_CENTER, member.c.code == code)
    if leaf is not None:
        query = query.where(member.c.is_group.is_(not leaf))
    found = conn.execute(query).scalar()
    if found is None:
        raise UserError(f"{'cost center' if leaf else 'node or cost center'} {code} not found")
    return int(found)


def set_owner(engine: Engine, actor: User, cc_code: str, login: str | None) -> None:
    """Override the matched owner: another user, or ``None`` for no owner."""
    actor.require_controller()
    with unit_of_work(engine, actor) as conn:
        cc = _member(conn, cc_code, leaf=True)
        user_id = user_by_login(conn, login).user_id if login is not None else None
        stmt = pg_insert(owner_override).values(
            cost_center_id=cc, user_id=user_id, set_by=actor.login
        )
        conn.execute(
            stmt.on_conflict_do_update(
                index_elements=[owner_override.c.cost_center_id],
                set_={"user_id": user_id, "set_by": actor.login, "set_at": func.now()},
            )
        )


def clear_owner(engine: Engine, actor: User, cc_code: str) -> None:
    """Back to the owner matched from the source."""
    actor.require_controller()
    with unit_of_work(engine, actor) as conn:
        cc = _member(conn, cc_code, leaf=True)
        conn.execute(delete(owner_override).where(owner_override.c.cost_center_id == cc))


def _assignment(
    engine: Engine, actor: User, table: Table, code: str, login: str, add: bool, node_only: bool
) -> None:
    """Add or remove a deputy (``planner_grant``), a capacity planner (``capacity_grant``) or an
    approver (``approver_assignment``)."""
    actor.require_controller()
    with unit_of_work(engine, actor) as conn:
        member_id = _member(conn, code, leaf=False if node_only else None)
        user_id = user_by_login(conn, login).user_id
        if add:
            values: dict[str, object] = {"member_id": member_id, "user_id": user_id}
            if table is planner_grant or table is capacity_grant:
                values["granted_by"] = actor.login
            conn.execute(pg_insert(table).values(**values).on_conflict_do_nothing())
        else:
            conn.execute(
                delete(table).where(table.c.member_id == member_id, table.c.user_id == user_id)
            )


def add_deputy(engine: Engine, actor: User, code: str, login: str) -> None:
    """Planning rights for ``login`` on a cost center, or every cost center below a node."""
    _assignment(engine, actor, planner_grant, code, login, add=True, node_only=False)


def remove_deputy(engine: Engine, actor: User, code: str, login: str) -> None:
    _assignment(engine, actor, planner_grant, code, login, add=False, node_only=False)


def add_capacity_planner(engine: Engine, actor: User, code: str, login: str) -> None:
    """Capacity planning rights for ``login`` on a cost center, or every one below a node: they
    read its sheets and write its capacity (ADR 0013)."""
    _assignment(engine, actor, capacity_grant, code, login, add=True, node_only=False)


def remove_capacity_planner(engine: Engine, actor: User, code: str, login: str) -> None:
    _assignment(engine, actor, capacity_grant, code, login, add=False, node_only=False)


def capacity_rights(conn: Connection, user: User) -> dict[int, bool]:
    """Cost centers whose capacity the user may read, and whether they may write it."""
    rights: dict[int, bool] = {}
    for cc, can_write in conn.execute(
        select(capacity_access.c.cost_center_id, capacity_access.c.can_write).where(
            capacity_access.c.user_id == user.user_id
        )
    ):
        rights[cc] = rights.get(cc, False) or can_write
    return rights


def add_approver(engine: Engine, actor: User, node_code: str, login: str) -> None:
    _assignment(engine, actor, approver_assignment, node_code, login, add=True, node_only=True)


def remove_approver(engine: Engine, actor: User, node_code: str, login: str) -> None:
    _assignment(engine, actor, approver_assignment, node_code, login, add=False, node_only=True)
