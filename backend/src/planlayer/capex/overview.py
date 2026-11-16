"""The overview of a round's investments (ADR 0028): their amounts by priority and confirmation,
rolled up the cost center hierarchy and by investment type, and their cash out by period. Over
what the user reads, so an approver sees their area and a controller the whole round."""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import Engine, select

from planlayer.access import User, cost_center_rights
from planlayer.capex.common import periods_of, settings_of
from planlayer.capex.items import Item, _confirms, _items
from planlayer.domain.capex import PRIORITIES, STATUSES, Status
from planlayer.rounds import RoundRef, get_round
from planlayer.store.schema import COST_CENTER, H_COST_CENTER, capex_type, hier_edge, member
from planlayer.store.uow import unit_of_work

ZERO = Decimal(0)


@dataclass
class Sums:
    """Amounts by priority (1 to 3) and confirmation, with the number of investments."""

    amounts: dict[tuple[int, Status], Decimal] = field(default_factory=dict)
    count: int = 0

    def add(self, priority: int, status: Status, amount: Decimal | None) -> None:
        key = (priority, status)
        self.amounts[key] = self.amounts.get(key, ZERO) + (amount or ZERO)
        self.count += 1

    def merge(self, other: "Sums") -> None:
        for key, value in other.amounts.items():
            self.amounts[key] = self.amounts.get(key, ZERO) + value
        self.count += other.count

    def get(self, priority: int, status: Status) -> Decimal:
        return self.amounts.get((priority, status), ZERO)


@dataclass
class AreaRow:
    kind: str  # node / cost_center
    code: str
    name: str
    depth: int
    sums: Sums


@dataclass
class TypeRow:
    code: str | None  # None: investments without a type
    name: str
    sums: Sums


@dataclass
class CashOutRow:
    period: date | None  # None: not scheduled yet
    by_status: dict[Status, Decimal]


@dataclass
class Overview:
    round: RoundRef
    on: bool
    cash_out: bool
    areas: list[AreaRow]
    types: list[TypeRow]
    total: Sums
    schedule: list[CashOutRow]  # empty: the round doesn't plan cash out
    next_year: date
    waiting: int  # open investments the user confirms


def overview(engine: Engine, user: User, code: str) -> Overview:
    with unit_of_work(engine, user) as conn:
        r = get_round(conn, code)
        settings = settings_of(conn, r.version_id)
        periods, next_year = periods_of(conn, r)
        if settings is None:
            return Overview(r, False, False, [], [], Sums(), [], next_year, 0)
        rights = cost_center_rights(conn, user)
        items = _items(conn, r.version_id, rights, _confirms(conn, user), settings)
        info = {
            m.member_id: (m.code, m.name)
            for m in conn.execute(
                select(member.c.member_id, member.c.code, member.c.name).where(
                    member.c.dim_id == COST_CENTER
                )
            )
        }
        by_code = {code_: m for m, (code_, _) in info.items()}
        children: dict[int | None, list[int]] = defaultdict(list)
        for m, parent in conn.execute(
            select(hier_edge.c.member_id, hier_edge.c.parent_id)
            .join(member, member.c.member_id == hier_edge.c.member_id)
            .where(hier_edge.c.hier_id == H_COST_CENTER)
            .order_by(hier_edge.c.sort_order, member.c.code)
        ):
            children[parent].append(m)
        type_names = dict(conn.execute(select(capex_type.c.code, capex_type.c.name)).all())

    # Lines count; a project counts through its lines, with its priority and confirmation.
    lines = [i for i in items if i.kind == "line"]
    per_cc: dict[int, Sums] = defaultdict(Sums)
    per_type: dict[str | None, Sums] = defaultdict(Sums)
    total = Sums()
    for i in lines:
        for sums in (per_cc[by_code[i.cost_center_code]], per_type[i.type_code], total):
            sums.add(i.priority, i.status, i.amount)

    areas: list[AreaRow] = []

    def visit(node: int, depth: int) -> Sums:
        code_, name = info[node]
        if node not in children:
            own = per_cc.get(node)
            if own is None:
                return Sums()
            areas.append(AreaRow("cost_center", code_, name, depth, own))
            return own
        at = len(areas)
        areas.append(AreaRow("node", code_, name, depth, Sums()))
        sums = Sums()
        for child in children[node]:
            sums.merge(visit(child, depth + 1))
        if sums.count == 0:
            del areas[at:]
        else:
            areas[at].sums = sums
        return sums

    for root in children[None]:
        visit(root, 0)

    types = [
        TypeRow(t, type_names.get(t, t) if t else "No type", s)
        for t, s in sorted(per_type.items(), key=lambda kv: (kv[0] is None, kv[0] or ""))
    ]
    return Overview(
        r,
        True,
        settings.cash_out,
        areas,
        types,
        total,
        _schedule(lines, [*periods, next_year]) if settings.cash_out else [],
        next_year,
        sum(1 for i in items if i.project_id is None and i.can_confirm and i.status == "open"),
    )


def _schedule(lines: list[Item], periods: list[date]) -> list[CashOutRow]:
    """Cash out per period by confirmation, and what isn't scheduled yet."""
    rows = {p: dict.fromkeys(STATUSES, ZERO) for p in periods}
    rest = dict.fromkeys(STATUSES, ZERO)
    for i in lines:
        for period, amount in i.cash_out.items():
            if period in rows:
                rows[period][i.status] += amount
        rest[i.status] += (i.amount or ZERO) - i.scheduled
    return [CashOutRow(p, v) for p, v in rows.items()] + [CashOutRow(None, rest)]


__all__ = ["PRIORITIES", "Overview", "overview"]
