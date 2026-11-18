"""Publishing a round's plan values (ADR 0011): files, preview and run into ``planlayer_out``.

A run replaces the round's rows in the output with exactly the selected scope: the approved
cost centers only, or the whole round. The preview compares those rows by key with what the
output holds and lists rows added, changed and removed; it carries a fingerprint of both sides,
and a run refuses when the fingerprint no longer matches, so it does exactly what was previewed.

The output may live in another database, so a run is two transactions: the output is replaced,
then the run is recorded in the app store. Repeating a run is harmless. Values are published
natural-positive in ``amount``, and in ``amount_signed`` with the installation's sign convention
for each account's role (ADR 0019).

A forecast's closed months go out as its actuals at the moment of the run, marked ``origin``
``actual``; its own values are ``plan`` (ADR 0015). The sum over a version is its full year.
"""

import csv
import hashlib
import io
from collections import Counter, defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Literal

import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import (
    BigInteger,
    Column,
    Connection,
    Date,
    DateTime,
    Engine,
    Integer,
    Numeric,
    SmallInteger,
    Table,
    delete,
    func,
    insert,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert

from planlayer.access import User
from planlayer.capex.published import CashOutRow, InvestmentRow, capex_types, published_rows
from planlayer.domain.signs import signed
from planlayer.rounds import RoundRef, closed_periods, get_round
from planlayer.signs import account_roles, convention
from planlayer.store.output import (
    LEVELS,
    dim_account,
    dim_activity_type,
    dim_capex_type,
    dim_cost_center,
    dim_staff_category,
    dim_staff_type,
    dim_version,
    dim_work_center,
    ensure,
    fact_plan_capacity,
    fact_plan_capex,
    fact_plan_cash_out,
    fact_plan_cost_center,
    fact_plan_workforce,
)
from planlayer.store.schema import (
    ACCOUNT,
    ACTIVITY_TYPE,
    AMOUNT,
    BASE_LAYER,
    COST_CENTER,
    FTE,
    H_ACCOUNT,
    H_COST_CENTER,
    HEADCOUNT,
    HOURS,
    MODULE_CAPACITY,
    MODULE_COST_CENTER,
    MODULE_WORKFORCE,
    STAFF_CATEGORY,
    STAFF_TYPE,
    WORK_CENTER,
    fact_capacity,
    fact_cost_center,
    fact_workforce,
    hier_edge,
    member,
    publish_run,
    round_member,
    round_module,
    version,
    workflow_status,
)
from planlayer.store.uow import unit_of_work

Scope = Literal["approved", "all"]
Key = tuple[date, str, str, str, str]  # period, entity, cost center, account, measure
MEASURE = "amount"
COLUMNS = (
    "version_code",
    "fiscal_year",
    "period",
    "entity_code",
    "cost_center_code",
    "account_code",
    "measure",
    "amount",
    "status",
    "origin",
    "amount_signed",
)
Origin = Literal["plan", "actual"]


class PublishError(ValueError):
    """A publish that can't run as asked; nothing was written."""


@dataclass(frozen=True)
class OutRow:
    key: Key
    amount: Decimal
    status: str
    origin: Origin = "plan"
    signed: Decimal | None = None  # with the sign convention (ADR 0019)


def plan_rows(engine: Engine, user: User, code: str, scope: Scope) -> tuple[RoundRef, list[OutRow]]:
    """The round's plan values in the scope, as the output will hold them."""
    user.require_controller()
    f, e, cc, acc = fact_cost_center, member.alias("e"), member.alias("cc"), member.alias("acc")
    with unit_of_work(engine, user) as conn:
        r = get_round(conn, code)
        # No total over fewer people than the minimum leaves Planlayer (ADR 0022). Imported
        # here: personnel costs build on planning.
        from planlayer.personnel import small_groups

        small = small_groups(conn, r.version_id)
        if small:
            raise PublishError(
                f"{len(small)} cost center(s) have personnel costs of fewer people than the "
                "minimum; HR charges them to other cost centers before the round is published"
            )
        status = func.coalesce(workflow_status.c.status, "not_started")
        query = (
            select(f.c.period, e.c.code, cc.c.code, acc.c.code, f.c.amount, status)
            .join(e, e.c.member_id == f.c.dim_entity)
            .join(cc, cc.c.member_id == f.c.dim_cost_center)
            .join(acc, acc.c.member_id == f.c.dim_account)
            .outerjoin(
                workflow_status,
                (workflow_status.c.version_id == f.c.version_id)
                & (workflow_status.c.cost_center_id == f.c.dim_cost_center),
            )
            .where(
                f.c.version_id == r.version_id, f.c.layer_id == BASE_LAYER, f.c.measure_id == AMOUNT
            )
        )
        closed = closed_periods(conn, r)
        if closed:
            query = query.where(f.c.period > closed[-1])
        if scope == "approved":
            query = query.where(workflow_status.c.status == "approved")
        rows = [
            OutRow((p, ent, c, a, MEASURE), amount, s)
            for p, ent, c, a, amount, s in conn.execute(query)
        ]
        if closed:
            rows += _actual_rows(conn, r, closed, scope)
        roles, sign = account_roles(conn), convention(conn)
    rows = sorted(
        (
            replace(row, signed=signed(row.amount, roles.get(row.key[3], "cost"), sign))
            for row in rows
        ),
        key=lambda row: row.key,
    )
    return r, rows


def _actual_rows(conn: Connection, r: RoundRef, closed: list[date], scope: Scope) -> list[OutRow]:
    """A forecast's closed months: the actuals of the cost centers it plans, on every account."""
    f, e, cc, acc = fact_cost_center, member.alias("e"), member.alias("cc"), member.alias("acc")
    status = func.coalesce(workflow_status.c.status, "not_started")
    query = (
        select(f.c.period, e.c.code, cc.c.code, acc.c.code, f.c.amount, status)
        .join(e, e.c.member_id == f.c.dim_entity)
        .join(cc, cc.c.member_id == f.c.dim_cost_center)
        .join(acc, acc.c.member_id == f.c.dim_account)
        .join(
            round_member,
            (round_member.c.version_id == r.version_id)
            & (round_member.c.module == MODULE_COST_CENTER)
            & (round_member.c.member_id == f.c.dim_cost_center)
            & round_member.c.enabled.is_(True),
        )
        .join(version, version.c.version_id == f.c.version_id)
        .outerjoin(
            workflow_status,
            (workflow_status.c.version_id == r.version_id)
            & (workflow_status.c.cost_center_id == f.c.dim_cost_center),
        )
        .where(
            version.c.kind == "actuals",
            f.c.layer_id == BASE_LAYER,
            f.c.measure_id == AMOUNT,
            f.c.period.in_(closed),
        )
    )
    if scope == "approved":
        query = query.where(workflow_status.c.status == "approved")
    return [
        OutRow((p, ent, c, a, MEASURE), amount, s, "actual")
        for p, ent, c, a, amount, s in conn.execute(query)
    ]


Published = dict[Key, tuple[Decimal, str, str, Decimal | None]]  # amount, status, origin, signed


def _published(output: Engine, schema: str, version_code: str) -> Published:
    t = fact_plan_cost_center
    with output.begin() as conn:
        ensure(conn, schema)
        return {
            (p, ent, c, a, m): (amount, s, o, sg)
            for p, ent, c, a, m, amount, s, o, sg in conn.execute(
                select(
                    t.c.period,
                    t.c.entity_code,
                    t.c.cost_center_code,
                    t.c.account_code,
                    t.c.measure,
                    t.c.amount,
                    t.c.status,
                    t.c.origin,
                    t.c.amount_signed,
                ).where(t.c.version_code == version_code)
            )
        }


def _fingerprint(rows: list[OutRow], current: Published) -> str:
    h = hashlib.sha256()
    for row in rows:
        h.update(f"+{row.key}|{row.amount}|{row.status}|{row.origin}|{row.signed}\n".encode())
    for key in sorted(current):
        amount, status, origin, sign = current[key]
        h.update(f"={key}|{amount}|{status}|{origin}|{sign}\n".encode())
    return h.hexdigest()


@dataclass
class Change:
    kind: Literal["added", "changed", "removed"]
    cost_center_code: str
    account_code: str
    period: date
    old: Decimal | None
    new: Decimal | None


@dataclass
class CostCenterCounts:
    code: str
    added: int = 0
    changed: int = 0
    removed: int = 0
    unchanged: int = 0


@dataclass
class Preview:
    version_code: str
    scope: Scope
    rows: int
    added: int
    changed: int
    removed: int
    unchanged: int
    cost_centers: list[CostCenterCounts]
    changes: list[Change] = field(default_factory=list)  # the first ones, for display
    fingerprint: str = ""
    hours_rows: int = 0  # planned hours in fact_plan_capacity (ADR 0016)
    workforce_rows: int = 0  # FTE and headcount in fact_plan_workforce (ADR 0020)
    capex_rows: int = 0  # investments in fact_plan_capex (ADR 0028)
    cash_out_rows: int = 0  # their cash out in fact_plan_cash_out
    capex_changed: bool = False  # investments or cash out differ from the output


def preview(
    engine: Engine, output: Engine, schema: str, user: User, code: str, scope: Scope
) -> Preview:
    r, rows = plan_rows(engine, user, code, scope)
    current = _published(output, schema, r.code)
    hours = hour_rows(engine, user, r, scope)
    done = _with_hours(_compare(r, scope, rows, current), hours, _published_hours(output, r.code))
    staff = workforce_rows(engine, user, r, scope)
    done = _with_workforce(done, staff, _published_workforce(output, r.code))
    investments, cash = capex_rows(engine, user, r, scope)
    return _with_capex(done, r, investments, cash, _published_capex(output, r.code))


# --- Planned hours (ADR 0016) ---------------------------------------------------------------------

HourKey = tuple[date, str, str, str, str]  # period, entity, cost center, work center, activity type
HOURS_MEASURE = "hours"


@dataclass(frozen=True)
class HourRow:
    key: HourKey
    hours: Decimal
    status: str
    origin: Origin = "plan"


def hour_rows(engine: Engine, user: User, r: RoundRef, scope: Scope) -> list[HourRow]:
    """The round's planned hours in the scope; a forecast's closed months as actual hours of
    the cost centers productive in the round."""
    f = fact_capacity
    e, cc, wc, at = (member.alias(n) for n in ("e", "cc", "wc", "at"))
    status = func.coalesce(workflow_status.c.status, "not_started")
    with unit_of_work(engine, user) as conn:
        closed = closed_periods(conn, r)
        base = (
            select(f.c.period, e.c.code, cc.c.code, wc.c.code, at.c.code, f.c.amount, status)
            .join(e, e.c.member_id == f.c.dim_entity)
            .join(cc, cc.c.member_id == f.c.dim_cost_center)
            .join(wc, wc.c.member_id == f.c.dim_work_center)
            .join(at, at.c.member_id == f.c.dim_activity_type)
            .outerjoin(
                workflow_status,
                (workflow_status.c.version_id == r.version_id)
                & (workflow_status.c.cost_center_id == f.c.dim_cost_center),
            )
            .where(f.c.layer_id == BASE_LAYER, f.c.measure_id == HOURS)
        )
        if scope == "approved":
            base = base.where(workflow_status.c.status == "approved")
        planned = base.where(f.c.version_id == r.version_id)
        if closed:
            planned = planned.where(f.c.period > closed[-1])
        rows = [HourRow((p, en, c, w, a), h, s) for p, en, c, w, a, h, s in conn.execute(planned)]
        if closed:
            actual = (
                base.join(version, version.c.version_id == f.c.version_id)
                .join(
                    round_member,
                    (round_member.c.version_id == r.version_id)
                    & (round_member.c.module == MODULE_CAPACITY)
                    & (round_member.c.member_id == f.c.dim_cost_center)
                    & round_member.c.enabled.is_(True),
                )
                .where(version.c.kind == "actuals", f.c.period.in_(closed))
            )
            rows += [
                HourRow((p, en, c, w, a), h, s, "actual")
                for p, en, c, w, a, h, s in conn.execute(actual)
            ]
    rows.sort(key=lambda row: row.key)
    return rows


def _hour_records(r: RoundRef, hours: list[HourRow]) -> list[dict[str, object]]:
    """The rows of ``fact_plan_capacity`` for the round, without the run's time and id."""
    return [
        {
            "version_code": r.code,
            "fiscal_year": r.fiscal_year,
            "period": row.key[0],
            "entity_code": row.key[1],
            "cost_center_code": row.key[2],
            "work_center_code": row.key[3],
            "activity_type_code": row.key[4],
            "measure": HOURS_MEASURE,
            "amount": row.hours,
            "status": row.status,
            "origin": row.origin,
        }
        for row in hours
    ]


PublishedHours = dict[HourKey, tuple[Decimal, str, str]]


def _published_hours(output: Engine, version_code: str) -> PublishedHours:
    t = fact_plan_capacity
    with output.connect() as conn:
        if not output.dialect.has_table(conn, t.name, schema=conn.schema_for_object(t)):
            return {}
        return {
            (p, en, c, w, a): (h, s, o)
            for p, en, c, w, a, h, s, o in conn.execute(
                select(
                    t.c.period,
                    t.c.entity_code,
                    t.c.cost_center_code,
                    t.c.work_center_code,
                    t.c.activity_type_code,
                    t.c.amount,
                    t.c.status,
                    t.c.origin,
                ).where(t.c.version_code == version_code)
            )
        }


def _with_hours(done: Preview, rows: list[HourRow], current: PublishedHours) -> Preview:
    """The preview with the hour rows counted and in its fingerprint."""
    h = hashlib.sha256(done.fingerprint.encode())
    for row in rows:
        h.update(f"+{row.key}|{row.hours}|{row.status}|{row.origin}\n".encode())
    for key in sorted(current):
        h.update(f"={key}|{current[key]}\n".encode())
    done.hours_rows = len(rows)
    done.fingerprint = h.hexdigest()
    return done


# --- FTE and headcount (ADR 0020) -----------------------------------------------------------------

# period, entity, cost center, staff type, staff category, measure
StaffKey = tuple[date, str, str, str, str, str]
STAFF_MEASURES = {FTE: "fte", HEADCOUNT: "headcount"}


@dataclass(frozen=True)
class StaffRow:
    key: StaffKey
    value: Decimal
    status: str
    origin: Origin = "plan"


def workforce_rows(engine: Engine, user: User, r: RoundRef, scope: Scope) -> list[StaffRow]:
    """The round's FTE and headcount in the scope, if it plans its workforce; a forecast's
    closed months as actuals of the cost centers planned in the round."""
    f = fact_workforce
    e, cc, st, sc = (member.alias(n) for n in ("e", "cc", "st", "sc"))
    status = func.coalesce(workflow_status.c.status, "not_started")
    with unit_of_work(engine, user) as conn:
        planned_here = conn.execute(
            select(round_module.c.mode).where(
                round_module.c.version_id == r.version_id,
                round_module.c.module == MODULE_WORKFORCE,
            )
        ).scalar()
        if planned_here is None:
            return []
        closed = closed_periods(conn, r)
        base = (
            select(f.c.period, e.c.code, cc.c.code, st.c.code, sc.c.code, f.c.measure_id,
                   f.c.amount, status)
            .join(e, e.c.member_id == f.c.dim_entity)
            .join(cc, cc.c.member_id == f.c.dim_cost_center)
            .join(st, st.c.member_id == f.c.dim_staff_type)
            .join(sc, sc.c.member_id == f.c.dim_staff_category)
            .outerjoin(
                workflow_status,
                (workflow_status.c.version_id == r.version_id)
                & (workflow_status.c.cost_center_id == f.c.dim_cost_center),
            )
            .where(f.c.layer_id == BASE_LAYER, f.c.measure_id.in_(STAFF_MEASURES))
        )  # fmt: skip
        if scope == "approved":
            base = base.where(workflow_status.c.status == "approved")
        planned = base.where(f.c.version_id == r.version_id)
        if closed:
            planned = planned.where(f.c.period > closed[-1])
        rows = [
            StaffRow((p, en, c, t, k, STAFF_MEASURES[m]), v, s)
            for p, en, c, t, k, m, v, s in conn.execute(planned)
        ]
        if closed:
            actual = (
                base.join(version, version.c.version_id == f.c.version_id)
                .join(
                    round_member,
                    (round_member.c.version_id == r.version_id)
                    & (round_member.c.module == MODULE_COST_CENTER)
                    & (round_member.c.member_id == f.c.dim_cost_center)
                    & round_member.c.enabled.is_(True),
                )
                .where(version.c.kind == "actuals", f.c.period.in_(closed))
            )
            rows += [
                StaffRow((p, en, c, t, k, STAFF_MEASURES[m]), v, s, "actual")
                for p, en, c, t, k, m, v, s in conn.execute(actual)
            ]
    rows.sort(key=lambda row: row.key)
    return rows


PublishedStaff = dict[StaffKey, tuple[Decimal, str, str]]


def _published_workforce(output: Engine, version_code: str) -> PublishedStaff:
    t = fact_plan_workforce
    with output.connect() as conn:
        if not output.dialect.has_table(conn, t.name, schema=conn.schema_for_object(t)):
            return {}
        return {
            (p, en, c, st, sc, m): (v, s, o)
            for p, en, c, st, sc, m, v, s, o in conn.execute(
                select(
                    t.c.period,
                    t.c.entity_code,
                    t.c.cost_center_code,
                    t.c.staff_type_code,
                    t.c.staff_category_code,
                    t.c.measure,
                    t.c.amount,
                    t.c.status,
                    t.c.origin,
                ).where(t.c.version_code == version_code)
            )
        }


def _with_workforce(done: Preview, rows: list[StaffRow], current: PublishedStaff) -> Preview:
    """The preview with the workforce rows counted and in its fingerprint."""
    h = hashlib.sha256(done.fingerprint.encode())
    for row in rows:
        h.update(f"+{row.key}|{row.value}|{row.status}|{row.origin}\n".encode())
    for key in sorted(current):
        h.update(f"={key}|{current[key]}\n".encode())
    done.workforce_rows = len(rows)
    done.fingerprint = h.hexdigest()
    return done


# --- Investments and cash out (ADR 0028) ----------------------------------------------------------


def capex_rows(
    engine: Engine, user: User, r: RoundRef, scope: Scope
) -> tuple[list[InvestmentRow], list[CashOutRow]]:
    with unit_of_work(engine, user) as conn:
        return published_rows(conn, r, scope)


def _capex_records(
    r: RoundRef, investments: list[InvestmentRow], cash: list[CashOutRow]
) -> dict[str, list[dict[str, object]]]:
    """The rows of ``fact_plan_capex`` and ``fact_plan_cash_out`` for the round, without the
    run's time and id."""
    head = {"version_code": r.code, "fiscal_year": r.fiscal_year}
    return {
        fact_plan_capex.name: [
            {
                **head,
                "investment_id": i.investment_id,
                "investment_name": i.name,
                "project_id": i.project_id,
                "project_name": i.project_name,
                "entity_code": i.entity_code,
                "cost_center_code": i.cost_center_code,
                "capex_type_code": i.capex_type_code,
                "priority": i.priority,
                "start_month": i.start_month,
                "amount": i.amount,
                "confirmation": i.confirmation,
                "confirmed_by": i.confirmed_by,
                "confirmed_at": i.confirmed_at,
            }
            for i in investments
        ],
        fact_plan_cash_out.name: [
            {
                **head,
                "investment_id": c.investment_id,
                "period": c.period,
                "entity_code": c.entity_code,
                "cost_center_code": c.cost_center_code,
                "capex_type_code": c.capex_type_code,
                "amount": c.amount,
                "confirmation": c.confirmation,
            }
            for c in cash
        ],
    }


def _line(table: str, record: dict[str, object]) -> str:
    """A record as one comparable line; times in UTC, whatever the connection's time zone."""
    values = [
        v.astimezone(UTC).isoformat() if isinstance(v, datetime) else str(v)
        for _, v in sorted(record.items())
    ]
    return f"{table}|{'|'.join(values)}"


def _published_capex(output: Engine, version_code: str) -> list[str]:
    """What the output holds of the round's investments and cash out, as comparable lines."""
    lines: list[str] = []
    with output.connect() as conn:
        for t in (fact_plan_capex, fact_plan_cash_out):
            if not output.dialect.has_table(conn, t.name, schema=conn.schema_for_object(t)):
                continue
            columns = [c for c in t.c if c.name not in ("published_at", "publish_run_id")]
            lines += [
                _line(t.name, dict(row._mapping))
                for row in conn.execute(select(*columns).where(t.c.version_code == version_code))
            ]
    return sorted(lines)


def _with_capex(
    done: Preview,
    r: RoundRef,
    investments: list[InvestmentRow],
    cash: list[CashOutRow],
    current: list[str],
) -> Preview:
    """The preview with investments and cash out counted, compared and in its fingerprint."""
    wanted = sorted(
        _line(table, record)
        for table, records in _capex_records(r, investments, cash).items()
        for record in records
    )
    h = hashlib.sha256(done.fingerprint.encode())
    for line in wanted:
        h.update(f"+{line}\n".encode())
    for line in current:
        h.update(f"={line}\n".encode())
    done.capex_rows = len(investments)
    done.cash_out_rows = len(cash)
    done.capex_changed = wanted != current
    done.fingerprint = h.hexdigest()
    return done


def _compare(r: RoundRef, scope: Scope, rows: list[OutRow], current: Published) -> Preview:
    counts: dict[str, CostCenterCounts] = {}
    total: Counter[str] = Counter()
    changes: list[Change] = []

    def note(kind: str, key: Key, old: Decimal | None, new: Decimal | None) -> None:
        cc = counts.setdefault(key[2], CostCenterCounts(key[2]))
        setattr(cc, kind, getattr(cc, kind) + 1)
        total[kind] += 1
        if kind != "unchanged" and len(changes) < 200:
            changes.append(Change(kind, key[2], key[3], key[0], old, new))  # type: ignore[arg-type]

    seen = set()
    for row in rows:
        seen.add(row.key)
        old = current.get(row.key)
        if old is None:
            note("added", row.key, None, row.amount)
        elif old != (row.amount, row.status, row.origin, row.signed):
            note("changed", row.key, old[0], row.amount)
        else:
            note("unchanged", row.key, old[0], row.amount)
    for key in sorted(set(current) - seen):
        note("removed", key, current[key][0], None)
    return Preview(
        r.code,
        scope,
        len(rows),
        total["added"],
        total["changed"],
        total["removed"],
        total["unchanged"],
        sorted(counts.values(), key=lambda c: c.code),
        changes,
        _fingerprint(rows, current),
    )


def run(
    engine: Engine,
    output: Engine,
    schema: str,
    user: User,
    code: str,
    scope: Scope,
    fingerprint: str | None,
) -> Preview:
    """Replace the round's rows in the output. ``fingerprint`` is the preview's; ``None`` skips
    the check (republish). Returns what the run did."""
    r, rows = plan_rows(engine, user, code, scope)
    current = _published(output, schema, r.code)
    hours = hour_rows(engine, user, r, scope)
    done = _with_hours(_compare(r, scope, rows, current), hours, _published_hours(output, r.code))
    staff = workforce_rows(engine, user, r, scope)
    done = _with_workforce(done, staff, _published_workforce(output, r.code))
    investments, cash = capex_rows(engine, user, r, scope)
    done = _with_capex(done, r, investments, cash, _published_capex(output, r.code))
    if fingerprint is not None and fingerprint != done.fingerprint:
        raise PublishError("the plan values or the output changed since the preview; preview again")

    with unit_of_work(engine, user) as conn:
        run_id = int(
            conn.execute(
                insert(publish_run)
                .values(version_id=r.version_id, scope=scope, actor=user.login)
                .returning(publish_run.c.id)
            ).scalar_one()
        )
        cost_centers, accounts = (
            _dimension(conn, COST_CENTER, H_COST_CENTER),
            _dimension(conn, ACCOUNT, H_ACCOUNT),
        )
        roles = account_roles(conn)
        for account in accounts:
            account["role"] = roles.get(str(account["code"]))
        work_centers = [
            {"code": c, "name": n, "cost_center_code": cc}
            for c, n, cc in conn.execute(
                select(member.c.code, member.c.name, member.c.cost_center_code).where(
                    member.c.dim_id == WORK_CENTER, member.c.is_group.is_(False)
                )
            )
        ]
        activity_types = [
            {"code": c, "name": n, "account_code": a}
            for c, n, a in conn.execute(
                select(member.c.code, member.c.name, member.c.account_code).where(
                    member.c.dim_id == ACTIVITY_TYPE, member.c.is_group.is_(False)
                )
            )
        ]
        staff_types = [
            {"code": c, "name": n, "external": bool(x)}
            for c, n, x in conn.execute(
                select(member.c.code, member.c.name, member.c.external).where(
                    member.c.dim_id == STAFF_TYPE, member.c.is_group.is_(False)
                )
            )
        ]
        types = capex_types(conn)
        staff_categories = [
            {"code": c, "name": n}
            for c, n in conn.execute(
                select(member.c.code, member.c.name).where(
                    member.c.dim_id == STAFF_CATEGORY, member.c.is_group.is_(False)
                )
            )
        ]
    capex_records = _capex_records(r, investments, cash)
    now = datetime.now().astimezone()
    with output.begin() as conn:
        ensure(conn, schema)
        t = fact_plan_cost_center
        conn.execute(delete(t).where(t.c.version_code == r.code))
        if rows:
            conn.execute(
                insert(t),
                [
                    {
                        "version_code": r.code,
                        "fiscal_year": r.fiscal_year,
                        "period": row.key[0],
                        "entity_code": row.key[1],
                        "cost_center_code": row.key[2],
                        "account_code": row.key[3],
                        "measure": row.key[4],
                        "amount": row.amount,
                        "amount_signed": row.signed,
                        "status": row.status,
                        "origin": row.origin,
                        "published_at": now,
                        "publish_run_id": run_id,
                    }
                    for row in rows
                ],
            )
        h = fact_plan_capacity
        conn.execute(delete(h).where(h.c.version_code == r.code))
        if hours:
            conn.execute(
                insert(h),
                [
                    {**record, "published_at": now, "publish_run_id": run_id}
                    for record in _hour_records(r, hours)
                ],
            )
        w = fact_plan_workforce
        conn.execute(delete(w).where(w.c.version_code == r.code))
        if staff:
            conn.execute(
                insert(w),
                [
                    {
                        "version_code": r.code,
                        "fiscal_year": r.fiscal_year,
                        "period": row.key[0],
                        "entity_code": row.key[1],
                        "cost_center_code": row.key[2],
                        "staff_type_code": row.key[3],
                        "staff_category_code": row.key[4],
                        "measure": row.key[5],
                        "amount": row.value,
                        "status": row.status,
                        "origin": row.origin,
                        "published_at": now,
                        "publish_run_id": run_id,
                    }
                    for row in staff
                ],
            )
        stamp = {"published_at": now, "publish_run_id": run_id}
        for table, records in (
            (fact_plan_capex, capex_records[fact_plan_capex.name]),
            (fact_plan_cash_out, capex_records[fact_plan_cash_out.name]),
        ):
            conn.execute(delete(table).where(table.c.version_code == r.code))
            if records:
                conn.execute(insert(table), [{**record, **stamp} for record in records])
        for table, members in (
            (dim_capex_type, types),
            (dim_cost_center, cost_centers),
            (dim_account, accounts),
            (dim_work_center, work_centers),
            (dim_activity_type, activity_types),
            (dim_staff_type, staff_types),
            (dim_staff_category, staff_categories),
        ):
            conn.execute(delete(table))
            if members:
                conn.execute(insert(table), members)
        stmt = pg_insert(dim_version).values(
            code=r.code,
            name=r.name,
            kind=r.kind,
            fiscal_year=r.fiscal_year,
            published_at=now,
            scope=scope,
            actuals_through=r.actuals_through,
        )
        conn.execute(
            stmt.on_conflict_do_update(
                index_elements=[dim_version.c.code],
                set_={
                    c: stmt.excluded[c]
                    for c in (
                        "name",
                        "kind",
                        "fiscal_year",
                        "published_at",
                        "scope",
                        "actuals_through",
                    )
                },
            )
        )
    with unit_of_work(engine, user) as conn:
        conn.execute(
            update(publish_run)
            .where(publish_run.c.id == run_id)
            .values(
                finished_at=func.now(),
                rows=done.rows,
                added=done.added,
                changed=done.changed,
                removed=done.removed,
                fingerprint=done.fingerprint,
            )
        )
    return done


def _dimension(conn: Connection, dim: int, hier_id: int) -> list[dict[str, object]]:
    """Members of a dimension with parent, depth and the level columns of the main hierarchy."""
    rows = {
        m.member_id: m
        for m in conn.execute(
            select(member.c.member_id, member.c.code, member.c.name, member.c.entity_code,
                   member.c.account_type).where(member.c.dim_id == dim)
        )
    }  # fmt: skip
    parent = dict(
        conn.execute(
            select(hier_edge.c.member_id, hier_edge.c.parent_id).where(
                hier_edge.c.hier_id == hier_id
            )
        ).all()
    )
    out = []
    for mid, m in rows.items():
        path: list[int] = []
        node: int | None = mid
        while node is not None and node in rows and len(path) < 50:
            path.append(node)
            node = parent.get(node)
        path.reverse()
        row: dict[str, object] = {
            "code": m.code,
            "name": m.name,
            "parent_code": rows[parent[mid]].code if parent.get(mid) in rows else None,
            "depth": len(path) - 1 if mid in parent else None,
        }
        row["entity_code" if dim == COST_CENTER else "account_type"] = (
            m.entity_code if dim == COST_CENTER else m.account_type
        )
        for i in range(LEVELS):
            level = rows[path[i]] if mid in parent and i < len(path) else None
            row[f"level_{i + 1}_code"] = level.code if level else None
            row[f"level_{i + 1}_name"] = level.name if level else None
        out.append(row)
    return out


# --- Files ----------------------------------------------------------------------------------------


def _records(r: RoundRef, rows: list[OutRow]) -> Iterator[tuple[object, ...]]:
    for row in rows:
        p, ent, c, a, m = row.key
        amount = f"{row.amount:.2f}"
        sign = "" if row.signed is None else f"{row.signed:.2f}"
        yield (
            r.code, r.fiscal_year, p.isoformat(), ent, c, a, m, amount, row.status, row.origin, sign
        )  # fmt: skip


def csv_bytes(engine: Engine, user: User, code: str, scope: Scope) -> bytes:
    r, rows = plan_rows(engine, user, code, scope)
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(COLUMNS)
    writer.writerows(_records(r, rows))
    return buffer.getvalue().encode("utf-8")


def parquet_bytes(engine: Engine, user: User, code: str, scope: Scope) -> bytes:
    r, rows = plan_rows(engine, user, code, scope)
    columns: dict[str, list[object]] = defaultdict(list)
    for row in rows:
        p, ent, c, a, m = row.key
        for name, value in zip(
            COLUMNS,
            (
                r.code,
                r.fiscal_year,
                p,
                ent,
                c,
                a,
                m,
                row.amount,
                row.status,
                row.origin,
                row.signed,
            ),
            strict=True,
        ):
            columns[name].append(value)
    schema = pa.schema(
        [
            ("version_code", pa.string()),
            ("fiscal_year", pa.int32()),
            ("period", pa.date32()),
            ("entity_code", pa.string()),
            ("cost_center_code", pa.string()),
            ("account_code", pa.string()),
            ("measure", pa.string()),
            ("amount", pa.decimal128(18, 2)),
            ("status", pa.string()),
            ("origin", pa.string()),
            ("amount_signed", pa.decimal128(18, 2)),
        ]
    )
    table = pa.table({n: columns.get(n, []) for n in COLUMNS}, schema=schema)
    buffer = io.BytesIO()
    pq.write_table(table, buffer)
    return buffer.getvalue()


# A download per published table: the plan amounts (above), planned hours, investments and their
# cash out, each with its table's columns but the run's time and id.
Download = Literal["amounts", "hours", "investments", "cash_out"]
RUN_COLUMNS = ("published_at", "publish_run_id")


def _arrow_type(column: Column[object]) -> pa.DataType:
    match column.type:
        case Numeric() as n:
            return pa.decimal128(n.precision or 18, n.scale or 0)
        case DateTime():
            return pa.timestamp("us", tz="UTC")
        case Date():
            return pa.date32()
        case BigInteger():
            return pa.int64()
        case SmallInteger():
            return pa.int16()
        case Integer():
            return pa.int32()
        case _:
            return pa.string()


def _text(value: object) -> object:
    match value:
        case None:
            return ""
        case datetime():
            return value.astimezone(UTC).isoformat()
        case date():
            return value.isoformat()
        case _:
            return value


def _table_file(
    table: Table, records: list[dict[str, object]], fmt: Literal["csv", "parquet"]
) -> bytes:
    columns = [c for c in table.columns if c.name not in RUN_COLUMNS]
    if fmt == "csv":
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow([c.name for c in columns])
        writer.writerows([_text(record[c.name]) for c in columns] for record in records)
        return buffer.getvalue().encode("utf-8")
    schema = pa.schema([(c.name, _arrow_type(c)) for c in columns])
    data = {c.name: [record[c.name] for record in records] for c in columns}
    out = io.BytesIO()
    pq.write_table(pa.table(data, schema=schema), out)
    return out.getvalue()


def download(
    engine: Engine,
    user: User,
    code: str,
    scope: Scope,
    what: Download,
    fmt: Literal["csv", "parquet"],
) -> bytes:
    """One published table of the round in the scope, as a file; writes nothing."""
    if what == "amounts":
        return (
            csv_bytes(engine, user, code, scope)
            if fmt == "csv"
            else parquet_bytes(engine, user, code, scope)
        )
    user.require_controller()
    with unit_of_work(engine, user) as conn:
        r = get_round(conn, code)
    if what == "hours":
        return _table_file(
            fact_plan_capacity, _hour_records(r, hour_rows(engine, user, r, scope)), fmt
        )
    records = _capex_records(r, *capex_rows(engine, user, r, scope))
    table = fact_plan_capex if what == "investments" else fact_plan_cash_out
    return _table_file(table, records[table.name], fmt)


# --- History and republish ------------------------------------------------------------------------


@dataclass
class RunRow:
    id: int
    scope: str
    actor: str | None
    started_at: datetime
    finished_at: datetime | None
    rows: int | None
    added: int | None
    changed: int | None
    removed: int | None


def history(engine: Engine, user: User, code: str) -> list[RunRow]:
    user.require_controller()
    with engine.connect() as conn:
        r = get_round(conn, code)
        return [
            RunRow(**row._mapping)
            for row in conn.execute(
                select(
                    publish_run.c.id,
                    publish_run.c.scope,
                    publish_run.c.actor,
                    publish_run.c.started_at,
                    publish_run.c.finished_at,
                    publish_run.c.rows,
                    publish_run.c.added,
                    publish_run.c.changed,
                    publish_run.c.removed,
                )
                .where(publish_run.c.version_id == r.version_id)
                .order_by(publish_run.c.id.desc())
                .limit(50)
            )
        ]


def republish_all(engine: Engine, output: Engine, schema: str, user: User) -> list[str]:
    """Rebuild the output from every round's last finished run and its scope (D-30 restore)."""
    with engine.connect() as conn:
        latest = (
            select(func.max(publish_run.c.id))
            .where(publish_run.c.finished_at.is_not(None))
            .group_by(publish_run.c.version_id)
        )
        last = conn.execute(
            select(version.c.code, publish_run.c.scope)
            .join(version, version.c.version_id == publish_run.c.version_id)
            .where(publish_run.c.id.in_(latest))
            .order_by(version.c.code)
        ).all()
    for code, scope in last:
        run(engine, output, schema, user, code, scope, fingerprint=None)
    return [code for code, _ in last]
