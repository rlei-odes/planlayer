"""What a publish run writes of a round's investments (ADR 0028, ADR 0011): one row per
investment with its project, and one row per investment and cash-out period. The scope
"approved" publishes confirmed investments; "all" every one, its confirmation in a column."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from sqlalchemy import Connection, select

from planlayer.rounds import RoundRef
from planlayer.store.schema import (
    NO_ENTITY,
    capex_cash_out,
    capex_item,
    capex_setting,
    capex_type,
    member,
)

Scope = Literal["approved", "all"]


@dataclass(frozen=True)
class InvestmentRow:
    investment_id: int
    name: str
    project_id: int | None
    project_name: str | None
    entity_code: str
    cost_center_code: str
    capex_type_code: str | None
    priority: int
    start_month: date | None
    amount: Decimal | None
    confirmation: str
    confirmed_by: str | None
    confirmed_at: datetime | None

    def fingerprint(self) -> str:
        return "|".join(str(v) for v in self.__dict__.values())


@dataclass(frozen=True)
class CashOutRow:
    investment_id: int
    period: date
    entity_code: str
    cost_center_code: str
    capex_type_code: str | None
    amount: Decimal
    confirmation: str


def published_rows(
    conn: Connection, r: RoundRef, scope: Scope
) -> tuple[list[InvestmentRow], list[CashOutRow]]:
    """The round's investments and cash out in the scope; nothing when it doesn't plan capex."""
    on = conn.execute(
        select(capex_setting.c.cash_out).where(capex_setting.c.version_id == r.version_id)
    ).first()
    if on is None:
        return [], []
    i, cc = capex_item, member.alias("cc")
    items = {
        row.item_id: row
        for row in conn.execute(
            select(
                i.c.item_id,
                i.c.kind,
                i.c.project_id,
                i.c.name,
                i.c.priority,
                i.c.start_month,
                i.c.amount,
                i.c.status,
                i.c.status_by,
                i.c.status_at,
                cc.c.code.label("cc_code"),
                cc.c.entity_code,
                capex_type.c.code.label("type_code"),
            )
            .join(cc, cc.c.member_id == i.c.cost_center_id)
            .outerjoin(capex_type, capex_type.c.type_id == i.c.type_id)
            .where(i.c.version_id == r.version_id)
        )
    }
    out: list[InvestmentRow] = []
    for row in items.values():
        if row.kind != "line":
            continue
        head = items[row.project_id] if row.project_id is not None else row
        if scope == "approved" and head.status != "confirmed":
            continue
        out.append(
            InvestmentRow(
                row.item_id,
                row.name,
                row.project_id,
                head.name if row.project_id is not None else None,
                row.entity_code or NO_ENTITY,
                row.cc_code,
                row.type_code,
                head.priority,
                row.start_month,
                row.amount,
                head.status,
                head.status_by,
                head.status_at,
            )
        )
    out.sort(key=lambda o: o.investment_id)
    cash: list[CashOutRow] = []
    if on.cash_out:
        by_id = {o.investment_id: o for o in out}
        for item_id, period, amount in conn.execute(
            select(capex_cash_out.c.item_id, capex_cash_out.c.period, capex_cash_out.c.amount)
            .where(capex_cash_out.c.item_id.in_(list(by_id)))
            .order_by(capex_cash_out.c.item_id, capex_cash_out.c.period)
        ):
            o = by_id[item_id]
            cash.append(
                CashOutRow(
                    item_id,
                    period,
                    o.entity_code,
                    o.cost_center_code,
                    o.capex_type_code,
                    amount,
                    o.confirmation,
                )
            )
    return out, cash


def capex_types(conn: Connection) -> list[dict[str, object]]:
    """The investment types as ``dim_capex_type`` holds them."""
    return [
        dict(row._mapping)
        for row in conn.execute(
            select(
                capex_type.c.code,
                capex_type.c.name,
                capex_type.c.asset_account,
                capex_type.c.useful_life,
                capex_type.c.active,
            ).order_by(capex_type.c.code)
        )
    ]
