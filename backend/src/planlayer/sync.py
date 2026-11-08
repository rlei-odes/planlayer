"""Sync master data and actuals from the source into the app store (D-27).

Discipline: read every dataset, resolve every reference, and only then write, in one
transaction. Any unresolved code stops the sync before anything is written, and the error lists
the codes. Re-running a sync gives the same result.

Datasets and their contract schemas are the first part of D-11; the mapping of each dataset to
a source relation is fixed here until the mapping page (E-01) exists.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import cast

import pyarrow as pa
from sqlalchemy import Connection, Engine, delete, func, insert, literal, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from planlayer.adapters.source import Relation, SourceAdapter
from planlayer.store.schema import (
    ACCOUNT,
    AMOUNT,
    BASE_LAYER,
    COST_CENTER,
    ENTITY,
    H_ACCOUNT,
    H_COST_CENTER,
    closure,
    fact_cost_center,
    hier_edge,
    member,
    sync_run,
    version,
)
from planlayer.store.uow import Actor, unit_of_work

ACTUALS_CODE = "ACT"

DATASETS: dict[str, pa.Schema] = {
    "entities": pa.schema([("entity_code", pa.string()), ("entity_name", pa.string())]),
    "cost_centers": pa.schema(
        [
            ("cost_center_code", pa.string()),
            ("cost_center_name", pa.string()),
            ("entity_code", pa.string()),
            ("owner", pa.string()),
            ("valid_from", pa.date32()),
            ("valid_to", pa.date32()),
        ]
    ),
    "cost_center_hierarchy": pa.schema(
        [
            ("member_code", pa.string()),
            ("member_name", pa.string()),
            ("parent_code", pa.string()),
            ("is_group", pa.bool_()),
            ("sort_order", pa.int32()),
        ]
    ),
    "accounts": pa.schema(
        [
            ("account_code", pa.string()),
            ("account_name", pa.string()),
            ("account_type", pa.string()),
        ]
    ),
    "account_hierarchy": pa.schema(
        [
            ("member_code", pa.string()),
            ("member_name", pa.string()),
            ("parent_code", pa.string()),
            ("is_group", pa.bool_()),
            ("sort_order", pa.int32()),
        ]
    ),
    # Natural signs (T-7); sign rules per source arrive with E-12.
    "gl_actuals": pa.schema(
        [
            ("period", pa.date32()),
            ("entity_code", pa.string()),
            ("cost_center_code", pa.string()),
            ("account_code", pa.string()),
            ("amount", pa.decimal128(18, 2)),
        ]
    ),
}


def default_mapping(source_schema: str) -> dict[str, Relation]:
    names = {
        "entities": "v_entities",
        "cost_centers": "v_cost_centers",
        "cost_center_hierarchy": "v_cost_center_hierarchy",
        "accounts": "v_accounts",
        "account_hierarchy": "v_account_hierarchy",
        "gl_actuals": "v_gl_actuals_natural",
    }
    return {k: Relation(v, source_schema) for k, v in names.items()}


class SyncError(Exception):
    """The source data can't be synced as it is; nothing was written."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        shown = "; ".join(problems[:10])
        more = f" (and {len(problems) - 10} more)" if len(problems) > 10 else ""
        super().__init__(f"{len(problems)} problem(s): {shown}{more}")


@dataclass
class SyncResult:
    members: int
    actual_rows: int


def _rows(
    adapter: SourceAdapter, relation: Relation, contract: pa.Schema
) -> list[dict[str, object]]:
    batches = list(adapter.read(relation, contract))
    rows: list[dict[str, object]] = pa.Table.from_batches(batches, schema=contract).to_pylist()
    return rows


def run(
    engine: Engine,
    adapter: SourceAdapter,
    mapping: dict[str, Relation],
    actor: Actor,
    entity: str | None = None,
) -> SyncResult:
    data = {name: _rows(adapter, mapping[name], contract) for name, contract in DATASETS.items()}
    actuals = [r for r in data["gl_actuals"] if entity is None or r["entity_code"] == entity]
    _resolve(data, actuals)

    with unit_of_work(engine, actor) as conn:
        run_id = conn.execute(
            insert(sync_run)
            .values(actor=actor.login, entity_filter=entity)
            .returning(sync_run.c.id)
        ).scalar_one()
        ids = _write_members(conn, data)
        _write_hierarchy(conn, H_COST_CENTER, COST_CENTER, data["cost_center_hierarchy"], ids)
        _write_hierarchy(conn, H_ACCOUNT, ACCOUNT, data["account_hierarchy"], ids)
        _rebuild_closure(conn)
        n = _write_actuals(conn, actuals, ids, entity)
        conn.execute(
            sync_run.update()
            .where(sync_run.c.id == run_id)
            .values(finished_at=func.now(), members=len(ids), actual_rows=n)
        )
    return SyncResult(members=len(ids), actual_rows=n)


def _resolve(data: dict[str, list[dict[str, object]]], actuals: list[dict[str, object]]) -> None:
    problems: list[str] = []
    entities = {r["entity_code"] for r in data["entities"]}
    cc_leaves = {r["cost_center_code"] for r in data["cost_centers"]}
    accounts = {r["account_code"] for r in data["accounts"]}
    for name, leaves in (("cost_center_hierarchy", cc_leaves), ("account_hierarchy", accounts)):
        rows = data[name]
        codes = {r["member_code"] for r in rows}
        roots = [r for r in rows if r["parent_code"] is None]
        if len(roots) != 1:
            problems.append(f"{name}: {len(roots)} roots, expected 1")
        problems += [
            f"{name}: {r['member_code']} has unknown parent {r['parent_code']}"
            for r in rows
            if r["parent_code"] is not None and r["parent_code"] not in codes
        ]
        problems += [f"{name}: leaf {c} is not in the hierarchy" for c in sorted(leaves - codes)]  # type: ignore[type-var]
        problems += _cycles(name, rows)
    for r in data["cost_centers"]:
        if r["entity_code"] not in entities:
            problems.append(
                f"cost center {r['cost_center_code']}: unknown entity {r['entity_code']}"
            )
    for field, known, label in (
        ("entity_code", entities, "entity"),
        ("cost_center_code", cc_leaves, "cost center"),
        ("account_code", accounts, "account"),
    ):
        unknown = sorted({r[field] for r in actuals} - known)  # type: ignore[type-var]
        problems += [f"actuals: unknown {label} {code}" for code in unknown]
    if problems:
        raise SyncError(problems)


def _cycles(name: str, rows: list[dict[str, object]]) -> list[str]:
    parent = {r["member_code"]: r["parent_code"] for r in rows}
    for start in parent:
        seen, node = set(), start
        while node is not None:
            if node in seen:
                return [f"{name}: cycle through {node}"]
            seen.add(node)
            node = parent.get(node)
    return []


def _write_members(
    conn: Connection, data: dict[str, list[dict[str, object]]]
) -> dict[tuple[int, str], int]:
    rows: list[dict[str, object]] = []
    rows += [
        {"dim_id": ENTITY, "code": r["entity_code"], "name": r["entity_name"], "is_group": False}
        for r in data["entities"]
    ]
    cc = {r["cost_center_code"]: r for r in data["cost_centers"]}
    for r in data["cost_center_hierarchy"]:
        leaf = cc.get(r["member_code"])
        rows.append(
            {
                "dim_id": COST_CENTER,
                "code": r["member_code"],
                "name": r["member_name"],
                "is_group": r["is_group"],
                "entity_code": leaf["entity_code"] if leaf else None,
                "owner": leaf["owner"] if leaf else None,
                "valid_from": leaf["valid_from"] if leaf else None,
                "valid_to": leaf["valid_to"] if leaf else None,
            }
        )
    types = {r["account_code"]: r["account_type"] for r in data["accounts"]}
    for r in data["account_hierarchy"]:
        rows.append(
            {
                "dim_id": ACCOUNT,
                "code": r["member_code"],
                "name": r["member_name"],
                "is_group": r["is_group"],
                "account_type": types.get(r["member_code"]),
            }
        )
    for r in rows:
        for key in ("entity_code", "owner", "account_type", "valid_from", "valid_to"):
            r.setdefault(key, None)
    stmt = pg_insert(member).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=[member.c.dim_id, member.c.code],
        set_={
            c: stmt.excluded[c]
            for c in (
                "name",
                "is_group",
                "entity_code",
                "owner",
                "account_type",
                "valid_from",
                "valid_to",
            )
        },
    )
    conn.execute(stmt)
    return {
        (d, c): i
        for d, c, i in conn.execute(select(member.c.dim_id, member.c.code, member.c.member_id))
    }


def _write_hierarchy(
    conn: Connection,
    hier_id: int,
    dim_id: int,
    rows: list[dict[str, object]],
    ids: dict[tuple[int, str], int],
) -> None:
    conn.execute(delete(hier_edge).where(hier_edge.c.hier_id == hier_id))
    conn.execute(
        insert(hier_edge),
        [
            {
                "hier_id": hier_id,
                "member_id": ids[(dim_id, str(r["member_code"]))],
                "parent_id": ids[(dim_id, str(r["parent_code"]))]
                if r["parent_code"] is not None
                else None,
                "sort_order": r["sort_order"],
            }
            for r in rows
        ],
    )


def _rebuild_closure(conn: Connection) -> None:
    conn.execute(delete(closure))
    edges = select(hier_edge.c.hier_id, hier_edge.c.member_id, hier_edge.c.parent_id).cte("edges")
    base = select(
        edges.c.hier_id,
        edges.c.member_id.label("anc"),
        edges.c.member_id.label("des"),
        literal(0).label("depth"),
    )
    tree = base.cte("tree", recursive=True)
    step = (
        select(tree.c.hier_id, edges.c.parent_id, tree.c.des, tree.c.depth + 1)
        .join(edges, (edges.c.hier_id == tree.c.hier_id) & (edges.c.member_id == tree.c.anc))
        .where(edges.c.parent_id.is_not(None))
    )
    tree = tree.union_all(step)
    conn.execute(
        insert(closure).from_select(
            ["hier_id", "ancestor_id", "descendant_id", "depth"], select(tree)
        )
    )


def actuals_version_id(conn: Connection) -> int:
    found = conn.execute(
        select(version.c.version_id).where(version.c.code == ACTUALS_CODE)
    ).scalar()
    if found is not None:
        return int(found)
    return int(
        conn.execute(
            insert(version)
            .values(code=ACTUALS_CODE, name="Actuals", kind="actuals")
            .returning(version.c.version_id)
        ).scalar_one()
    )


def _write_actuals(
    conn: Connection,
    actuals: list[dict[str, object]],
    ids: dict[tuple[int, str], int],
    entity: str | None,
) -> int:
    vid = actuals_version_id(conn)
    stmt = delete(fact_cost_center).where(fact_cost_center.c.version_id == vid)
    if entity is not None:
        stmt = stmt.where(fact_cost_center.c.dim_entity == ids[(ENTITY, entity)])
    conn.execute(stmt)
    # The source may deliver several rows per cell (e.g. several documents); sum them.
    cells: dict[tuple[date, int, int, int], Decimal] = defaultdict(Decimal)
    for r in actuals:
        key = (
            r["period"],
            ids[(ENTITY, str(r["entity_code"]))],
            ids[(COST_CENTER, str(r["cost_center_code"]))],
            ids[(ACCOUNT, str(r["account_code"]))],
        )
        cells[key] += cast(Decimal, r["amount"])  # type: ignore[index]
    if cells:
        conn.execute(
            insert(fact_cost_center),
            [
                {
                    "version_id": vid,
                    "layer_id": BASE_LAYER,
                    "measure_id": AMOUNT,
                    "period": p,
                    "dim_entity": e,
                    "dim_cost_center": c,
                    "dim_account": a,
                    "amount": v,
                }
                for (p, e, c, a), v in cells.items()
            ],
        )
    return len(cells)
