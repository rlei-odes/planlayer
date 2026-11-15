"""Sync master data and actuals from the source into the app store (ADR 0012, D-27).

Each dataset is read with the admin's query and field mapping, cast to the dataset's contract
(ADR 0004). A sync is planned first: every dataset is read, every code resolved and every
hierarchy built, and the plan lists problems, new, changed and no longer delivered members, and
matches (codes the source delivers that exist as manual members). Nothing is written by a plan.
A run applies the plan in one transaction, refusing while there are problems or unconfirmed
matches. Re-running a sync gives the same result.

Members and edges keep effective values (the controller's overrides win) next to the source's.
Master data only grows: a member the source stops delivering keeps its values and its place in
the hierarchy.

Actuals and actual hours are synced for a window of fiscal years. Only the window's periods are
replaced; periods outside it stay as they are. The default window is what the setup and open
rounds compare with: their fiscal years and the years before.
"""

import json
import logging
import time
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal
from typing import Any, cast

import pyarrow as pa
from sqlalchemy import (
    Connection,
    Engine,
    delete,
    func,
    insert,
    literal,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError

from planlayer.access import system_user
from planlayer.adapters.source import Query, SourceAdapter
from planlayer.domain.periods import fiscal_year_of, fiscal_year_periods
from planlayer.store.bulk import insert_all
from planlayer.store.schema import (
    ACCOUNT,
    ACTIVITY_TYPE,
    AMOUNT,
    BASE_LAYER,
    COST_CENTER,
    ENTITY,
    FTE,
    H_ACCOUNT,
    H_COST_CENTER,
    HEADCOUNT,
    HOURS,
    NO_ENTITY,
    STAFF_CATEGORY,
    STAFF_TYPE,
    WORK_CENTER,
    closure,
    dataset_mapping,
    edge_override,
    fact_capacity,
    fact_cost_center,
    fact_workforce,
    hier_edge,
    hierarchy,
    hr_pay,
    hr_staff,
    installation,
    member,
    member_override,
    member_shadow,
    planning_round,
    sync_run,
    version,
    work_center_rate,
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
    # Capacity planning (ADR 0013).
    "activity_types": {
        "code": pa.string(),
        "name": pa.string(),
        "account_code": pa.string(),
        "variable_account_code": pa.string(),
    },
    "work_centers": {"code": pa.string(), "name": pa.string(), "cost_center_code": pa.string()},
    "work_center_rates": {
        "work_center_code": pa.string(),
        "activity_type_code": pa.string(),
        "rate": pa.decimal128(18, 4),
    },
    "capacity_actuals": {
        "period": pa.date32(),
        "work_center_code": pa.string(),
        "activity_type_code": pa.string(),
        "hours": pa.decimal128(18, 2),
    },
    # Workforce planning (ADR 0020).
    "staff_types": {"code": pa.string(), "name": pa.string(), "external": pa.bool_()},
    "staff_categories": {"code": pa.string(), "name": pa.string()},
    "staff": {
        "code": pa.string(),
        "cost_center_code": pa.string(),
        "title": pa.string(),
        "staff_type_code": pa.string(),
        "staff_category_code": pa.string(),
        "person_name": pa.string(),
        "entry_date": pa.date32(),
        "leave_date": pa.date32(),
        "contract_percent": pa.decimal128(5, 2),
        # Pay (ADR 0022): stored apart, under the pay rules.
        "monthly_salary": pa.decimal128(18, 2),
    },
    "workforce_actuals": {
        "period": pa.date32(),
        "cost_center_code": pa.string(),
        "staff_type_code": pa.string(),
        "staff_category_code": pa.string(),
        "headcount": pa.decimal128(18, 2),
        "fte": pa.decimal128(18, 2),
    },
}
REQUIRED: dict[str, tuple[str, ...]] = {
    "entities": ("code", "name"),
    "cost_centers": ("code", "name"),
    "accounts": ("code", "name"),
    "actuals": ("period", "entity_code", "cost_center_code", "account_code", "amount"),
    "activity_types": ("code", "name"),
    "work_centers": ("code", "name"),
    "work_center_rates": ("work_center_code", "activity_type_code", "rate"),
    "capacity_actuals": ("period", "work_center_code", "activity_type_code", "hours"),
    "staff_types": ("code", "name"),
    "staff_categories": ("code", "name"),
    "staff": ("code", "cost_center_code", "title", "staff_type_code", "staff_category_code"),
    "workforce_actuals": (
        "period",
        "cost_center_code",
        "staff_type_code",
        "staff_category_code",
        "headcount",
    ),
}
DATASETS = tuple(FIELDS)
# Member datasets: dimension and hierarchy (None: no hierarchy). Work centers and activity types
# come after the cost centers and accounts their codes refer to.
DIMENSIONS: dict[str, tuple[int, int | None]] = {
    "entities": (ENTITY, None),
    "cost_centers": (COST_CENTER, H_COST_CENTER),
    "accounts": (ACCOUNT, H_ACCOUNT),
    "activity_types": (ACTIVITY_TYPE, None),
    "work_centers": (WORK_CENTER, None),
    "staff_types": (STAFF_TYPE, None),
    "staff_categories": (STAFF_CATEGORY, None),
}
LABEL = {
    ENTITY: "entity",
    COST_CENTER: "cost center",
    ACCOUNT: "account",
    ACTIVITY_TYPE: "activity type",
    WORK_CENTER: "work center",
    STAFF_TYPE: "staff type",
    STAFF_CATEGORY: "staff category",
}
# Member attributes kept on ``member``, as codes.
ATTRIBUTES = (
    "entity_code",
    "owner",
    "account_type",
    "valid_from",
    "valid_to",
    "cost_center_code",
    "account_code",
    "variable_account_code",
    "external",
)


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


# Pay in the dataset ``staff`` (ADR 0025): read from the source only when the admin turns it on.
PAY_FIELDS = ("monthly_salary",)


def pay_from_source(conn: Connection) -> bool:
    return bool(conn.execute(select(installation.c.pay_from_source)).scalar_one())


def set_pay_from_source(engine: Engine, actor: Actor, on: bool) -> None:
    """On, the sync replaces HR's salaries with the source's; off, HR uploads them."""
    with unit_of_work(engine, actor) as conn:
        conn.execute(update(installation).values(pay_from_source=on))


def staff_mapping(m: Mapping, pay: bool) -> Mapping:
    """The staff mapping as the sync reads it: without its pay columns unless ``pay``."""
    if pay:
        return m
    return replace(m, fields={f: c for f, c in m.fields.items() if f not in PAY_FIELDS})


# Datasets of values per period: synced for a window of fiscal years.
PERIOD_DATASETS = ("actuals", "capacity_actuals", "workforce_actuals")


@dataclass(frozen=True)
class Window:
    """Fiscal years ``first`` to ``last``, both included, as the periods they span."""

    first: int
    last: int
    start_month: int = 1

    @property
    def period_from(self) -> date:
        return fiscal_year_periods(self.first, self.start_month)[0]

    @property
    def period_to(self) -> date:
        return fiscal_year_periods(self.last, self.start_month)[-1]

    @property
    def after(self) -> date:
        """The first day after the window."""
        return fiscal_year_periods(self.last + 1, self.start_month)[0]


def start_month(conn: Connection) -> int:
    return int(conn.execute(select(installation.c.fiscal_year_start_month)).scalar_one())


def default_window(conn: Connection) -> Window | None:
    """The fiscal years the setup and open rounds compare with: each round's year and the year
    before. None without such rounds: every year the source delivers."""
    years = set(
        conn.execute(
            select(planning_round.c.fiscal_year).where(planning_round.c.status != "closed")
        ).scalars()
    )
    if not years:
        return None
    return Window(min(years) - 1, max(years), start_month(conn))


def window_of(conn: Connection, years: tuple[int, int] | None) -> Window | None:
    """The window for the years asked for, or the default one."""
    if years is None:
        return default_window(conn)
    first, last = years
    if first > last:
        raise SyncError([f"the window's first year {first} is after its last year {last}"])
    return Window(first, last, start_month(conn))


@dataclass
class Years:
    """What the Sync page offers: the default window, and per fiscal year the actual cells
    stored, for the years stored or planned (and the one after the last round)."""

    default: Window | None
    stored: dict[int, int]


def years(engine: Engine) -> Years:
    # Counted as the system user, the sync's writer (row-level security, ADR 0009).
    with unit_of_work(engine, system_user(engine)) as conn:
        default = default_window(conn)
        stored = _stored_actuals(conn, None, start_month(conn), None)
        rounds = set(conn.execute(select(planning_round.c.fiscal_year)).scalars())
    known = set(stored) | rounds | ({default.first, default.last} if default else set())
    if not known:
        return Years(default, {})
    span = range(min(known), max(known) + 2)
    return Years(default, {y: stored.get(y, 0) for y in span})


def read(
    adapter: SourceAdapter, m: Mapping, limit: int | None = None, window: Window | None = None
) -> list[dict[str, Any]]:
    """The mapped dataset's rows, cast to its contract. A dataset of values per period reads only
    the window's periods: the read adds a filter on the column mapped to ``period`` to the
    admin's query, which the source applies before sending rows. A period is any day of its
    month (a date, a timestamp or ISO text); it counts as the month, so daily rows add up."""
    within = None
    if m.dataset in PERIOD_DATASETS and window is not None:
        within = ("period", window.period_from, window.after)
    query = Query(m.query, within, limit)
    contract = m.contract()
    batches = list(adapter.read(query, contract, m.columns()))
    rows: list[dict[str, Any]] = pa.Table.from_batches(batches, schema=contract).to_pylist()
    if m.dataset in PERIOD_DATASETS:
        for r in rows:
            if r["period"] is not None:
                r["period"] = r["period"].replace(day=1)
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
            "SELECT posting_month, company_code, cost_center, gl_account, amount_eur "
            f"FROM {s}.v_gl_actuals_natural",
            {
                "period": "posting_month",
                "entity_code": "company_code",
                "cost_center_code": "cost_center",
                "account_code": "gl_account",
                "amount": "amount_eur",
            },
        ),
        Mapping(
            "activity_types",
            "SELECT activity_type_code, activity_type_name, account_code, variable_account_code "
            f"FROM {s}.v_activity_types",
            {
                "code": "activity_type_code",
                "name": "activity_type_name",
                "account_code": "account_code",
                "variable_account_code": "variable_account_code",
            },
        ),
        Mapping(
            "work_centers",
            f"SELECT work_center_code, work_center_name, cost_center_code FROM {s}.v_work_centers",
            {
                "code": "work_center_code",
                "name": "work_center_name",
                "cost_center_code": "cost_center_code",
            },
        ),
        Mapping(
            "work_center_rates",
            f"SELECT work_center_code, activity_type_code, rate FROM {s}.v_work_center_rates",
            {f: f for f in REQUIRED["work_center_rates"]},
        ),
        Mapping(
            "capacity_actuals",
            "SELECT posting_month, work_center, activity_type, actual_hours "
            f"FROM {s}.v_capacity_actuals",
            {
                "period": "posting_month",
                "work_center_code": "work_center",
                "activity_type_code": "activity_type",
                "hours": "actual_hours",
            },
        ),
        # Workforce planning (ADR 0020): staff types are Planlayer's seeded ones.
        Mapping(
            "staff_categories",
            f"SELECT job_family, job_family_name FROM {s}.v_job_families",
            {"code": "job_family", "name": "job_family_name"},
        ),
        Mapping(
            "staff",
            "SELECT personnel_no, cost_center, job_title, employee_name, employee_group, "
            f"job_family, entry_date, exit_date, contract_pct, monthly_salary FROM {s}.v_staff",
            {
                "code": "personnel_no",
                "cost_center_code": "cost_center",
                "title": "job_title",
                "person_name": "employee_name",
                "staff_type_code": "employee_group",
                "staff_category_code": "job_family",
                "entry_date": "entry_date",
                "leave_date": "exit_date",
                "contract_percent": "contract_pct",
                "monthly_salary": "monthly_salary",
            },
        ),
        Mapping(
            "workforce_actuals",
            "SELECT snapshot_month, cost_center, employee_group, job_family, headcount, fte "
            f"FROM {s}.v_workforce_actuals",
            {
                "period": "snapshot_month",
                "cost_center_code": "cost_center",
                "staff_type_code": "employee_group",
                "staff_category_code": "job_family",
                "headcount": "headcount",
                "fte": "fte",
            },
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
class YearCount:
    """Actual cells of one fiscal year: stored now, and delivered by the source."""

    fiscal_year: int
    stored: int
    delivered: int


@dataclass
class SyncPlan:
    window: Window | None = None  # None: every year
    years: list[YearCount] = field(default_factory=list)  # actuals per fiscal year in the window
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
    rates: dict[tuple[str, str], Decimal] | None = None  # None: rates not mapped
    capacity: dict[tuple[date, str, str], Decimal] | None = None  # None: hours not mapped
    staff: list[dict[str, Any]] | None = None  # None: HR's staff list not mapped
    pay_from_source: bool = False  # the staff list's salaries replace HR's (ADR 0025)
    # Month-end FTE and headcount per period, cost center, staff type and category.
    workforce: dict[tuple[date, str, str, str], tuple[Decimal, Decimal | None]] | None = None


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


def plan(
    engine: Engine,
    adapter: SourceAdapter,
    entity: str | None = None,
    years: tuple[int, int] | None = None,
) -> SyncPlan:
    """``years``: the window of fiscal years for actuals and actual hours; None for the
    default window."""
    p = SyncPlan()
    # Actuals are counted as the system user, the sync's writer: row-level security shows the
    # admin no actuals (ADR 0009), and the preview compares what is stored with what comes.
    with unit_of_work(engine, system_user(engine)) as conn:
        p.window = window_of(conn, years)
        fy_start = start_month(conn)
        stored_by_year = _stored_actuals(conn, p.window, fy_start, entity)
        maps = mappings(conn)
        p.pay_from_source = pay_from_source(conn)
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
                    *(member.c[a] for a in ATTRIBUTES),
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
                p.new.append(f"{label} {sm.code} {sm.name}")
            elif known.origin == "manual":
                p.matches.append(Match(label, sm.code, sm.name))
            else:
                parent_now = None
                if hier_id is not None and (hier_id, sm.code) in edges:
                    pid = edges[(hier_id, sm.code)].source_parent_id
                    parent_now = ids.get(pid) if pid else None
                was: dict[str, Any] = {
                    "name": known.source_name,
                    "group": known.is_group,
                    "parent": parent_now,
                    **{a: getattr(known, a) for a in sm.attrs},
                }
                now: dict[str, Any] = {
                    "name": sm.name,
                    "group": sm.is_group,
                    "parent": sm.parent,
                    **sm.attrs,
                }
                diffs = [
                    f"{_FIELD_TEXT.get(k, k)} {_shown(was[k])} → {_shown(now[k])}"
                    for k in was
                    if was[k] != now[k]
                ]
                if diffs:
                    p.changed.append(f"{label} {sm.code} {sm.name}: {'; '.join(diffs)}")
        p.gone += [
            f"{label} {code} {r.name}"
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
        for attr, dim_ref in REFERENCES.get(dataset, ()):
            refs = _leaves(existing, p, dim_ref)
            for sm in members:
                ref = sm.attrs.get(attr)
                if ref not in (None, "") and ref not in refs:
                    p.problems.append(f"{label} {sm.code}: unknown {LABEL[dim_ref]} {ref}")

    if "actuals" in maps:
        try:
            rows = read(adapter, maps["actuals"], window=p.window)
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
        by_year: dict[int, int] = defaultdict(int)
        for period, *_ in cells:
            by_year[fiscal_year_of(period, fy_start)] += 1
        p.years = [
            YearCount(y, stored_by_year.get(y, 0), by_year.get(y, 0))
            for y in sorted(set(stored_by_year) | set(by_year))
        ]

    work_centers = _leaves(existing, p, WORK_CENTER)
    activity_types = _leaves(existing, p, ACTIVITY_TYPE)

    def report_unknown(dataset: str, rows: list[dict[str, Any]]) -> None:
        for fld, known, dim in (
            ("work_center_code", work_centers, WORK_CENTER),
            ("activity_type_code", activity_types, ACTIVITY_TYPE),
        ):
            p.problems += [
                f"{dataset}: unknown {LABEL[dim]} {c}"
                for c in sorted({str(r[fld]) for r in rows} - known)
            ]

    if "work_center_rates" in maps:
        try:
            rows = read(adapter, maps["work_center_rates"])
        except SOURCE_ERRORS as e:
            p.problems.append(_failed("work_center_rates", e))
            rows = []
        report_unknown("work_center_rates", rows)
        p.rates = {}
        for r in rows:
            pair = (str(r["work_center_code"]), str(r["activity_type_code"]))
            if pair in p.rates:
                p.problems.append(f"work_center_rates: {pair[0]}/{pair[1]} has two rates")
            if cast(Decimal, r["rate"]) < 0:
                p.problems.append(f"work_center_rates: {pair[0]}/{pair[1]} has a negative rate")
            p.rates[pair] = cast(Decimal, r["rate"])
    if "capacity_actuals" in maps:
        try:
            rows = read(adapter, maps["capacity_actuals"], window=p.window)
        except SOURCE_ERRORS as e:
            p.problems.append(_failed("capacity_actuals", e))
            rows = []
        report_unknown("capacity_actuals", rows)
        hours: dict[tuple[date, str, str], Decimal] = defaultdict(Decimal)
        for r in rows:
            hours[(r["period"], str(r["work_center_code"]), str(r["activity_type_code"]))] += cast(
                Decimal, r["hours"]
            )
        p.capacity = dict(hours)
    _plan_workforce(p, maps, adapter, existing)
    return p


def _plan_workforce(
    p: SyncPlan,
    maps: dict[str, Mapping],
    adapter: SourceAdapter,
    existing: dict[tuple[int, str], Any],
) -> None:
    """HR's staff list and workforce actuals (ADR 0020): every code they name must be known."""
    known = {dim: _leaves(existing, p, dim) for dim in (COST_CENTER, STAFF_TYPE, STAFF_CATEGORY)}

    def report_unknown(dataset: str, rows: list[dict[str, Any]]) -> None:
        for fld, dim in (
            ("cost_center_code", COST_CENTER),
            ("staff_type_code", STAFF_TYPE),
            ("staff_category_code", STAFF_CATEGORY),
        ):
            p.problems += [
                f"{dataset}: unknown {LABEL[dim]} {c}"
                for c in sorted({str(r[fld]) for r in rows} - known[dim])
            ]

    if "staff" in maps:
        try:
            rows = read(adapter, staff_mapping(maps["staff"], p.pay_from_source))
        except SOURCE_ERRORS as e:
            p.problems.append(_failed("staff", e))
            rows = []
        report_unknown("staff", rows)
        seen: set[str] = set()
        for r in rows:
            code, percent = str(r["code"]), r.get("contract_percent")
            if code in seen:
                p.problems.append(f"staff: duplicate code {code}")
            seen.add(code)
            if percent is not None and not 0 < percent <= 100:
                p.problems.append(f"staff: {code} has a contract percentage of {percent}")
            if r.get("entry_date") and r.get("leave_date") and r["leave_date"] < r["entry_date"]:
                p.problems.append(f"staff: {code} leaves before entering")
            if not str(r["title"] or "").strip():
                p.problems.append(f"staff: {code} has no title")
            if r.get("monthly_salary") is not None and r["monthly_salary"] < 0:
                p.problems.append(f"staff: {code} has a negative salary")
        p.staff = rows
    if "workforce_actuals" in maps:
        try:
            rows = read(adapter, maps["workforce_actuals"], window=p.window)
        except SOURCE_ERRORS as e:
            p.problems.append(_failed("workforce_actuals", e))
            rows = []
        report_unknown("workforce_actuals", rows)
        cells: dict[tuple[date, str, str, str], tuple[Decimal, Decimal | None]] = {}
        for r in rows:
            key = (
                r["period"],
                str(r["cost_center_code"]),
                str(r["staff_type_code"]),
                str(r["staff_category_code"]),
            )
            heads, fte = cast(Decimal, r["headcount"]), cast(Decimal | None, r.get("fte"))
            if heads < 0 or (fte is not None and fte < 0):
                p.problems.append(f"workforce_actuals: negative values for {'/'.join(key[1:])}")
            # Several rows per cell (e.g. per person) add up.
            before = cells.get(key)
            if before is not None:
                fte = fte if before[1] is None else before[1] + (fte or Decimal(0))
                heads += before[0]
            cells[key] = (heads, fte)
        p.workforce = cells


# How a changed member's fields read in the sync preview.
_FIELD_TEXT = {
    "entity_code": "entity",
    "account_type": "type",
    "valid_from": "valid from",
    "valid_to": "valid to",
    "cost_center_code": "cost center",
    "account_code": "account",
    "variable_account_code": "variable account",
    "external": "external",
}


def _shown(value: Any) -> str:
    match value:
        case None | "":
            return "none"
        case bool():
            return "yes" if value else "no"
        case _:
            return str(value)


# Attributes naming a member of another dimension: (attribute, dimension).
REFERENCES: dict[str, tuple[tuple[str, int], ...]] = {
    "work_centers": (("cost_center_code", COST_CENTER),),
    "activity_types": (("account_code", ACCOUNT), ("variable_account_code", ACCOUNT)),
}


def _leaves(existing: dict[tuple[int, str], Any], p: SyncPlan, dim: int) -> set[str]:
    """Leaf codes of a dimension as they will be after the run: stored and delivered ones."""
    return {c for (d, c), r in existing.items() if d == dim and not r.is_group} | {
        sm.code for sm in p.members.get(dim, []) if not sm.is_group
    }


# --- Run ------------------------------------------------------------------------------------------


@dataclass
class SyncResult:
    members: int
    actual_rows: int
    matched: list[str]
    seconds: float = 0.0  # how long the run took, preview included


class _Steps:
    """Times the steps of a run; each is logged, and all are kept in the run's summary."""

    def __init__(self) -> None:
        self.started = time.perf_counter()
        self.last = self.started
        self.seconds: dict[str, float] = {}

    def done(self, step: str, detail: str = "") -> None:
        now = time.perf_counter()
        self.seconds[step] = round(now - self.last, 2)
        log.info("sync: %s in %.1f s%s", step, now - self.last, f" ({detail})" if detail else "")
        self.last = now

    @property
    def total(self) -> float:
        return round(time.perf_counter() - self.started, 2)


def run(
    engine: Engine,
    adapter: SourceAdapter,
    actor: Actor,
    entity: str | None = None,
    confirm: Iterable[str] = (),
    years: tuple[int, int] | None = None,
) -> SyncResult:
    """Plan and apply. ``actor`` is who asked for the run; the system user writes it, as for
    every job (ADR 0009), so an admin without access to cost centers can sync. ``confirm`` lists
    the matches (``Match.key``) the user confirmed; a run refuses while there are problems or
    unconfirmed matches. ``years`` is the window of fiscal years for actuals (see ``plan``)."""
    steps = _Steps()
    p = plan(engine, adapter, entity, years)
    steps.done("read the source", f"{len(p.datasets)} datasets")
    if p.problems:
        raise SyncError(p.problems)
    unconfirmed = [m.key for m in p.matches if m.key not in set(confirm)]
    if unconfirmed:
        raise SyncError([f"confirm the match with the source: {k}" for k in unconfirmed])

    with unit_of_work(engine, system_user(engine)) as conn:
        run_id = conn.execute(
            insert(sync_run)
            .values(actor=actor.login, entity_filter=entity)
            .returning(sync_run.c.id)
        ).scalar_one()
        for match in p.matches:
            _shadow(conn, match, actor)
        for dim, members in p.members.items():
            _write_members(conn, dim, members)
        members_written = sum(len(ms) for ms in p.members.values())
        ids = {
            (d, c): i
            for d, c, i in conn.execute(select(member.c.dim_id, member.c.code, member.c.member_id))
        }
        for dim, hier_id in DIMENSIONS.values():
            if hier_id is not None and dim in p.members:
                _write_hierarchy(conn, hier_id, dim, p.members[dim], ids)
        rebuild_closure(conn)
        steps.done("write members and hierarchies", f"{members_written} members")
        n = (
            _write_actuals(conn, p.actuals, ids, entity, p.window)
            if p.actual_cells is not None
            else 0
        )
        steps.done("write actuals", f"{n} cells")
        if p.rates is not None:
            _write_rates(conn, p.rates, ids)
        if p.capacity is not None:
            hours = _write_capacity_actuals(conn, p.capacity, ids, entity, p.window)
            steps.done("write rates and actual hours", f"{hours} cells")
        if p.staff is not None:
            _write_staff(conn, p.staff, ids, p.pay_from_source)
        if p.workforce is not None:
            cells = _write_workforce_actuals(conn, p.workforce, ids, entity, p.window)
            steps.done("write staff and workforce actuals", f"{cells} cells")
        summary = {
            "window": [p.window.first, p.window.last] if p.window else None,
            "datasets": p.datasets,
            "new": len(p.new),
            "changed": len(p.changed),
            "gone": len(p.gone),
            "matched": [m.key for m in p.matches],
            "seconds": steps.seconds,
        }
        # now() is the transaction's start; the clock gives the real end.
        conn.execute(
            update(sync_run)
            .where(sync_run.c.id == run_id)
            .values(
                finished_at=func.clock_timestamp(),
                members=members_written,
                actual_rows=n,
                summary=summary,
            )
        )
    log.info("sync: done in %.1f s (%d members, %d actual cells)", steps.total, members_written, n)
    return SyncResult(members_written, n, [m.key for m in p.matches], steps.total)


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
                **{attr: a.get(attr) for attr in ATTRIBUTES if attr != "entity_code"},
            }
        )
    stmt = pg_insert(member).values(rows)
    conn.execute(
        stmt.on_conflict_do_update(
            index_elements=[member.c.dim_id, member.c.code],
            set_={
                **{
                    c: stmt.excluded[c]
                    for c in ("name", "source_name", "is_group", "origin", *ATTRIBUTES)
                },
                "missing_since": None,
            },
        )
    )
    # Synced members the source no longer delivers are kept, and marked since when.
    conn.execute(
        update(member)
        .where(
            member.c.dim_id == dim,
            member.c.origin == "source",
            member.c.missing_since.is_(None),
            member.c.code.not_in([sm.code for sm in members]),
        )
        .values(missing_since=func.now())
    )


def _write_hierarchy(
    conn: Connection,
    hier_id: int,
    dim: int,
    members: list[SourceMember],
    ids: dict[tuple[int, str], int],
) -> None:
    delivered = [ids[(dim, sm.code)] for sm in members]
    # The delivered members' edges are replaced, a taken-over member's manual edge too. A member
    # the source no longer delivers keeps its last edge, so its values still roll up.
    conn.execute(
        delete(hier_edge).where(
            hier_edge.c.hier_id == hier_id, hier_edge.c.member_id.in_(delivered)
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


def _stored_actuals(
    conn: Connection, window: Window | None, fy_start: int, entity: str | None
) -> dict[int, int]:
    """Stored actual cells per fiscal year in the window."""
    f = fact_cost_center
    query = (
        select(f.c.period, func.count())
        .join(version, version.c.version_id == f.c.version_id)
        .where(version.c.code == ACTUALS_CODE)
        .group_by(f.c.period)
    )
    if window is not None:
        query = query.where(f.c.period.between(window.period_from, window.period_to))
    if entity is not None:
        ent = member.alias("ent")
        query = query.join(ent, ent.c.member_id == f.c.dim_entity).where(
            ent.c.dim_id == ENTITY, ent.c.code == entity
        )
    out: dict[int, int] = defaultdict(int)
    for period, n in conn.execute(query):
        out[fiscal_year_of(period, fy_start)] += n
    return dict(out)


def _write_actuals(
    conn: Connection,
    cells: dict[tuple[date, str, str, str], Decimal],
    ids: dict[tuple[int, str], int],
    entity: str | None,
    window: Window | None,
) -> int:
    """The source's actuals replace the stored ones of the window's periods."""
    vid = actuals_version_id(conn)
    stmt = delete(fact_cost_center).where(fact_cost_center.c.version_id == vid)
    if entity is not None:
        stmt = stmt.where(fact_cost_center.c.dim_entity == ids[(ENTITY, entity)])
    if window is not None:
        stmt = stmt.where(fact_cost_center.c.period.between(window.period_from, window.period_to))
    conn.execute(stmt)
    insert_all(
        conn,
        fact_cost_center,
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


def _write_rates(
    conn: Connection, rates: dict[tuple[str, str], Decimal], ids: dict[tuple[int, str], int]
) -> None:
    """The source's rates replace the stored ones; rounds keep their own plan rates."""
    conn.execute(delete(work_center_rate))
    if rates:
        conn.execute(
            insert(work_center_rate),
            [
                {
                    "work_center_id": ids[(WORK_CENTER, w)],
                    "activity_type_id": ids[(ACTIVITY_TYPE, t)],
                    "rate": rate,
                }
                for (w, t), rate in rates.items()
            ],
        )


def _write_capacity_actuals(
    conn: Connection,
    cells: dict[tuple[date, str, str], Decimal],
    ids: dict[tuple[int, str], int],
    entity: str | None,
    window: Window | None,
) -> int:
    """Actual hours of the window's periods, booked on each work center's cost center as it is
    now. A work center without a cost center is skipped."""
    vid = actuals_version_id(conn)
    cost_centers = {
        code: (ids.get((COST_CENTER, cc or "")), ids.get((ENTITY, ent or NO_ENTITY)))
        for code, cc, ent in conn.execute(
            select(member.c.code, member.c.cost_center_code, _entity_of_cost_center()).where(
                member.c.dim_id == WORK_CENTER
            )
        )
    }
    stmt = delete(fact_capacity).where(fact_capacity.c.version_id == vid)
    if entity is not None:
        stmt = stmt.where(fact_capacity.c.dim_entity == ids[(ENTITY, entity)])
    if window is not None:
        stmt = stmt.where(fact_capacity.c.period.between(window.period_from, window.period_to))
    conn.execute(stmt)
    rows = []
    for (p, w, t), hours in cells.items():
        cc, ent = cost_centers.get(w, (None, None))
        if cc is None or ent is None or (entity is not None and ent != ids[(ENTITY, entity)]):
            continue
        rows.append(
            {
                "version_id": vid,
                "layer_id": BASE_LAYER,
                "measure_id": HOURS,
                "period": p,
                "dim_entity": ent,
                "dim_cost_center": cc,
                "dim_work_center": ids[(WORK_CENTER, w)],
                "dim_activity_type": ids[(ACTIVITY_TYPE, t)],
                "amount": hours,
            }
        )
    insert_all(conn, fact_capacity, rows)
    return len(rows)


def _entity_of_cost_center() -> Any:
    """The entity code of a work center's cost center, as a scalar subquery."""
    cc = member.alias("cc")
    return (
        select(cc.c.entity_code)
        .where(cc.c.dim_id == COST_CENTER, cc.c.code == member.c.cost_center_code)
        .scalar_subquery()
    )


def _write_staff(
    conn: Connection, rows: list[dict[str, Any]], ids: dict[tuple[int, str], int], pay: bool
) -> None:
    """HR's staff list replaces the stored one; rounds keep their own positions. With ``pay``,
    its salaries replace HR's; without, HR's uploaded salaries stay (ADR 0025)."""
    conn.execute(delete(hr_staff))
    insert_all(
        conn,
        hr_staff,
        [
            {
                "code": str(r["code"]),
                "cost_center_id": ids[(COST_CENTER, str(r["cost_center_code"]))],
                "title": str(r["title"]).strip(),
                "person_name": r.get("person_name"),
                "staff_type_id": ids[(STAFF_TYPE, str(r["staff_type_code"]))],
                "staff_category_id": ids[(STAFF_CATEGORY, str(r["staff_category_code"]))],
                "entry_date": r.get("entry_date"),
                "leave_date": r.get("leave_date"),
                "contract_percent": r.get("contract_percent"),
            }
            for r in rows
        ],
    )
    if not pay:
        return
    # Salaries go to their own table, which only readers of pay read (ADR 0022).
    conn.execute(delete(hr_pay))
    insert_all(
        conn,
        hr_pay,
        [
            {
                "code": str(r["code"]),
                "cost_center_id": ids[(COST_CENTER, str(r["cost_center_code"]))],
                "monthly_salary": r["monthly_salary"],
            }
            for r in rows
            if r.get("monthly_salary") is not None
        ],
    )


def _write_workforce_actuals(
    conn: Connection,
    cells: dict[tuple[date, str, str, str], tuple[Decimal, Decimal | None]],
    ids: dict[tuple[int, str], int],
    entity: str | None,
    window: Window | None,
) -> int:
    """Actual FTE and headcount of the window's periods, on the cost center's entity; with an
    entity filter, only that entity's cost centers."""
    vid = actuals_version_id(conn)
    entities = {
        code: ids.get((ENTITY, ent or NO_ENTITY))
        for code, ent in conn.execute(
            select(member.c.code, member.c.entity_code).where(member.c.dim_id == COST_CENTER)
        )
    }
    f = fact_workforce
    stmt = delete(f).where(f.c.version_id == vid)
    if entity is not None:
        stmt = stmt.where(f.c.dim_entity == ids[(ENTITY, entity)])
    if window is not None:
        stmt = stmt.where(f.c.period.between(window.period_from, window.period_to))
    conn.execute(stmt)
    rows = []
    for (p, cc, t, c), (heads, fte) in cells.items():
        ent = entities.get(cc)
        if ent is None or (entity is not None and ent != ids[(ENTITY, entity)]):
            continue
        for measure, value in ((HEADCOUNT, heads), (FTE, fte)):
            if value is None:
                continue
            rows.append(
                {
                    "version_id": vid,
                    "layer_id": BASE_LAYER,
                    "measure_id": measure,
                    "period": p,
                    "dim_entity": ent,
                    "dim_cost_center": ids[(COST_CENTER, cc)],
                    "dim_staff_type": ids[(STAFF_TYPE, t)],
                    "dim_staff_category": ids[(STAFF_CATEGORY, c)],
                    "amount": value,
                }
            )
    insert_all(conn, f, rows)
    return len(rows)
