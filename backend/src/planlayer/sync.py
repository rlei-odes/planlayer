"""Sync master data and actuals from the source into the app store (ADR 0012, D-27).

Each dataset is read with the admin's query and field mapping, cast to the dataset's contract
(ADR 0004). A sync is planned first: every dataset is read, every code resolved and every
hierarchy built, and the plan lists problems, new, changed and no longer delivered members, and
matches (codes the source delivers that exist as manual members). Nothing is written by a plan.
A run applies the plan in one transaction, refusing while there are problems or unconfirmed
matches. Re-running a sync gives the same result.

Members and edges keep effective values (the controller's overrides win) next to the source's.
"""

import json
import logging
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, cast

import pyarrow as pa
from sqlalchemy import Connection, Engine, delete, func, insert, literal, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError

from planlayer.adapters.source import Query, SourceAdapter
from planlayer.store.schema import (
    ACCOUNT,
    AMOUNT,
    BASE_LAYER,
    COST_CENTER,
    ENTITY,
    H_ACCOUNT,
    H_COST_CENTER,
    NO_ENTITY,
    closure,
    dataset_mapping,
    edge_override,
    fact_cost_center,
    hier_edge,
    hierarchy,
    member,
    member_override,
    member_shadow,
    sync_run,
    version,
)
from planlayer.store.uow import Actor, unit_of_work

log = logging.getLogger(__name__)

ACTUALS_CODE = "ACT"

# Dataset fields and their contract types (ADR 0012). Required fields first.
FIELDS: dict[str, dict[str, pa.DataType]] = {
    "entities": {"code": pa.string(), "name": pa.string()},
    "cost_centers": {
        "code": pa.string(),
        "name": pa.string(),
        "entity_code": pa.string(),
        "owner": pa.string(),
        "valid_from": pa.date32(),
        "valid_to": pa.date32(),
        "is_group": pa.bool_(),
        "sort_order": pa.int32(),
    },
    "accounts": {
        "code": pa.string(),
        "name": pa.string(),
        "account_type": pa.string(),
        "is_group": pa.bool_(),
        "sort_order": pa.int32(),
    },
    "actuals": {
        "period": pa.date32(),
        "entity_code": pa.string(),
        "cost_center_code": pa.string(),
        "account_code": pa.string(),
        "amount": pa.decimal128(18, 2),
    },
}
REQUIRED: dict[str, tuple[str, ...]] = {
    "entities": ("code", "name"),
    "cost_centers": ("code", "name"),
    "accounts": ("code", "name"),
    "actuals": ("period", "entity_code", "cost_center_code", "account_code", "amount"),
}
DATASETS = tuple(FIELDS)
# Member datasets: dimension and hierarchy (None: no hierarchy).
DIMENSIONS: dict[str, tuple[int, int | None]] = {
    "entities": (ENTITY, None),
    "cost_centers": (COST_CENTER, H_COST_CENTER),
    "accounts": (ACCOUNT, H_ACCOUNT),
}
LABEL = {ENTITY: "entity", COST_CENTER: "cost center", ACCOUNT: "account"}


class SyncError(Exception):
    """The sync can't run as it is; nothing was written."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        shown = "; ".join(problems[:10])
        more = f" (and {len(problems) - 10} more)" if len(problems) > 10 else ""
        super().__init__(f"{len(problems)} problem(s): {shown}{more}")


class MappingError(ValueError):
    """A mapping that can't be saved as it is."""


# --- Mappings -------------------------------------------------------------------------------------


@dataclass
class Mapping:
    dataset: str
    query: str
    fields: dict[str, str]  # field -> source column
    hierarchy: dict[str, Any] | None = None  # {"parent": col} or {"levels": [{"code", "name"}]}

    def validate(self) -> None:
        if self.dataset not in FIELDS:
            raise MappingError(f"unknown dataset {self.dataset}")
        if not self.query.strip():
            raise MappingError("the query is empty")
        unknown = set(self.fields) - set(FIELDS[self.dataset])
        if unknown:
            raise MappingError(f"unknown field(s): {', '.join(sorted(unknown))}")
        missing = [f for f in REQUIRED[self.dataset] if not self.fields.get(f)]
        if missing:
            raise MappingError(f"name a column for: {', '.join(missing)}")
        has_tree = DIMENSIONS.get(self.dataset, (0, None))[1] is not None
        h = self.hierarchy
        if not has_tree:
            if h:
                raise MappingError(f"{self.dataset} has no hierarchy")
            return
        if not h or (("parent" in h) == ("levels" in h)):
            raise MappingError("name a parent column, or level columns")
        if "levels" in h and (
            not h["levels"] or any(not lv.get("code") or not lv.get("name") for lv in h["levels"])
        ):
            raise MappingError("every level needs a code and a name column")

    def columns(self) -> dict[str, str]:
        """Contract field -> source column, hierarchy columns included."""
        out = {f: c for f, c in self.fields.items() if c}
        h = self.hierarchy or {}
        if "parent" in h:
            out["__parent"] = h["parent"]
        for i, lv in enumerate(h.get("levels", [])):
            out[f"__l{i}_code"], out[f"__l{i}_name"] = lv["code"], lv["name"]
        return out

    def contract(self) -> pa.Schema:
        types = FIELDS[self.dataset]
        return pa.schema([(f, types.get(f, pa.string())) for f in self.columns()])


def mappings(conn: Connection) -> dict[str, Mapping]:
    return {
        r.dataset: Mapping(r.dataset, r.query, dict(r.fields), r.hierarchy)
        for r in conn.execute(select(dataset_mapping))
    }


def save_mapping(engine: Engine, actor: Actor, m: Mapping) -> None:
    m.validate()
    with unit_of_work(engine, actor) as conn:
        stmt = pg_insert(dataset_mapping).values(
            dataset=m.dataset,
            query=m.query,
            fields=m.fields,
            hierarchy=m.hierarchy,
            updated_by=actor.login,
        )
        conn.execute(
            stmt.on_conflict_do_update(
                index_elements=[dataset_mapping.c.dataset],
                set_={
                    "query": stmt.excluded.query,
                    "fields": stmt.excluded.fields,
                    "hierarchy": stmt.excluded.hierarchy,
                    "updated_by": actor.login,
                    "updated_at": func.now(),
                },
            )
        )


def delete_mapping(engine: Engine, actor: Actor, dataset: str) -> None:
    with unit_of_work(engine, actor) as conn:
        conn.execute(delete(dataset_mapping).where(dataset_mapping.c.dataset == dataset))


def read(adapter: SourceAdapter, m: Mapping, limit: int | None = None) -> list[dict[str, Any]]:
    """The mapped dataset's rows, cast to its contract."""
    sql = m.query if limit is None else f"SELECT * FROM ({m.query}) q LIMIT {int(limit)}"
    contract = m.contract()
    batches = list(adapter.read(Query(sql), contract, m.columns()))
    rows: list[dict[str, Any]] = pa.Table.from_batches(batches, schema=contract).to_pylist()
    return rows


def default_mappings(schema: str) -> list[Mapping]:
    """Queries on the contract views of the generated test warehouse (tools/gen_testdata)."""
    s = schema
    hier = "{s}.v_{d}_hierarchy h LEFT JOIN {s}.v_{d}s c ON c.{d}_code = h.member_code"
    return [
        Mapping(
            "entities",
            f"SELECT entity_code, entity_name FROM {s}.v_entities",
            {"code": "entity_code", "name": "entity_name"},
        ),
        Mapping(
            "cost_centers",
            "SELECT h.member_code, h.member_name, h.parent_code, h.is_group, h.sort_order, "
            "c.entity_code, c.owner, c.valid_from, c.valid_to "
            f"FROM {hier.format(s=s, d='cost_center')}",
            {
                "code": "member_code",
                "name": "member_name",
                "is_group": "is_group",
                "sort_order": "sort_order",
                "entity_code": "entity_code",
                "owner": "owner",
                "valid_from": "valid_from",
                "valid_to": "valid_to",
            },
            {"parent": "parent_code"},
        ),
        Mapping(
            "accounts",
            "SELECT h.member_code, h.member_name, h.parent_code, h.is_group, h.sort_order, "
            f"c.account_type FROM {hier.format(s=s, d='account')}",
            {
                "code": "member_code",
                "name": "member_name",
                "is_group": "is_group",
                "sort_order": "sort_order",
                "account_type": "account_type",
            },
            {"parent": "parent_code"},
        ),
        Mapping(
            "actuals",
            "SELECT period, entity_code, cost_center_code, account_code, amount "
            f"FROM {s}.v_gl_actuals_natural",
            {f: f for f in REQUIRED["actuals"]},
        ),
    ]


# --- Plan -----------------------------------------------------------------------------------------


@dataclass
class SourceMember:
    code: str
    name: str
    is_group: bool
    parent: str | None
    sort_order: int
    attrs: dict[str, Any] = field(default_factory=dict)


@dataclass
class Match:
    dimension: str  # entity / cost center / account
    code: str
    name: str

    @property
    def key(self) -> str:
        return f"{self.dimension}:{self.code}"


@dataclass
class SyncPlan:
    problems: list[str] = field(default_factory=list)
    new: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    gone: list[str] = field(default_factory=list)  # source members no longer delivered
    matches: list[Match] = field(default_factory=list)
    actual_cells: int | None = None  # None: actuals not mapped
    datasets: list[str] = field(default_factory=list)
    # What a run writes:
    members: dict[int, list[SourceMember]] = field(default_factory=dict)
    actuals: dict[tuple[date, str, str, str], Decimal] = field(default_factory=dict)


def _members(
    dataset: str, rows: list[dict[str, Any]], m: Mapping
) -> tuple[list[SourceMember], list[str]]:
    """Rows to members, building the hierarchy in either form."""
    problems: list[str] = []
    attrs = [f for f in m.fields if f not in ("code", "name", "is_group", "sort_order")]
    out: dict[str, SourceMember] = {}
    h = m.hierarchy or {}

    def add(sm: SourceMember) -> None:
        known = out.get(sm.code)
        if known is None:
            out[sm.code] = sm
        elif known.parent != sm.parent or known.is_group != sm.is_group:
            problems.append(f"{dataset}: {sm.code} appears twice with different places")
        elif not known.is_group:
            problems.append(f"{dataset}: duplicate code {sm.code}")

    if "levels" in h:
        for i, r in enumerate(rows):
            parent: str | None = None
            for lv in range(len(h["levels"])):
                code, name = r.get(f"__l{lv}_code"), r.get(f"__l{lv}_name")
                if code in (None, ""):
                    continue
                add(SourceMember(str(code), str(name or code), True, parent, i))
                parent = str(code)
            leaf = SourceMember(
                str(r["code"]), str(r["name"]), False, parent, _order(r, i),
                {a: r.get(a) for a in attrs},
            )  # fmt: skip
            add(leaf)
    else:
        parents = {r.get("__parent") for r in rows} if "parent" in h else set()
        for i, r in enumerate(rows):
            is_group = r.get("is_group")
            parent = r.get("__parent") if "parent" in h else None
            add(
                SourceMember(
                    str(r["code"]),
                    str(r["name"]),
                    bool(is_group) if is_group is not None else r["code"] in parents,
                    None if parent in (None, "") else str(parent),
                    _order(r, i),
                    {a: r.get(a) for a in attrs},
                )
            )
    found = set(out)
    for sm in out.values():
        if sm.parent is not None and sm.parent not in found:
            problems.append(f"{dataset}: {sm.code} has unknown parent {sm.parent}")
    return list(out.values()), problems


def _order(row: dict[str, Any], i: int) -> int:
    value = row.get("sort_order")
    return i if value is None else int(value)


def _cycles(label: str, parent: dict[str, str | None]) -> list[str]:
    for start in parent:
        seen: set[str] = set()
        node: str | None = start
        while node is not None:
            if node in seen:
                return [f"{label}: cycle through {node}"]
            seen.add(node)
            node = parent.get(node)
    return []


# A query the source refuses, or values that don't fit the contract: reported, not raised, so
# the admin sees every dataset's problems at once.
SOURCE_ERRORS = (DBAPIError, pa.ArrowException)


def _failed(dataset: str, e: Exception) -> str:
    log.warning("sync: reading %s failed: %s", dataset, e)
    detail = str(getattr(e, "orig", None) or e).splitlines()[0]
    return f"{dataset}: reading failed: {detail}"


def plan(engine: Engine, adapter: SourceAdapter, entity: str | None = None) -> SyncPlan:
    p = SyncPlan()
    with engine.connect() as conn:
        maps = mappings(conn)
        p.datasets = [d for d in DATASETS if d in maps]
        existing = {
            (r.dim_id, r.code): r
            for r in conn.execute(
                select(
                    member.c.member_id,
                    member.c.dim_id,
                    member.c.code,
                    member.c.origin,
                    member.c.source_name,
                    member.c.name,
                    member.c.is_group,
                    member.c.owner,
                    member.c.entity_code,
                    member.c.account_type,
                    member.c.valid_from,
                    member.c.valid_to,
                )
            )
        }
        ids = {r.member_id: code for (_, code), r in existing.items()}
        edges = {(r.hier_id, ids[r.member_id]): r for r in conn.execute(select(hier_edge))}
        overrides = {
            (r.hier_id, ids[r.member_id]): ids.get(r.parent_id) if r.parent_id else None
            for r in conn.execute(select(edge_override))
        }

    for dataset, (dim, hier_id) in DIMENSIONS.items():
        if dataset not in maps:
            continue
        try:
            rows = read(adapter, maps[dataset])
        except SOURCE_ERRORS as e:
            p.problems.append(_failed(dataset, e))
            continue
        members, problems = _members(dataset, rows, maps[dataset])
        p.problems += problems
        p.members[dim] = members
        label = LABEL[dim]
        delivered = {sm.code for sm in members}
        for sm in members:
            known = existing.get((dim, sm.code))
            if known is None:
                p.new.append(f"{label} {sm.code}")
            elif known.origin == "manual":
                p.matches.append(Match(label, sm.code, sm.name))
            else:
                attrs = {a: getattr(known, a) for a in sm.attrs}
                parent_now = None
                if hier_id is not None and (hier_id, sm.code) in edges:
                    pid = edges[(hier_id, sm.code)].source_parent_id
                    parent_now = ids.get(pid) if pid else None
                if (
                    known.source_name != sm.name
                    or known.is_group != sm.is_group
                    or parent_now != sm.parent
                    or any(attrs[a] != sm.attrs[a] for a in attrs)
                ):
                    p.changed.append(f"{label} {sm.code}")
        p.gone += [
            f"{label} {code}"
            for (d, code), r in existing.items()
            if d == dim and r.origin == "source" and code not in delivered
        ]
        if hier_id is not None:
            # The hierarchy as it will be: the source's edges with overrides, plus manual ones.
            parent: dict[str, str | None] = {
                code: e.parent_id and ids.get(e.parent_id)
                for (h, code), e in edges.items()
                if h == hier_id and code not in delivered
            }
            for sm in members:
                parent[sm.code] = overrides.get((hier_id, sm.code), sm.parent)
            p.problems += _cycles(dataset, parent)
        if dataset == "cost_centers":
            known_entities = {c for (d, c) in existing if d == ENTITY} | {
                sm.code for sm in p.members.get(ENTITY, [])
            }
            for sm in members:
                ent = sm.attrs.get("entity_code")
                if not sm.is_group and ent not in (None, "") and ent not in known_entities:
                    p.problems.append(f"cost center {sm.code}: unknown entity {ent}")

    if "actuals" in maps:
        try:
            rows = read(adapter, maps["actuals"])
        except SOURCE_ERRORS as e:
            p.problems.append(_failed("actuals", e))
            rows = []
        rows = [r for r in rows if entity is None or r["entity_code"] == entity]
        codes: dict[int, set[str]] = defaultdict(set)
        for (d, c), row in existing.items():
            if not row.is_group:
                codes[d].add(c)
        for d, members in p.members.items():
            codes[d] |= {sm.code for sm in members if not sm.is_group}
        for fld, dim in (
            ("entity_code", ENTITY),
            ("cost_center_code", COST_CENTER),
            ("account_code", ACCOUNT),
        ):
            unknown = sorted({str(r[fld]) for r in rows} - codes[dim])
            p.problems += [f"actuals: unknown {LABEL[dim]} {c}" for c in unknown]
        # The source may deliver several rows per cell (e.g. several documents); sum them.
        cells: dict[tuple[date, str, str, str], Decimal] = defaultdict(Decimal)
        for r in rows:
            key = (r["period"], r["entity_code"], r["cost_center_code"], r["account_code"])
            cells[key] += cast(Decimal, r["amount"])
        p.actuals = dict(cells)
        p.actual_cells = len(cells)
    return p


# --- Run ------------------------------------------------------------------------------------------


@dataclass
class SyncResult:
    members: int
    actual_rows: int
    matched: list[str]


def run(
    engine: Engine,
    adapter: SourceAdapter,
    actor: Actor,
    entity: str | None = None,
    confirm: Iterable[str] = (),
) -> SyncResult:
    """Plan and apply. ``confirm`` lists the matches (``Match.key``) the user confirmed; a run
    refuses while there are problems or unconfirmed matches."""
    p = plan(engine, adapter, entity)
    if p.problems:
        raise SyncError(p.problems)
    unconfirmed = [m.key for m in p.matches if m.key not in set(confirm)]
    if unconfirmed:
        raise SyncError([f"confirm the match with the source: {k}" for k in unconfirmed])

    with unit_of_work(engine, actor) as conn:
        run_id = conn.execute(
            insert(sync_run)
            .values(actor=actor.login, entity_filter=entity)
            .returning(sync_run.c.id)
        ).scalar_one()
        for match in p.matches:
            _shadow(conn, match, actor)
        for dim, members in p.members.items():
            _write_members(conn, dim, members)
        ids = {
            (d, c): i
            for d, c, i in conn.execute(select(member.c.dim_id, member.c.code, member.c.member_id))
        }
        for dim, hier_id in DIMENSIONS.values():
            if hier_id is not None and dim in p.members:
                _write_hierarchy(conn, hier_id, dim, p.members[dim], ids)
        rebuild_closure(conn)
        n = _write_actuals(conn, p.actuals, ids, entity) if p.actual_cells is not None else 0
        summary = {
            "datasets": p.datasets,
            "new": len(p.new),
            "changed": len(p.changed),
            "gone": len(p.gone),
            "matched": [m.key for m in p.matches],
        }
        conn.execute(
            update(sync_run)
            .where(sync_run.c.id == run_id)
            .values(
                finished_at=func.now(),
                members=sum(len(ms) for ms in p.members.values()),
                actual_rows=n,
                summary=summary,
            )
        )
    return SyncResult(sum(len(ms) for ms in p.members.values()), n, [m.key for m in p.matches])


def _shadow(conn: Connection, match: Match, actor: Actor) -> None:
    """Keep a manual member's data before the source takes it over."""
    dim = next(d for d, label in LABEL.items() if label == match.dimension)
    m = conn.execute(
        select(member).where(member.c.dim_id == dim, member.c.code == match.code)
    ).one()
    parent = member.alias("parent")
    parents = {
        h: p
        for h, p in conn.execute(
            select(hierarchy.c.code, parent.c.code)
            .select_from(hier_edge)
            .join(hierarchy, hierarchy.c.hier_id == hier_edge.c.hier_id)
            .outerjoin(parent, parent.c.member_id == hier_edge.c.parent_id)
            .where(hier_edge.c.member_id == m.member_id)
        )
    }
    attributes = {
        k: (v.isoformat() if isinstance(v, date) else v)
        for k, v in m._mapping.items()
        if k in ("is_group", "entity_code", "owner", "account_type", "valid_from", "valid_to")
    }
    conn.execute(
        insert(member_shadow).values(
            member_id=m.member_id,
            code=m.code,
            name=m.name,
            attributes=json.loads(json.dumps(attributes)),
            parents=parents,
            confirmed_by=actor.login,
        )
    )


def _write_members(conn: Connection, dim: int, members: list[SourceMember]) -> None:
    if not members:
        return
    renamed = {
        mid: name
        for mid, name in conn.execute(select(member_override.c.member_id, member_override.c.name))
    }
    current = dict(
        conn.execute(select(member.c.code, member.c.member_id).where(member.c.dim_id == dim)).all()
    )
    rows = []
    for sm in members:
        a = sm.attrs
        override = renamed.get(current.get(sm.code, -1))
        rows.append(
            {
                "dim_id": dim,
                "code": sm.code,
                "name": override or sm.name,
                "source_name": sm.name,
                "is_group": sm.is_group,
                "origin": "source",
                "entity_code": (a.get("entity_code") or NO_ENTITY)
                if dim == COST_CENTER and not sm.is_group
                else None,
                "owner": a.get("owner"),
                "account_type": a.get("account_type"),
                "valid_from": a.get("valid_from"),
                "valid_to": a.get("valid_to"),
            }
        )
    stmt = pg_insert(member).values(rows)
    conn.execute(
        stmt.on_conflict_do_update(
            index_elements=[member.c.dim_id, member.c.code],
            set_={
                c: stmt.excluded[c]
                for c in (
                    "name",
                    "source_name",
                    "is_group",
                    "origin",
                    "entity_code",
                    "owner",
                    "account_type",
                    "valid_from",
                    "valid_to",
                )
            },
        )
    )


def _write_hierarchy(
    conn: Connection,
    hier_id: int,
    dim: int,
    members: list[SourceMember],
    ids: dict[tuple[int, str], int],
) -> None:
    delivered = [ids[(dim, sm.code)] for sm in members]
    # The source's edges are replaced; a taken-over member's manual edge too.
    conn.execute(
        delete(hier_edge).where(
            hier_edge.c.hier_id == hier_id,
            or_(hier_edge.c.origin == "source", hier_edge.c.member_id.in_(delivered)),
        )
    )
    moved = {
        m: p
        for m, p in conn.execute(
            select(edge_override.c.member_id, edge_override.c.parent_id).where(
                edge_override.c.hier_id == hier_id
            )
        )
    }
    rows = []
    for sm in members:
        mid = ids[(dim, sm.code)]
        source_parent = ids[(dim, sm.parent)] if sm.parent is not None else None
        rows.append(
            {
                "hier_id": hier_id,
                "member_id": mid,
                "parent_id": moved.get(mid, source_parent),
                "source_parent_id": source_parent,
                "sort_order": sm.sort_order,
                "origin": "source",
            }
        )
    if rows:
        conn.execute(insert(hier_edge), rows)


def rebuild_closure(conn: Connection) -> None:
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
    cells: dict[tuple[date, str, str, str], Decimal],
    ids: dict[tuple[int, str], int],
    entity: str | None,
) -> int:
    vid = actuals_version_id(conn)
    stmt = delete(fact_cost_center).where(fact_cost_center.c.version_id == vid)
    if entity is not None:
        stmt = stmt.where(fact_cost_center.c.dim_entity == ids[(ENTITY, entity)])
    conn.execute(stmt)
    if cells:
        conn.execute(
            insert(fact_cost_center),
            [
                {
                    "version_id": vid,
                    "layer_id": BASE_LAYER,
                    "measure_id": AMOUNT,
                    "period": p,
                    "dim_entity": ids[(ENTITY, e)],
                    "dim_cost_center": ids[(COST_CENTER, c)],
                    "dim_account": ids[(ACCOUNT, a)],
                    "amount": v,
                }
                for (p, e, c, a), v in cells.items()
            ],
        )
    return len(cells)
