"""Publishing a round's plan values (ADR 0011): files, preview and run into ``planlayer_out``.

A run replaces the round's rows in the output with exactly the selected scope: the approved
cost centers only, or the whole round. The preview compares those rows by key with what the
output holds and lists rows added, changed and removed; it carries a fingerprint of both sides,
and a run refuses when the fingerprint no longer matches, so it does exactly what was previewed.

The output may live in another database, so a run is two transactions: the output is replaced,
then the run is recorded in the app store. Repeating a run is harmless. Values are published
natural-positive until the export sign rules exist (E-12).
"""

import csv
import hashlib
import io
from collections import Counter, defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import Connection, Engine, delete, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from planlayer.access import User
from planlayer.rounds import RoundRef, get_round
from planlayer.store.output import (
    LEVELS,
    dim_account,
    dim_cost_center,
    dim_version,
    ensure,
    fact_plan_cost_center,
)
from planlayer.store.schema import (
    ACCOUNT,
    AMOUNT,
    BASE_LAYER,
    COST_CENTER,
    H_ACCOUNT,
    H_COST_CENTER,
    fact_cost_center,
    hier_edge,
    member,
    publish_run,
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
)


class PublishError(ValueError):
    """A publish that can't run as asked; nothing was written."""


@dataclass(frozen=True)
class OutRow:
    key: Key
    amount: Decimal
    status: str


def plan_rows(engine: Engine, user: User, code: str, scope: Scope) -> tuple[RoundRef, list[OutRow]]:
    """The round's plan values in the scope, as the output will hold them."""
    user.require_controller()
    f, e, cc, acc = fact_cost_center, member.alias("e"), member.alias("cc"), member.alias("acc")
    with unit_of_work(engine, user) as conn:
        r = get_round(conn, code)
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
        if scope == "approved":
            query = query.where(workflow_status.c.status == "approved")
        rows = [
            OutRow((p, ent, c, a, MEASURE), amount, s)
            for p, ent, c, a, amount, s in conn.execute(query)
        ]
    rows.sort(key=lambda row: row.key)
    return r, rows


def _published(output: Engine, schema: str, version_code: str) -> dict[Key, tuple[Decimal, str]]:
    t = fact_plan_cost_center
    with output.begin() as conn:
        ensure(conn, schema)
        return {
            (p, ent, c, a, m): (amount, s)
            for p, ent, c, a, m, amount, s in conn.execute(
                select(
                    t.c.period,
                    t.c.entity_code,
                    t.c.cost_center_code,
                    t.c.account_code,
                    t.c.measure,
                    t.c.amount,
                    t.c.status,
                ).where(t.c.version_code == version_code)
            )
        }


def _fingerprint(rows: list[OutRow], current: dict[Key, tuple[Decimal, str]]) -> str:
    h = hashlib.sha256()
    for row in rows:
        h.update(f"+{row.key}|{row.amount}|{row.status}\n".encode())
    for key in sorted(current):
        amount, status = current[key]
        h.update(f"={key}|{amount}|{status}\n".encode())
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


def preview(
    engine: Engine, output: Engine, schema: str, user: User, code: str, scope: Scope
) -> Preview:
    r, rows = plan_rows(engine, user, code, scope)
    current = _published(output, schema, r.code)
    return _compare(r, scope, rows, current)


def _compare(
    r: RoundRef, scope: Scope, rows: list[OutRow], current: dict[Key, tuple[Decimal, str]]
) -> Preview:
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
        elif old != (row.amount, row.status):
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
    done = _compare(r, scope, rows, current)
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
                        "status": row.status,
                        "published_at": now,
                        "publish_run_id": run_id,
                    }
                    for row in rows
                ],
            )
        for table, members in ((dim_cost_center, cost_centers), (dim_account, accounts)):
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
        )
        conn.execute(
            stmt.on_conflict_do_update(
                index_elements=[dim_version.c.code],
                set_={
                    c: stmt.excluded[c]
                    for c in ("name", "kind", "fiscal_year", "published_at", "scope")
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
        yield (r.code, r.fiscal_year, p.isoformat(), ent, c, a, m, f"{row.amount:.2f}", row.status)


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
            COLUMNS, (r.code, r.fiscal_year, p, ent, c, a, m, row.amount, row.status), strict=True
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
        ]
    )
    table = pa.table({n: columns.get(n, []) for n in COLUMNS}, schema=schema)
    buffer = io.BytesIO()
    pq.write_table(table, buffer)
    return buffer.getvalue()


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
