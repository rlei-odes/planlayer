"""What capex planning's parts share (ADR 0028): its errors, a round's capex settings, the
installation's investment types, turning the module on and off in setup, and the database's
refusals in the caller's terms."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import Connection, Engine, delete, exists, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError, IntegrityError

from planlayer.access import AccessError, User
from planlayer.domain.capex import CapexError, cash_out_periods
from planlayer.rounds import RoundError, RoundRef, fiscal_periods
from planlayer.sheet import LockedError, locked_error
from planlayer.store.schema import (
    MODULE_CAPEX,
    capex_item,
    capex_setting,
    capex_type,
    round_module,
)
from planlayer.store.uow import system_rights, unit_of_work

RULE_SQLSTATE = "PL003"  # raised by capex's rules triggers
RLS_SQLSTATE = "42501"  # a row-level security policy refused the row


@dataclass
class CapexSettings:
    threshold: Decimal | None  # above it, an investment is better a large project
    cash_out: bool  # planners schedule cash out


def settings_of(conn: Connection, version_id: int) -> CapexSettings | None:
    """The round's capex settings; None: the round doesn't plan capex."""
    row = conn.execute(
        select(capex_setting.c.project_threshold, capex_setting.c.cash_out).where(
            capex_setting.c.version_id == version_id
        )
    ).first()
    return None if row is None else CapexSettings(row.project_threshold, row.cash_out)


def periods_of(conn: Connection, r: RoundRef) -> tuple[list[date], date]:
    """The round's cash-out periods, and the one period after them: the next fiscal year."""
    return (
        cash_out_periods(fiscal_periods(conn, r.fiscal_year), r.grain),
        fiscal_periods(conn, r.fiscal_year + 1)[0],
    )


def switch(conn: Connection, r: RoundRef, actor: User, settings: CapexSettings | None) -> None:
    """Capex planning on with its settings, or off. Off is refused while the round has
    investments: they would be lost."""
    if settings is not None and settings.threshold is not None and settings.threshold <= 0:
        raise RoundError("the threshold for large projects is more than 0")
    current = settings_of(conn, r.version_id)
    if settings is None:
        if current is None:
            return
        # Any investment in the round, not only those the actor's own rights show.
        with system_rights(conn, actor=actor.login):
            found = conn.execute(
                select(exists().where(capex_item.c.version_id == r.version_id))
            ).scalar()
        if found:
            raise RoundError("the round has investments; delete them before turning capex off")
        conn.execute(delete(capex_setting).where(capex_setting.c.version_id == r.version_id))
        conn.execute(
            delete(round_module).where(
                round_module.c.version_id == r.version_id, round_module.c.module == MODULE_CAPEX
            )
        )
        return
    if current is None:
        conn.execute(
            pg_insert(round_module)
            .values(version_id=r.version_id, module=MODULE_CAPEX)
            .on_conflict_do_nothing()
        )
        conn.execute(
            insert(capex_setting).values(
                version_id=r.version_id,
                project_threshold=settings.threshold,
                cash_out=settings.cash_out,
                changed_by=actor.login,
            )
        )
    elif current != settings:
        conn.execute(
            update(capex_setting)
            .where(capex_setting.c.version_id == r.version_id)
            .values(
                project_threshold=settings.threshold,
                cash_out=settings.cash_out,
                changed_by=actor.login,
            )
        )


# --- Investment types ----------------------------------------------------------------------------


@dataclass
class CapexType:
    code: str
    name: str
    asset_account: str | None = None
    useful_life: int | None = None  # years
    active: bool = True
    used: bool = False  # investments of some round have it


def types(engine: Engine, user: User) -> list[CapexType]:
    with unit_of_work(engine, user) as conn:
        return _types(conn)


def _types(conn: Connection) -> list[CapexType]:
    used = select(capex_item.c.item_id).where(capex_item.c.type_id == capex_type.c.type_id)
    return [
        CapexType(r.code, r.name, r.asset_account, r.useful_life, r.active, r.used)
        for r in conn.execute(
            select(
                capex_type.c.code,
                capex_type.c.name,
                capex_type.c.asset_account,
                capex_type.c.useful_life,
                capex_type.c.active,
                exists(used).label("used"),
            ).order_by(capex_type.c.code)
        )
    ]


def save_types(engine: Engine, user: User, wanted: list[CapexType]) -> list[CapexType]:
    """Replace the installation's investment types (controllers). A type that investments use
    stays; it can be made inactive, so no new investment takes it."""
    user.require_controller()
    codes: set[str] = set()
    for t in wanted:
        t.code, t.name = t.code.strip(), t.name.strip()
        t.asset_account = (t.asset_account or "").strip() or None
        if not t.code or not t.name:
            raise CapexError("an investment type needs a code and a name")
        if t.code in codes:
            raise CapexError(f"two investment types have the code {t.code}")
        if t.useful_life is not None and not 1 <= t.useful_life <= 100:
            raise CapexError(f"{t.code}: the useful life is 1 to 100 years")
        codes.add(t.code)
    with unit_of_work(engine, user) as conn:
        current = {t.code: t for t in _types(conn)}
        gone = [c for c in current if c not in codes]
        if in_use := [c for c in gone if current[c].used]:
            raise CapexError(
                f"{', '.join(in_use)} {'is' if len(in_use) == 1 else 'are'} used by investments; "
                "make it inactive instead"
            )
        if gone:
            conn.execute(delete(capex_type).where(capex_type.c.code.in_(gone)))
        for t in wanted:
            values = {
                "name": t.name,
                "asset_account": t.asset_account,
                "useful_life": t.useful_life,
                "active": t.active,
            }
            stmt = pg_insert(capex_type).values(code=t.code, **values)
            conn.execute(stmt.on_conflict_do_update(index_elements=["code"], set_=values))
        return _types(conn)


# --- Refusals ------------------------------------------------------------------------------------


def refusal(e: DBAPIError) -> Exception:
    """The database's refusal of a capex write as the error the API reports."""
    if (locked := locked_error(e)) is not None:
        return locked
    state = getattr(e.orig, "sqlstate", None)
    first = str(e.orig).splitlines()[0]
    if state == RULE_SQLSTATE:
        return CapexError(first)
    if state == RLS_SQLSTATE:
        return AccessError("you may not change investments of this cost center")
    if isinstance(e, IntegrityError):
        return CapexError(first)
    return e


__all__ = ["CapexError", "LockedError"]
