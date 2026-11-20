"""From a provider's identity to a Planlayer user (ADR 0030).

A user who logged in through the provider once is found by the identity's key from then on. At
a first login the identity is matched by login, then by a verified e-mail; without a match a user
without roles is created. Role groups set their roles at every login through the provider.
"""

from dataclasses import dataclass, field

from sqlalchemy import Connection, Engine, func, insert, select, update

from planlayer import access
from planlayer.login.settings import ROLES, LoginSettings
from planlayer.store.schema import app_user
from planlayer.store.uow import unit_of_work


class Refused(Exception):
    """The provider knows the person, but Planlayer doesn't let them in; the message says why."""


@dataclass(frozen=True)
class Identity:
    key: str  # oidc:<issuer> <sub>, or ldap:<dn>
    login: str
    display_name: str
    email: str | None = None
    email_verified: bool = True
    groups: frozenset[str] = field(default_factory=frozenset)


def normalize_group(name: str) -> str:
    """Case-insensitive, without a Keycloak path's leading slash."""
    return name.strip().lstrip("/").casefold()


def group_names(raw: list[str]) -> frozenset[str]:
    """A provider's groups as compared: each one, and for an LDAP DN its first RDN value too
    (``cn=planlayer-admins,ou=groups,…`` also matches ``planlayer-admins``)."""
    names: set[str] = set()
    for g in raw:
        n = normalize_group(g)
        if not n:
            continue
        names.add(n)
        first = n.split(",", 1)[0]
        if "=" in first and "," in n:
            names.add(first.split("=", 1)[1].strip())
    return frozenset(names)


def in_group(identity: Identity, group: str) -> bool:
    return normalize_group(group) in identity.groups


def roles_of(settings: LoginSettings, identity: Identity) -> dict[str, bool]:
    """The roles the role groups set, by column; roles without a group are left out."""
    return {
        f"is_{role}": in_group(identity, settings.role_group(role))
        for role in ROLES
        if settings.role_group(role)
    }


def clean_login(value: str) -> str:
    login = value.strip().lower()
    if not login or len(login) > 100 or any(c.isspace() for c in login):
        raise Refused(f"the provider's login {value[:100]!r} can't be a Planlayer login")
    if login == access.SYSTEM_LOGIN:
        raise Refused("the provider's login is reserved")
    return login


def _find(conn: Connection, identity: Identity, login: str) -> int | None:
    by_key: int | None = conn.execute(
        select(app_user.c.user_id).where(app_user.c.identity == identity.key)
    ).scalar()
    if by_key is not None:
        return by_key
    candidates: set[int] = set()
    by_login = conn.execute(
        select(app_user.c.user_id).where(app_user.c.login == login, app_user.c.is_system.is_(False))
    ).scalar()
    if by_login is not None:
        candidates.add(by_login)
    if identity.email and identity.email_verified:
        rows = conn.execute(
            select(app_user.c.user_id).where(
                func.lower(app_user.c.email) == identity.email.strip().lower(),
                app_user.c.is_system.is_(False),
            )
        ).scalars()
        candidates.update(rows)
    if len(candidates) > 1:
        raise Refused("the login and the e-mail match two different users")
    if not candidates:
        return None
    user_id = candidates.pop()
    linked = conn.execute(select(app_user.c.identity).where(app_user.c.user_id == user_id)).scalar()
    if linked is not None:
        raise Refused("the matching user logs in with another identity")
    return user_id


def resolve(engine: Engine, settings: LoginSettings, identity: Identity) -> str:
    """The login of the user this identity logs in as; creates the user at a first login."""
    if settings.allowed_group and not in_group(identity, settings.allowed_group):
        raise Refused("not in the allowed group")
    login = clean_login(identity.login)
    roles = roles_of(settings, identity)
    # Only a verified e-mail is kept: owners are matched by e-mail (ADR 0009), so an address
    # the person typed themselves would hand them someone else's cost centers.
    email = identity.email if identity.email_verified else None
    system = access.system_user(engine)
    with unit_of_work(engine, system) as conn:
        user_id = _find(conn, identity, login)
        if user_id is None:
            created: str = conn.execute(
                insert(app_user)
                .values(
                    login=login,
                    display_name=identity.display_name or login,
                    email=email,
                    identity=identity.key,
                    **roles,
                )
                .returning(app_user.c.login)
            ).scalar_one()
            return created
        row = conn.execute(
            select(app_user.c.disabled, app_user.c.email).where(app_user.c.user_id == user_id)
        ).one()
        if row.disabled:
            raise Refused("the user is disabled")
        changes: dict[str, object] = {"identity": identity.key, **roles}
        if row.email is None and email:
            changes["email"] = email
        found: str = conn.execute(
            update(app_user)
            .where(app_user.c.user_id == user_id)
            .values(**changes)
            .returning(app_user.c.login)
        ).scalar_one()
        return found


def preview(engine: Engine, settings: LoginSettings, identity: Identity) -> str:
    """Who this identity would log in as, without writing: for the Settings page's test."""
    if settings.allowed_group and not in_group(identity, settings.allowed_group):
        raise Refused(f"not in the allowed group {settings.allowed_group!r}")
    login = clean_login(identity.login)
    with engine.connect() as conn:
        user_id = _find(conn, identity, login)
        if user_id is None:
            return f"a new user {login}"
        row = conn.execute(
            select(app_user.c.login, app_user.c.disabled).where(app_user.c.user_id == user_id)
        ).one()
    if row.disabled:
        raise Refused(f"the matching user {row.login} is disabled")
    return f"the existing user {row.login}"
