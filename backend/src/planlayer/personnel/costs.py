"""The totals personnel costs write to the cost centers (ADR 0022): every position's costs per
account and month, summed on the cost center charged, rounded once, and stored where they
differ; the round's small groups; and the withdrawal of HR's review where totals moved.

A recompute covers the whole round: charging a position to another cost center moves costs
between cost centers, and one advisory lock per round keeps recomputes from crossing. It reads
pay with the system user's rights; what it writes are totals."""

from collections import defaultdict
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import Connection, and_, delete, func, insert, select, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert

from planlayer.domain.periods import closed_months
from planlayer.domain.personnel import PayPosition, costs, group_counts
from planlayer.personnel.common import load_rules, module_on, pay_accounts
from planlayer.rounds import RoundRef, fiscal_periods
from planlayer.store.schema import (
    AMOUNT,
    BASE_LAYER,
    COST_CENTER,
    ENTITY,
    NO_ENTITY,
    fact_cost_center,
    member,
    pay_review,
    pay_small_group,
    position_pay,
    workforce_position,
)
from planlayer.store.uow import system_rights
from planlayer.workflow import enabled_members

CENT = Decimal("0.01")
RECOMPUTE_LOCK = 2022  # advisory lock class of personnel cost recomputes (ADR 0022)

Key = tuple[int, int, date]  # cost center charged, account, period


def entities_of(conn: Connection) -> dict[int, int]:
    """Each cost center's entity member."""
    entity = member.alias("entity")
    return dict(
        conn.execute(
            select(member.c.member_id, entity.c.member_id)
            .join(
                entity,
                and_(
                    entity.c.dim_id == ENTITY,
                    entity.c.code == func.coalesce(member.c.entity_code, NO_ENTITY),
                ),
            )
            .where(member.c.dim_id == COST_CENTER, member.c.is_group.is_(False))
        ).all()
    )


def _wanted(
    conn: Connection, r: RoundRef, periods: list[date]
) -> tuple[dict[Key, Decimal], dict[tuple[int, int], int]]:
    """Totals per cost center charged, account and month, and positions per cost center and
    account (group sizes)."""
    rules = load_rules(conn, r.version_id)
    enabled = {m for m, on in enabled_members(conn, r.version_id).items() if on}
    entities = entities_of(conn)
    p, pay = workforce_position, position_pay
    totals: dict[Key, Decimal] = defaultdict(Decimal)
    per_cc: dict[int, list[dict[int, list[Decimal]]]] = defaultdict(list)
    for row in conn.execute(
        select(
            p.c.cost_center_id,
            p.c.staff_type_id,
            p.c.entry_date,
            p.c.leave_date,
            pay.c.hr_salary,
            pay.c.salary,
            pay.c.bonus,
            pay.c.charged_to_id,
            pay.c.excluded_rules,
        )
        .outerjoin(pay, pay.c.position_id == p.c.position_id)
        .where(p.c.version_id == r.version_id)
    ):
        kind = rules.staff_types.get(row.staff_type_id)
        charged = row.charged_to_id or row.cost_center_id
        if kind is None or charged not in enabled:
            continue
        salary = row.salary if row.salary is not None else (row.hr_salary or Decimal(0))
        position = PayPosition(
            entities[row.cost_center_id],
            kind.account_id,
            kind.typed,
            salary,
            row.entry_date,
            row.leave_date,
            row.bonus,
            frozenset(row.excluded_rules or ()),
        )
        c = costs(position, rules.rules, periods)
        per_cc[charged].append(c)
        for account, months in c.items():
            for period, v in zip(periods, months, strict=True):
                if v:
                    totals[(charged, account, period)] += v
    sizes = {
        (cc, account): n
        for cc, by_position in per_cc.items()
        for account, n in group_counts(by_position).items()
    }
    return {k: v.quantize(CENT, ROUND_HALF_UP) for k, v in totals.items()}, sizes


def recompute(
    conn: Connection, r: RoundRef, actor: str, *, by_hr: bool, touched: set[int] | None = None
) -> set[int]:
    """Recompute the round's personnel costs and small groups. HR's review is withdrawn on the
    cost centers whose totals changed, and on ``touched``, unless HR made the change. Returns
    the cost centers whose totals changed."""
    if not module_on(conn, r.version_id):
        return set()
    conn.execute(select(func.pg_advisory_xact_lock(RECOMPUTE_LOCK, r.version_id)))
    periods = fiscal_periods(conn, r.fiscal_year)
    open_periods = periods[closed_months(periods, r.actuals_through) :]
    changed: set[int] = set()
    with system_rights(conn, actor=actor):
        wanted, sizes = _wanted(conn, r, periods)
        wanted = {k: v for k, v in wanted.items() if k[2] in open_periods and v}
        accounts = pay_accounts(conn, r.version_id)
        enabled = [m for m, on in enabled_members(conn, r.version_id).items() if on]
        f = fact_cost_center
        scope = and_(
            f.c.version_id == r.version_id,
            f.c.layer_id == BASE_LAYER,
            f.c.measure_id == AMOUNT,
            f.c.dim_account.in_(accounts),
            f.c.period.in_(open_periods),  # a forecast's closed months hold actuals
            # Values on cost centers the round doesn't plan are kept, read only (ADR 0014),
            # e.g. carried from a round without personnel costs.
            f.c.dim_cost_center.in_(enabled),
        )
        stored = {
            (cc, a, p): v
            for cc, a, p, v in conn.execute(
                select(f.c.dim_cost_center, f.c.dim_account, f.c.period, f.c.amount)
                .where(scope)
                .with_for_update()
            )
        }
        gone = [k for k in stored if k not in wanted]
        if gone:
            conn.execute(
                delete(f).where(
                    scope, tuple_(f.c.dim_cost_center, f.c.dim_account, f.c.period).in_(gone)
                )
            )
        entities = entities_of(conn)
        upserts = [
            {
                "version_id": r.version_id,
                "layer_id": BASE_LAYER,
                "measure_id": AMOUNT,
                "period": p,
                "dim_entity": entities[cc],
                "dim_cost_center": cc,
                "dim_account": a,
                "amount": v,
            }
            for (cc, a, p), v in wanted.items()
            if stored.get((cc, a, p)) != v
        ]
        if upserts:
            stmt = pg_insert(f).values(upserts)
            conn.execute(
                stmt.on_conflict_do_update(
                    index_elements=list(f.primary_key.columns),
                    set_={"amount": stmt.excluded.amount, "row_version": f.c.row_version + 1},
                )
            )
        changed = {k[0] for k in gone} | {
            cc for (cc, a, p), v in wanted.items() if stored.get((cc, a, p)) != v
        }

        minimum = load_rules(conn, r.version_id).min_group
        conn.execute(delete(pay_small_group).where(pay_small_group.c.version_id == r.version_id))
        small = [
            {"version_id": r.version_id, "cost_center_id": cc, "account_id": a, "positions": n}
            for (cc, a), n in sizes.items()
            if a in accounts and 0 < n < minimum
        ]
        if small:
            conn.execute(insert(pay_small_group), small)

        withdrawn = changed | (touched or set())
        if withdrawn and not by_hr:
            conn.execute(
                delete(pay_review).where(
                    pay_review.c.version_id == r.version_id,
                    pay_review.c.cost_center_id.in_(withdrawn),
                )
            )
    if changed:
        # Imported here: forwarding builds on planning, which this module builds on.
        from planlayer.forwarding import follow

        for cc in sorted(changed):
            follow(conn, r, cc)
    return changed


def clear(conn: Connection, r: RoundRef, accounts: set[int]) -> None:
    """Remove the round's values on personnel accounts about to stop being ones, while they
    still are: then the lock lets them go whatever the cost centers' status."""
    if not accounts:
        return
    f = fact_cost_center
    with system_rights(conn):
        conn.execute(delete(f).where(f.c.version_id == r.version_id, f.c.dim_account.in_(accounts)))


def small_groups(conn: Connection, version_id: int) -> dict[int, list[tuple[int, int]]]:
    """The round's small groups per cost center: (account, positions)."""
    out: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for row in conn.execute(
        select(pay_small_group).where(pay_small_group.c.version_id == version_id)
    ):
        out[row.cost_center_id].append((row.account_id, row.positions))
    return dict(out)
