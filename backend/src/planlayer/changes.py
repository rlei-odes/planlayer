"""The change log of a round for controllers: every change of plan values, planned hours and
FTE or headcount, one entry per change batch, read from the audit's change logs (ADR 0005).

A batch is what one request wrote: a planner's edit, a paste, an undo, a round's carry-over, a
recalculation. Batches are listed newest first. A batch whose cells are all written by the app
from other inputs (personnel costs, cost forwarding, capacity credits) or by the system user is
*calculated*; one with any typed cell is *entered*. Row-level security on the change logs
narrows what a controller sees to the cost centers whose values they read, with small groups of
personnel costs hidden as on the sheet (ADR 0022)."""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from sqlalchemy import (
    Engine,
    Integer,
    Subquery,
    and_,
    case,
    func,
    literal,
    select,
    tuple_,
    union_all,
)

from planlayer.access import User
from planlayer.rounds import get_round
from planlayer.store.schema import (
    COST_CENTER,
    FTE,
    app_user,
    capacity_change_log,
    change_log,
    credit_account,
    forwarding,
    member,
    pay_account,
    workforce_change_log,
)
from planlayer.store.uow import SYSTEM_LOGIN, unit_of_work

Sheet = Literal["costs", "capacity", "workforce"]
Source = Literal["plan", "personnel", "forwarding", "credit"]
Kind = Literal["all", "entered", "calculated"]
Unit = Literal["amount", "hours", "fte", "headcount"]

PAGE = 50
DETAIL_LINES = 500
CALCULATED: tuple[Source, ...] = ("personnel", "forwarding", "credit")


@dataclass
class Person:
    login: str
    name: str


@dataclass
class CostCenter:
    code: str
    name: str


@dataclass
class Batch:
    batch_id: str
    at: datetime
    actor: Person
    sheets: list[Sheet]
    sources: list[Source]
    cost_centers: list[CostCenter]  # the first three, by code
    cost_center_count: int
    cells: int
    cleared: int  # cells blanked
    calculated: bool


@dataclass
class Page:
    batches: list[Batch]
    counts: dict[Kind, int]
    cursor: str | None  # the next page's; None: the last page
    users: list[Person]  # who changed anything in the round, for the filter
    cost_centers: list[CostCenter]  # where anything changed, for the filter


@dataclass
class Line:
    sheet: Sheet
    cost_center: CostCenter
    code: str
    name: str
    unit: Unit
    source: Source
    months: list[date]  # the months changed
    # Amounts and hours: the changed months' sum; FTE and headcount: the last changed month.
    before: Decimal | None
    after: Decimal | None


@dataclass
class Detail:
    batch_id: str
    lines: list[Line]
    total_lines: int  # more than ``lines`` when the batch is too large to list whole


SHEETS: tuple[Sheet, ...] = ("costs", "capacity", "workforce")
SOURCES: tuple[Source, ...] = ("plan", "personnel", "forwarding", "credit")


def _rows(version_id: int) -> Subquery:
    """Every logged cell of the round as one relation: batch, time, actor, sheet, cost center,
    operation, source, line and unit."""
    c, h, w = change_log, capacity_change_log, workforce_change_log
    forwarded = (
        select(forwarding.c.charge_account_id)
        .where(forwarding.c.version_id == version_id)
        .union(select(forwarding.c.credit_account_id).where(forwarding.c.version_id == version_id))
    )
    paid = select(pay_account.c.account_id).where(pay_account.c.version_id == version_id)
    source = case(
        (c.c.dim_account.in_(select(credit_account.c.account_id)), "credit"),
        (c.c.dim_account.in_(forwarded), "forwarding"),
        (c.c.dim_account.in_(paid), "personnel"),
        else_="plan",
    )
    none = literal(None, Integer)
    return union_all(
        select(
            c.c.id,
            c.c.batch_id,
            c.c.at,
            c.c.actor,
            literal("costs").label("sheet"),
            c.c.dim_cost_center.label("cost_center_id"),
            c.c.op,
            source.label("source"),
            c.c.dim_account.label("line_a"),
            none.label("line_b"),
            literal("amount").label("unit"),
            c.c.period,
            c.c.old_amount,
            c.c.new_amount,
        ).where(c.c.version_id == version_id),
        select(
            h.c.id,
            h.c.batch_id,
            h.c.at,
            h.c.actor,
            literal("capacity"),
            h.c.dim_cost_center,
            h.c.op,
            literal("plan"),
            h.c.dim_work_center,
            h.c.dim_activity_type,
            literal("hours"),
            h.c.period,
            h.c.old_amount,
            h.c.new_amount,
        ).where(h.c.version_id == version_id),
        select(
            w.c.id,
            w.c.batch_id,
            w.c.at,
            w.c.actor,
            literal("workforce"),
            w.c.dim_cost_center,
            w.c.op,
            literal("plan"),
            w.c.dim_staff_type,
            w.c.dim_staff_category,
            case((w.c.measure_id == FTE, "fte"), else_="headcount"),
            w.c.period,
            w.c.old_amount,
            w.c.new_amount,
        ).where(w.c.version_id == version_id),
    ).subquery("logged")


def _cursor(at: datetime, batch_id: str) -> str:
    return f"{at.isoformat()}|{batch_id}"


def history(
    engine: Engine,
    user: User,
    code: str,
    *,
    kind: Kind = "all",
    sheet: Sheet | None = None,
    actor: str | None = None,
    cost_center: str | None = None,
    cursor: str | None = None,
) -> Page:
    """A page of the round's change batches, newest first, with the filters applied."""
    user.require_controller()
    with unit_of_work(engine, user) as conn:
        r = get_round(conn, code)
        rows = _rows(r.version_id)
        cc = member.alias("cc")
        logged = rows.c
        where = []
        if sheet is not None:
            where.append(logged.sheet == sheet)
        if actor is not None:
            where.append(logged.actor == actor)
        if cost_center is not None:
            where.append(cc.c.code == cost_center)
        entered = func.bool_or(and_(logged.source == "plan", logged.actor != SYSTEM_LOGIN)).label(
            "entered"
        )
        batches = (
            select(
                logged.batch_id,
                func.max(logged.at).label("at"),
                func.min(logged.actor).label("actor"),
                func.array_agg(func.distinct(logged.sheet)).label("sheets"),
                func.array_agg(func.distinct(logged.source)).label("sources"),
                func.array_agg(func.distinct(cc.c.code)).label("codes"),
                func.count().label("cells"),
                func.count().filter(logged.op == "DELETE").label("cleared"),
                entered,
            )
            .select_from(rows)
            .join(cc, cc.c.member_id == logged.cost_center_id)
            .where(*where)
            .group_by(logged.batch_id)
            .subquery("batches")
        )
        b = batches.c
        counts_row = conn.execute(
            select(
                func.count(),
                func.count().filter(b.entered),
                func.count().filter(~b.entered),
            )
        ).one()
        counts: dict[Kind, int] = {
            "all": counts_row[0],
            "entered": counts_row[1],
            "calculated": counts_row[2],
        }
        query = select(batches)
        if kind == "entered":
            query = query.where(b.entered)
        elif kind == "calculated":
            query = query.where(~b.entered)
        if cursor:
            at_text, _, last_id = cursor.partition("|")
            query = query.where(
                tuple_(b.at, b.batch_id) < tuple_(literal(datetime.fromisoformat(at_text)), last_id)
            )
        found = conn.execute(query.order_by(b.at.desc(), b.batch_id.desc()).limit(PAGE + 1)).all()
        more = len(found) > PAGE
        found = found[:PAGE]
        names = dict(
            conn.execute(
                select(member.c.code, member.c.name).where(
                    member.c.dim_id == COST_CENTER,
                    member.c.code.in_({c for row in found for c in row.codes}),
                )
            ).all()
        )
        people = dict(conn.execute(select(app_user.c.login, app_user.c.display_name)).all())
        users = sorted(
            {
                a
                for (a,) in conn.execute(select(func.distinct(logged.actor)).select_from(rows))
                if a
            },
            key=lambda a: str(people.get(a) or a).lower(),
        )
        changed = conn.execute(
            select(func.distinct(cc.c.code), cc.c.name)
            .select_from(rows)
            .join(cc, cc.c.member_id == logged.cost_center_id)
            .order_by(cc.c.code)
        ).all()
    return Page(
        [
            Batch(
                row.batch_id,
                row.at,
                Person(row.actor or SYSTEM_LOGIN, people.get(row.actor, row.actor or "System")),
                sorted(row.sheets, key=SHEETS.index),
                sorted(row.sources, key=SOURCES.index),
                [CostCenter(c, names.get(c, c)) for c in sorted(row.codes)[:3]],
                len(row.codes),
                row.cells,
                row.cleared,
                not row.entered,
            )
            for row in found
        ],
        counts,
        _cursor(found[-1].at, found[-1].batch_id) if more else None,
        [Person(a, people.get(a, a)) for a in users],
        [CostCenter(c, n) for c, n in changed],
    )


def batch_detail(
    engine: Engine, user: User, code: str, batch_id: str, cost_center: str | None = None
) -> Detail:
    """What one batch changed, per cost center and line."""
    user.require_controller()
    with unit_of_work(engine, user) as conn:
        r = get_round(conn, code)
        rows = _rows(r.version_id)
        logged = rows.c
        cc, a, b = member.alias("cc"), member.alias("a"), member.alias("b")
        query = (
            select(
                logged.sheet,
                cc.c.code,
                cc.c.name,
                a.c.code,
                a.c.name,
                b.c.code,
                b.c.name,
                logged.unit,
                logged.source,
                logged.period,
                logged.old_amount,
                logged.new_amount,
            )
            .select_from(rows)
            .join(cc, cc.c.member_id == logged.cost_center_id)
            .join(a, a.c.member_id == logged.line_a)
            .outerjoin(b, b.c.member_id == logged.line_b)
            .where(logged.batch_id == batch_id)
            .order_by(logged.id)
        )
        if cost_center is not None:
            query = query.where(cc.c.code == cost_center)
        cells = conn.execute(query).all()
    # A cell may be written twice in a batch (a value, then a follow-up): first old, last new.
    lines: dict[tuple[str, int, str, str], _Line] = {}
    for (
        sheet,
        cc_code,
        cc_name,
        a_code,
        a_name,
        b_code,
        b_name,
        unit,
        source,
        period,
        old,
        new,
    ) in cells:
        code = a_code if b_code is None else f"{a_code} · {b_code}"
        name = a_name if b_name is None else f"{a_name} · {b_name}"
        key = (cc_code, SHEETS.index(sheet), code, unit)
        if key not in lines:
            lines[key] = _Line(
                Line(sheet, CostCenter(cc_code, cc_name), code, name, unit, source, [], None, None)
            )
        lines[key].add(period, old, new)
    out = [lines[k].done() for k in sorted(lines)]
    return Detail(batch_id, out[:DETAIL_LINES], len(out))


@dataclass
class _Line:
    line: Line
    cells: dict[date, tuple[Decimal | None, Decimal | None]] = field(default_factory=dict)

    def add(self, month: date, old: Decimal | None, new: Decimal | None) -> None:
        first = self.cells.get(month, (old, new))[0]
        self.cells[month] = (first, new)

    def done(self) -> Line:
        line = self.line
        line.months = sorted(self.cells)
        if line.unit in ("fte", "headcount"):
            line.before, line.after = self.cells[line.months[-1]]
        else:
            line.before = _sum(o for o, _ in self.cells.values())
            line.after = _sum(n for _, n in self.cells.values())
        return line


def _sum(values: Iterable[Decimal | None]) -> Decimal | None:
    """Blank when every value is blank; otherwise the sum with blanks as nothing."""
    known = [v for v in values if v is not None]
    return sum(known, Decimal(0)) if known else None
