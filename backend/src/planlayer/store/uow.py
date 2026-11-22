"""Unit of work: one transaction per request, with the request's context (ADR 0005).

The context is set with ``set_config(name, value, true)``, i.e. ``SET LOCAL``: it ends with the
transaction, so nothing leaks to the next request on a pooled connection. Triggers (audit) and
policies (RLS) read it from there. Reads go through a unit of work too: without a user in the
context, row-level security shows no plan values or actuals.
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

import psycopg
from sqlalchemy import Connection, Engine, func, select

from planlayer.store.schema import app_user

SYSTEM_LOGIN = "system"


@dataclass(frozen=True)
class Actor:
    """Who a unit of work acts for: a logged-in user, or the system user of jobs."""

    user_id: int
    login: str


@contextmanager
def unit_of_work(engine: Engine, actor: Actor, batch_id: str | None = None) -> Iterator[Connection]:
    with engine.begin() as conn:
        for name, value in (
            ("app.user_id", str(actor.user_id)),
            ("app.actor", actor.login),
            ("app.batch_id", batch_id or uuid.uuid4().hex),
        ):
            conn.execute(select(func.set_config(name, value, True)))
        yield conn


@contextmanager
def system_rights(conn: Connection, *, actor: str | None = None) -> Iterator[None]:
    """Row-level security of the system user for a while, in the same transaction and change
    batch: for writes a request sets off in cost centers its user may not write (a forwarding's
    receivers, a transfer's sending side). With ``actor``, the audit keeps naming that actor."""
    before = {
        name: conn.execute(select(func.current_setting(name, True))).scalar()
        for name in ("app.user_id", "app.actor")
    }
    system_id = conn.execute(
        select(app_user.c.user_id).where(app_user.c.login == SYSTEM_LOGIN)
    ).scalar_one()
    for name, value in (("app.user_id", str(system_id)), ("app.actor", actor or SYSTEM_LOGIN)):
        conn.execute(select(func.set_config(name, value, True)))

    def restore() -> None:
        for name, old in before.items():
            conn.execute(select(func.set_config(name, old or "", True)))

    try:
        yield
    except BaseException:
        # A failed transaction takes no more statements and rolls back, the settings with it;
        # restoring there would hide the error with "current transaction is aborted".
        if not _aborted(conn):
            restore()
        raise
    restore()


def _aborted(conn: Connection) -> bool:
    raw = conn.connection.dbapi_connection
    return (
        isinstance(raw, psycopg.Connection)
        and raw.info.transaction_status == psycopg.pq.TransactionStatus.INERROR
    )
