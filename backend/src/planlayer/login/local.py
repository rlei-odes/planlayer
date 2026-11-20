"""Local accounts (ADR 0030): the first run's admin, created with the setup code from the server
log, and passwords set from the command line."""

import hashlib
import hmac
import logging
import secrets

from sqlalchemy import ColumnElement, Connection, Engine, Exists, exists, insert, select, update

from planlayer import access
from planlayer.login import passwords
from planlayer.login.settings import LoginSettings
from planlayer.store.schema import app_user, login_setting
from planlayer.store.uow import unit_of_work

log = logging.getLogger("planlayer.login")

_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I


class SetupError(ValueError):
    """The first run's admin can't be created as asked."""


def _active_admin(condition: ColumnElement[bool]) -> Exists:
    return exists().where(
        app_user.c.is_admin.is_(True),
        app_user.c.disabled.is_(False),
        app_user.c.is_system.is_(False),
        condition,
    )


def _needed(conn: Connection, settings: LoginSettings) -> bool:
    if conn.execute(select(_active_admin(app_user.c.password_hash.is_not(None)))).scalar():
        return False
    if settings.method == "none":
        return True
    # A provider without an admin group gives nobody the admin role.
    if settings.admin_group:
        return False
    return not conn.execute(select(_active_admin(app_user.c.identity.is_not(None)))).scalar()


def needed(engine: Engine, settings: LoginSettings) -> bool:
    """Whether no admin can log in, so the login page offers the first run's admin account."""
    with engine.connect() as conn:
        return _needed(conn, settings)


def _hash(code: str) -> str:
    return hashlib.sha256(code.replace("-", "").upper().encode()).hexdigest()


def ensure_code(engine: Engine, settings: LoginSettings) -> None:
    """While the first run is needed, keep a setup code and write it to the server log. A code
    already issued stays valid; only its hash is stored."""
    with engine.begin() as conn:
        if not _needed(conn, settings):
            return
        if conn.execute(select(login_setting.c.setup_code_hash)).scalar() is not None:
            return
        raw = "".join(secrets.choice(_ALPHABET) for _ in range(16))
        code = "-".join(raw[i : i + 4] for i in range(0, 16, 4))
        conn.execute(update(login_setting).values(setup_code_hash=_hash(code)))
    log.warning("No admin can log in yet. Setup code for the first admin account: %s", code)


def create_first_admin(
    engine: Engine,
    settings: LoginSettings,
    *,
    code: str,
    login: str,
    display_name: str,
    email: str | None,
    password: str,
) -> str:
    """The first run's admin, a local account; the setup code is used up."""
    login = login.strip().lower()
    if not login or login == access.SYSTEM_LOGIN or any(c.isspace() for c in login):
        raise SetupError(f"login {login!r} is not allowed")
    if not display_name.strip():
        raise SetupError("a name is needed")
    try:
        passwords.check_new(password)
    except passwords.PasswordError as e:
        raise SetupError(str(e)) from None
    password_hash = passwords.hash_password(password)
    system = access.system_user(engine)
    with unit_of_work(engine, system) as conn:
        stored = conn.execute(select(login_setting.c.setup_code_hash).with_for_update()).scalar()
        if not _needed(conn, settings):
            raise SetupError("an admin can log in already")
        if stored is None or not hmac.compare_digest(stored, _hash(code)):
            raise SetupError("the setup code is wrong; it is in the server log")
        if conn.execute(select(exists().where(app_user.c.login == login))).scalar():
            raise SetupError(f"user {login} exists")
        conn.execute(
            insert(app_user).values(
                login=login,
                display_name=display_name.strip(),
                email=(email or "").strip() or None,
                is_admin=True,
                password_hash=password_hash,
            )
        )
        conn.execute(update(login_setting).values(setup_code_hash=None))
    log.warning("First admin account %s created", login)
    return login


def set_password(engine: Engine, login: str, password: str) -> None:
    """A local password for an active user, from the command line: the way back in."""
    passwords.check_new(password)
    password_hash = passwords.hash_password(password)
    with unit_of_work(engine, access.system_user(engine)) as conn:
        done = conn.execute(
            update(app_user)
            .where(
                app_user.c.login == login.strip().lower(),
                app_user.c.is_system.is_(False),
                app_user.c.disabled.is_(False),
            )
            .values(password_hash=password_hash)
            .returning(app_user.c.user_id)
        ).scalar()
    if done is None:
        raise access.UserError(f"no active user {login}")


def check_password(engine: Engine, login: str, password: str) -> str | None:
    """The login of the local account this password opens, or None."""
    with engine.connect() as conn:
        stored = conn.execute(
            select(app_user.c.password_hash).where(
                app_user.c.login == login.strip().lower(),
                app_user.c.is_system.is_(False),
                app_user.c.disabled.is_(False),
            )
        ).scalar()
    if stored is None or not password:
        passwords.verify_nothing(password)
        return None
    return login.strip().lower() if passwords.verify(password, stored) else None
