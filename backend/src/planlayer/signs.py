"""Published signs (ADR 0019): each account's role, and the installation's sign convention for
published amounts, which the admin sets on the settings page."""

from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy import Connection, Engine, select, update

from planlayer.access import User
from planlayer.domain.signs import CONVENTIONS, Convention, Role, role_of
from planlayer.store.schema import (
    ACCOUNT,
    credit_account,
    forwarding_account,
    installation,
    member,
)
from planlayer.store.uow import unit_of_work


class SignsError(ValueError):
    """A sign setting that can't be made as asked."""


def account_roles(conn: Connection) -> dict[str, Role]:
    """Each leaf account's role, by code: credited by capacity planning or by a forwarding in any
    round, revenue, or cost."""
    credits = set(conn.execute(select(credit_account.c.account_id)).scalars()) | set(
        conn.execute(
            select(forwarding_account.c.account_id).where(forwarding_account.c.side == "credit")
        ).scalars()
    )
    return {
        code: role_of(account_type, mid in credits)
        for mid, code, account_type in conn.execute(
            select(member.c.member_id, member.c.code, member.c.account_type).where(
                member.c.dim_id == ACCOUNT, member.c.is_group.is_(False)
            )
        )
    }


def convention(conn: Connection) -> Convention:
    value: Convention = conn.execute(select(installation.c.published_signs)).scalar_one()
    return value


@dataclass
class SignSettings:
    convention: Convention
    roles: dict[Role, int]  # leaf accounts per role
    untyped: list[tuple[str, str]] = field(default_factory=list)  # accounts without a type
    credits: list[tuple[str, str]] = field(default_factory=list)  # accounts with the credit role


def settings(engine: Engine, user: User) -> SignSettings:
    user.require_admin()
    with unit_of_work(engine, user) as conn:
        roles = account_roles(conn)
        names = {
            code: (name, account_type)
            for code, name, account_type in conn.execute(
                select(member.c.code, member.c.name, member.c.account_type).where(
                    member.c.dim_id == ACCOUNT, member.c.is_group.is_(False)
                )
            )
        }
        counts = Counter(roles.values())
        return SignSettings(
            convention(conn),
            {role: counts.get(role, 0) for role in ("cost", "revenue", "credit")},
            sorted((c, names[c][0]) for c, r in roles.items() if r != "credit" and not names[c][1]),
            sorted((c, names[c][0]) for c, r in roles.items() if r == "credit"),
        )


def set_convention(engine: Engine, user: User, value: str) -> None:
    """The convention of ``amount_signed``; it applies from the next publish run."""
    user.require_admin()
    if value not in CONVENTIONS:
        raise SignsError(f"unknown sign convention {value}")
    with unit_of_work(engine, user) as conn:
        conn.execute(update(installation).values(published_signs=value))
