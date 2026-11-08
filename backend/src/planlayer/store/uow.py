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

from sqlalchemy import Connection, Engine, func, select


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
