"""The login log (ADR 0030): every attempt, in ``login_event`` and the server log, and the limit
on failed password logins."""

import logging
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import Engine, func, insert, or_, select

from planlayer.store.schema import login_event

log = logging.getLogger("planlayer.login")

WINDOW = timedelta(minutes=15)
MAX_FAILURES_PER_LOGIN = 5
MAX_FAILURES_PER_ADDRESS = 20


@dataclass(frozen=True)
class Client:
    """Where an attempt comes from."""

    address: str | None
    user_agent: str | None


def record(
    engine: Engine,
    client: Client,
    *,
    login: str,
    method: str,
    outcome: str,
    detail: str | None = None,
    user_id: int | None = None,
) -> None:
    login = login.strip().lower()[:200]
    with engine.begin() as conn:
        conn.execute(
            insert(login_event).values(
                login=login,
                method=method,
                outcome=outcome,
                address=client.address,
                user_agent=(client.user_agent or "")[:500] or None,
                detail=detail,
                user_id=user_id,
            )
        )
    level = logging.INFO if outcome == "success" else logging.WARNING
    log.log(
        level,
        "login %s: %s by %s from %s%s",
        outcome,
        method,
        login or "-",
        client.address or "-",
        f" ({detail})" if detail else "",
    )


def blocked(engine: Engine, client: Client, login: str) -> bool:
    """Whether password logins for this login, or from this address, are refused for now."""
    login = login.strip().lower()[:200]
    failed = login_event.c.outcome.in_(("failed", "refused"))
    recent = login_event.c.at > func.now() - WINDOW
    with engine.connect() as conn:
        by_login = conn.execute(
            select(func.count()).where(
                login_event.c.login == login, login_event.c.method == "password", failed, recent
            )
        ).scalar_one()
        by_address = 0
        if client.address:
            by_address = conn.execute(
                select(func.count()).where(
                    login_event.c.address == client.address,
                    or_(login_event.c.method == "password", login_event.c.method == "setup"),
                    failed,
                    recent,
                )
            ).scalar_one()
    return by_login >= MAX_FAILURES_PER_LOGIN or by_address >= MAX_FAILURES_PER_ADDRESS
