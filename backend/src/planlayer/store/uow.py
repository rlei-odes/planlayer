"""Unit of work: one transaction per request, with the request's context (ADR 0005).

The context is set with ``set_config(name, value, true)``, i.e. ``SET LOCAL``: it ends with the
transaction, so nothing leaks to the next request on a pooled connection. Triggers (audit) and
policies (RLS) read it from there.
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Connection, Engine, func, select


@contextmanager
def unit_of_work(engine: Engine, actor: str, batch_id: str | None = None) -> Iterator[Connection]:
    with engine.begin() as conn:
        conn.execute(select(func.set_config("app.actor", actor, True)))
        conn.execute(select(func.set_config("app.batch_id", batch_id or uuid.uuid4().hex, True)))
        yield conn
