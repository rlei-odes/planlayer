"""The planning grid for one cost center (T-25): read it, and apply edits.

Rows are the accounts with prior-year actuals or plan values for the cost center, grouped by
their parent in the account hierarchy, with group subtotals computed here (ADR 0003). Each
plan cell carries its ``row_version``; an edit states the versions it was made against, and a
cell changed by someone else in the meantime is a conflict (D-19). Blank and zero stay
distinct: a blank cell has no row.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal

from sqlalchemy import Connection, Engine, and_, delete, select, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert

from planlayer.domain.periods import fiscal_year_periods
from planlayer.domain.spreading import from_reference, spread
from planlayer.store.schema import (
    ACCOUNT,
    AMOUNT,
    BASE_LAYER,
    COST_CENTER,
    ENTITY,
    H_ACCOUNT,
    fact_cost_center,
    hier_edge,
    installation,
    member,
    version,
)
from planlayer.store.uow import unit_of_work
from planlayer.sync import ACTUALS_CODE

MONTHS = 12


class NotFoundError(LookupError):
    pass


class EditError(ValueError):
    """An edit that can't be applied as sent (bad value, unknown account)."""


@dataclass
class Conflict:
    account_code: str
    month: int


class ConflictError(Exception):
    def __init__(self, conflicts: list[Conflict]) -> None:
        self.conflicts = conflicts
        super().__init__(f"{len(conflicts)} cell(s) changed by someone else")


@dataclass
class Cell:
    value: Decimal | None
    row_version: int | None


@dataclass
class Row:
    kind: Literal["group", "account"]
    code: str
    name: str
    group_code: str
    prior_year: list[Decimal | None]
    plan: list[Cell]


@dataclass
class Grid:
    version_code: str
    version_name: str
    fiscal_year: int
    cost_center_code: str
    cost_center_name: str
    periods: list[date]
    prior_periods: list[date]
    rows: list[Row] = field(default_factory=list)


@dataclass
class Edit:
    account_code: str
    month: int | None  # 0-11, or None for the full year (spread by prior-year actuals)
    value: str | None  # canonical decimal string ("1234.5", "-3"); None = blank
    expected: list[int | None]  # row_version of each month as the client saw it


@dataclass
class _Context:
    version_id: int
    actuals_id: int
    fiscal_year: int
    cost_center_id: int
    entity_id: int
    periods: list[date]
    prior_periods: list[date]


def _context(conn: Connection, version_code: str, cc_code: str) -> tuple[_Context, dict[str, str]]:
    v = conn.execute(
        select(version).where(version.c.code == version_code, version.c.kind != "actuals")
    ).first()
    if v is None:
        raise NotFoundError(f"plan version {version_code} not found")
    cc = conn.execute(
        select(member).where(
            member.c.dim_id == COST_CENTER, member.c.code == cc_code, member.c.is_group.is_(False)
        )
    ).first()
    if cc is None:
        raise NotFoundError(f"cost center {cc_code} not found")
    entity_id = conn.execute(
        select(member.c.member_id).where(member.c.dim_id == ENTITY, member.c.code == cc.entity_code)
    ).scalar_one()
    actuals_id = conn.execute(
        select(version.c.version_id).where(version.c.code == ACTUALS_CODE)
    ).scalar()
    start = conn.execute(select(installation.c.fiscal_year_start_month)).scalar_one()
    ctx = _Context(
        version_id=v.version_id,
        actuals_id=actuals_id if actuals_id is not None else -1,
        fiscal_year=v.fiscal_year,
        cost_center_id=cc.member_id,
        entity_id=entity_id,
        periods=fiscal_year_periods(v.fiscal_year, start),
        prior_periods=fiscal_year_periods(v.fiscal_year - 1, start),
    )
    return ctx, {"version_name": v.name, "cost_center_name": cc.name}


def _values(conn: Connection, version_id: int, cc_id: int, periods: list[date]):  # type: ignore[no-untyped-def]
    f = fact_cost_center
    return conn.execute(
        select(f.c.dim_account, f.c.period, f.c.amount, f.c.row_version).where(
            f.c.version_id == version_id,
            f.c.layer_id == BASE_LAYER,
            f.c.measure_id == AMOUNT,
            f.c.dim_cost_center == cc_id,
            f.c.period.in_(periods),
        )
    ).all()


def read_grid(engine: Engine, version_code: str, cc_code: str) -> Grid:
    with engine.connect() as conn:
        ctx, names = _context(conn, version_code, cc_code)
        prior: dict[int, list[Decimal | None]] = defaultdict(lambda: [None] * MONTHS)
        plan: dict[int, list[Cell]] = defaultdict(lambda: [Cell(None, None) for _ in range(MONTHS)])
        for acc, period, amount, _ in _values(
            conn, ctx.actuals_id, ctx.cost_center_id, ctx.prior_periods
        ):
            prior[acc][ctx.prior_periods.index(period)] = amount
        for acc, period, amount, rv in _values(
            conn, ctx.version_id, ctx.cost_center_id, ctx.periods
        ):
            plan[acc][ctx.periods.index(period)] = Cell(amount, rv)
        account_ids = set(prior) | set(plan)

        acc, grp = member.alias("acc"), member.alias("grp")
        edge, gedge = hier_edge.alias("edge"), hier_edge.alias("gedge")
        meta = conn.execute(
            select(
                acc.c.member_id, acc.c.code, acc.c.name, grp.c.code, grp.c.name, gedge.c.sort_order
            )
            .join(edge, and_(edge.c.member_id == acc.c.member_id, edge.c.hier_id == H_ACCOUNT))
            .join(grp, grp.c.member_id == edge.c.parent_id)
            .join(gedge, and_(gedge.c.member_id == grp.c.member_id, gedge.c.hier_id == H_ACCOUNT))
            .where(acc.c.member_id.in_(account_ids))
            .order_by(gedge.c.sort_order, grp.c.code, acc.c.code)
        ).all()

    grid = Grid(
        version_code,
        names["version_name"],
        ctx.fiscal_year,
        cc_code,
        names["cost_center_name"],
        ctx.periods,
        ctx.prior_periods,
    )
    current_group = None
    group_row: Row | None = None
    for acc_id, code, name, gcode, gname, _ in meta:
        if gcode != current_group:
            current_group = gcode
            group_row = Row(
                "group",
                gcode,
                gname,
                gcode,
                [None] * MONTHS,
                [Cell(None, None) for _ in range(MONTHS)],
            )
            grid.rows.append(group_row)
        row = Row("account", code, name, gcode, prior[acc_id], plan[acc_id])
        grid.rows.append(row)
        assert group_row is not None
        for m in range(MONTHS):
            group_row.prior_year[m] = _add(group_row.prior_year[m], row.prior_year[m])
            group_row.plan[m] = Cell(_add(group_row.plan[m].value, row.plan[m].value), None)
    return grid


def _add(a: Decimal | None, b: Decimal | None) -> Decimal | None:
    if a is None:
        return b
    return a if b is None else a + b


def _parse(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        amount = Decimal(value)
    except InvalidOperation:
        raise EditError(f"not a number: {value!r}") from None
    if not amount.is_finite():
        raise EditError(f"not a number: {value!r}")
    exponent = amount.as_tuple().exponent
    if isinstance(exponent, int) and exponent < -2:
        raise EditError(f"{value} has more than 2 decimal places")
    return amount


def apply_edits(
    engine: Engine, actor: str, version_code: str, cc_code: str, edits: list[Edit]
) -> None:
    """Apply all edits in one transaction, or none: any conflict or invalid edit rolls back."""
    with unit_of_work(engine, actor) as conn:
        ctx, _ = _context(conn, version_code, cc_code)
        codes = {e.account_code for e in edits}
        accounts = dict(
            conn.execute(
                select(member.c.code, member.c.member_id).where(
                    member.c.dim_id == ACCOUNT,
                    member.c.is_group.is_(False),
                    member.c.code.in_(codes),
                )
            ).all()
        )
        missing = codes - set(accounts)
        if missing:
            raise EditError(f"unknown account(s): {', '.join(sorted(missing))}")

        f = fact_cost_center
        # Lock the cells this request may touch, then compare versions (D-19).
        locked = conn.execute(
            select(f.c.dim_account, f.c.period, f.c.amount, f.c.row_version)
            .where(
                f.c.version_id == ctx.version_id,
                f.c.layer_id == BASE_LAYER,
                f.c.measure_id == AMOUNT,
                f.c.dim_cost_center == ctx.cost_center_id,
                f.c.dim_account.in_(accounts.values()),
                f.c.period.in_(ctx.periods),
            )
            .with_for_update()
        ).all()
        current = {(a, p): rv for a, p, _, rv in locked}
        prior = {
            (a, p): amt
            for a, p, amt, _ in _values(conn, ctx.actuals_id, ctx.cost_center_id, ctx.prior_periods)
        }

        conflicts: list[Conflict] = []
        writes: dict[tuple[int, date], Decimal | None] = {}
        for e in edits:
            if len(e.expected) != MONTHS:
                raise EditError("expected must list 12 row versions")
            acc_id = accounts[e.account_code]
            months = range(MONTHS) if e.month is None else [e.month]
            if e.month is not None and not 0 <= e.month < MONTHS:
                raise EditError(f"month {e.month} out of range")
            for m in months:
                key = (acc_id, ctx.periods[m])
                if key not in writes and current.get(key) != e.expected[m]:
                    conflicts.append(Conflict(e.account_code, m))
            value = _parse(e.value)
            if e.month is not None:
                writes[(acc_id, ctx.periods[e.month])] = value
            elif value is None:
                for p in ctx.periods:
                    writes[(acc_id, p)] = None
            else:
                reference = [prior.get((acc_id, p)) for p in ctx.prior_periods]
                weights = from_reference(reference, negative="zero", if_zero="equal")
                for p, amount in zip(ctx.periods, spread(value, weights), strict=True):
                    writes[(acc_id, p)] = amount
        if conflicts:
            raise ConflictError(conflicts)

        deletes = [k for k, v in writes.items() if v is None and k in current]
        if deletes:
            conn.execute(
                delete(f).where(
                    f.c.version_id == ctx.version_id,
                    f.c.layer_id == BASE_LAYER,
                    f.c.measure_id == AMOUNT,
                    f.c.dim_cost_center == ctx.cost_center_id,
                    tuple_(f.c.dim_account, f.c.period).in_(deletes),
                )
            )
        upserts = [
            {
                "version_id": ctx.version_id,
                "layer_id": BASE_LAYER,
                "measure_id": AMOUNT,
                "period": p,
                "dim_entity": ctx.entity_id,
                "dim_cost_center": ctx.cost_center_id,
                "dim_account": a,
                "amount": v,
            }
            for (a, p), v in writes.items()
            if v is not None
        ]
        if upserts:
            stmt = pg_insert(f).values(upserts)
            conn.execute(
                stmt.on_conflict_do_update(
                    index_elements=[
                        f.c.version_id,
                        f.c.layer_id,
                        f.c.measure_id,
                        f.c.period,
                        f.c.dim_entity,
                        f.c.dim_cost_center,
                        f.c.dim_account,
                    ],
                    set_={"amount": stmt.excluded.amount, "row_version": f.c.row_version + 1},
                )
            )


def plan_versions(engine: Engine) -> list[dict[str, object]]:
    with engine.connect() as conn:
        rows = conn.execute(
            select(version.c.code, version.c.name, version.c.kind, version.c.fiscal_year)
            .where(version.c.kind != "actuals")
            .order_by(version.c.fiscal_year.desc())
        ).all()
    return [dict(r._mapping) for r in rows]


def planning_units(engine: Engine, version_code: str) -> list[dict[str, object]]:
    """Cost centers valid during the version's fiscal year (T-25 switcher)."""
    with engine.connect() as conn:
        v = conn.execute(select(version).where(version.c.code == version_code)).first()
        if v is None or v.fiscal_year is None:
            raise NotFoundError(f"plan version {version_code} not found")
        start = conn.execute(select(installation.c.fiscal_year_start_month)).scalar_one()
        periods = fiscal_year_periods(v.fiscal_year, start)
        rows = conn.execute(
            select(member.c.code, member.c.name, member.c.entity_code, member.c.owner)
            .where(
                member.c.dim_id == COST_CENTER,
                member.c.is_group.is_(False),
                (member.c.valid_from.is_(None)) | (member.c.valid_from <= periods[-1]),
                (member.c.valid_to.is_(None)) | (member.c.valid_to >= periods[0]),
            )
            .order_by(member.c.code)
        ).all()
    return [dict(r._mapping) for r in rows]
