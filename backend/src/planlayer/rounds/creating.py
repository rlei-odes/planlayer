"""A new round, set up like an earlier one, and what it carries over (ADR 0014)."""

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from sqlalchemy import (
    Connection,
    Engine,
    Table,
    func,
    insert,
    literal,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert

from planlayer.access import User
from planlayer.rounds.core import (
    Grain,
    RoundError,
    RoundRef,
    _check_periods,
    account_scopes,
    fiscal_periods,
    get_round,
    valid_cost_centers,
)
from planlayer.store.bulk import insert_all
from planlayer.store.schema import (
    AMOUNT,
    BASE_LAYER,
    MODULE_CAPACITY,
    MODULE_COST_CENTER,
    account_set,
    account_set_account,
    account_set_assignment,
    capacity_line,
    comparison,
    credit_account,
    entry_form,
    fact_cost_center,
    forwarding_account,
    line_spread,
    not_planned_account,
    planning_round,
    round_member,
    round_module,
    sheet_account,
    version,
)
from planlayer.store.uow import unit_of_work


@dataclass
class CarryOver:
    """What a new round takes from the round it is set up like (ADR 0014)."""

    setup: bool = True  # default form, entry forms, comparison columns
    compare_with_source: bool = True  # the source round as a comparison column
    scope: bool = True  # cost centers not planned
    accounts: bool = True  # accounts not planned, account sets and their assignments
    capacity: bool = True  # productive cost centers and their capacity lines
    sheets: bool = True  # accounts added to sheets, line spreads
    forwarding: bool = True  # cost forwardings (ADR 0018), recomputed in the new round
    workforce: bool = True  # lines added to workforce sheets and positions (ADR 0020)
    values: bool = False  # plan values, moved to the new year and raised by ``uplift``
    uplift: Decimal = Decimal(0)  # percent

    def record(self, cells: int | None, hours: int | None = None) -> dict[str, object]:
        """What the round carried, as kept on it: the items, the plan values and the planned
        hours copied (ADR 0016)."""
        items = {k: getattr(self, k) for k in CARRY_ITEMS}
        return {**items, "uplift": str(self.uplift), "cells": cells, "hours": hours}


CARRY_ITEMS = (
    "setup",
    "compare_with_source",
    "scope",
    "accounts",
    "capacity",
    "sheets",
    "forwarding",
    "workforce",
    "values",
)


CENT = Decimal("0.01")


def create_round(
    engine: Engine,
    actor: User,
    code: str,
    name: str,
    kind: Literal["budget", "forecast"],
    fiscal_year: int,
    deadline: date | None = None,
    copy_from: str | None = None,
    carry: CarryOver | None = None,
    grain: Grain = "month",
    actuals_through: date | None = None,
) -> int:
    """A plan version and its round, in status ``setup``.

    Without ``copy_from`` it starts from the module defaults: every account ``monthly``,
    prior-year actuals as the only comparison, every cost center valid in the fiscal year
    planned. With it, ``carry`` says what the new round takes from that round; cost centers
    valid in the fiscal year that the source round didn't know start planned. Everything is
    written in one transaction, plan values as one change batch. A forecast closes the months
    up to ``actuals_through``; carried plan values go into its open months only (ADR 0015).
    """
    actor.require_controller()
    carry = carry or CarryOver()
    with unit_of_work(engine, actor) as conn:
        if conn.execute(select(version.c.version_id).where(version.c.code == code)).first():
            raise RoundError(f"a round or version {code} exists")
        _check_periods(conn, kind, fiscal_year, grain, actuals_through)
        source = get_round(conn, copy_from) if copy_from is not None else None
        vid = int(
            conn.execute(
                insert(version)
                .values(code=code, name=name, kind=kind)
                .returning(version.c.version_id)
            ).scalar_one()
        )
        conn.execute(
            insert(planning_round).values(
                version_id=vid,
                fiscal_year=fiscal_year,
                deadline=deadline,
                copied_from=source.version_id if source else None,
                created_by=actor.login,
                created_at=func.now(),
                grain=grain,
                actuals_through=actuals_through,
            )
        )
        if source is not None and carry.setup:
            _copy_rows(conn, source.version_id, vid, round_module, entry_form, comparison)
        else:
            conn.execute(insert(round_module).values(version_id=vid, module=MODULE_COST_CENTER))
            conn.execute(
                insert(comparison).values(
                    version_id=vid,
                    module=MODULE_COST_CENTER,
                    position=1,
                    kind="prior_year_actuals",
                )
            )
        if source is not None and carry.compare_with_source:
            _compare_with(conn, vid, source.version_id)
        members = valid_cost_centers(conn, fiscal_year)
        disabled = (
            _round_members(conn, source.version_id, MODULE_COST_CENTER, enabled=False)
            if source is not None and carry.scope
            else set()
        )
        if members:
            conn.execute(
                insert(round_member),
                [
                    {
                        "version_id": vid,
                        "module": MODULE_COST_CENTER,
                        "member_id": m,
                        "enabled": m not in disabled,
                    }
                    for m in members
                ],
            )
        if source is None:
            return vid
        if carry.accounts:
            _copy_rows(conn, source.version_id, vid, not_planned_account)
            _copy_account_sets(conn, source.version_id, vid)
        planned = set(members) - disabled
        if carry.capacity:
            _copy_capacity(conn, source.version_id, vid, set(members))
        if carry.sheets:
            for table in (sheet_account, line_spread):
                columns = [c for c in table.c if c.name != "version_id"]
                conn.execute(
                    insert(table).from_select(
                        ["version_id", *(c.name for c in columns)],
                        select(literal(vid, table.c.version_id.type), *columns).where(
                            table.c.version_id == source.version_id,
                            table.c.cost_center_id.in_(planned),
                        ),
                    )
                )
        cells = (
            _copy_values(conn, source, vid, fiscal_year, planned, carry.uplift, actuals_through)
            if carry.values
            else None
        )
        hours = None
        if carry.values and carry.capacity:
            # Planned hours go with the plan values; credits are recomputed from them. Imported
            # here: capacity builds on this module.
            from planlayer.capacity import carry_hours, productive

            target = get_round(conn, code)
            hours = carry_hours(conn, source, target, productive(conn, vid) & planned)
        if carry.workforce:
            # Imported here: workforce planning builds on this module.
            from planlayer.workforce import carry as carry_workforce

            carry_workforce(conn, source, get_round(conn, code), planned, carry.values)
        if carry.forwarding:
            # After the values: full forwardings pass on the carried costs. Imported here:
            # forwarding builds on this module.
            from planlayer.forwarding import carry as carry_forwarding

            carry_forwarding(conn, source, get_round(conn, code), planned)
        conn.execute(
            update(planning_round)
            .where(planning_round.c.version_id == vid)
            .values(carried=carry.record(cells, hours))
        )
        return vid


def _copy_rows(conn: Connection, source_id: int, target_id: int, *tables: Table) -> None:
    for table in tables:
        columns = [c for c in table.c if c.name != "version_id"]
        conn.execute(
            insert(table).from_select(
                ["version_id", *(c.name for c in columns)],
                select(literal(target_id, table.c.version_id.type), *columns).where(
                    table.c.version_id == source_id
                ),
            )
        )


def _round_members(conn: Connection, version_id: int, module: str, *, enabled: bool) -> set[int]:
    return set(
        conn.execute(
            select(round_member.c.member_id).where(
                round_member.c.version_id == version_id,
                round_member.c.module == module,
                round_member.c.enabled.is_(enabled),
            )
        ).scalars()
    )


def _compare_with(conn: Connection, version_id: int, other_id: int) -> None:
    """Add a version comparison column at the end, unless the round has it already."""
    c = comparison
    rows = conn.execute(
        select(c.c.position, c.c.compare_version_id).where(
            c.c.version_id == version_id, c.c.module == MODULE_COST_CENTER
        )
    ).all()
    if any(other == other_id for _, other in rows):
        return
    conn.execute(
        insert(c).values(
            version_id=version_id,
            module=MODULE_COST_CENTER,
            position=max((p for p, _ in rows), default=0) + 1,
            kind="version",
            compare_version_id=other_id,
        )
    )


def _copy_account_sets(conn: Connection, source_id: int, target_id: int) -> None:
    ids = {}
    for set_id, module, set_name in conn.execute(
        select(account_set.c.set_id, account_set.c.module, account_set.c.name).where(
            account_set.c.version_id == source_id
        )
    ):
        ids[set_id] = conn.execute(
            insert(account_set)
            .values(version_id=target_id, module=module, name=set_name)
            .returning(account_set.c.set_id)
        ).scalar_one()
    for old, new in ids.items():
        conn.execute(
            insert(account_set_account).from_select(
                ["set_id", "account_id"],
                select(literal(new), account_set_account.c.account_id).where(
                    account_set_account.c.set_id == old
                ),
            )
        )
    a = account_set_assignment
    rows = conn.execute(
        select(a.c.module, a.c.member_id, a.c.set_id).where(a.c.version_id == source_id)
    ).all()
    if rows:
        conn.execute(
            insert(a),
            [
                {"version_id": target_id, "module": m, "member_id": mid, "set_id": ids[sid]}
                for m, mid, sid in rows
            ],
        )


def _copy_capacity(conn: Connection, source_id: int, target_id: int, members: set[int]) -> None:
    """Productive cost centers valid in the new year, and their capacity lines."""
    productive = _round_members(conn, source_id, MODULE_CAPACITY, enabled=True) & members
    if not productive:
        return
    conn.execute(
        pg_insert(round_module)
        .values(version_id=target_id, module=MODULE_CAPACITY)
        .on_conflict_do_nothing()
    )
    conn.execute(
        insert(round_member),
        [
            {"version_id": target_id, "module": MODULE_CAPACITY, "member_id": m, "enabled": True}
            for m in sorted(productive)
        ],
    )
    columns = [
        c for c in capacity_line.c if c.name not in ("version_id", "changed_by", "changed_at")
    ]
    conn.execute(
        insert(capacity_line).from_select(
            ["version_id", *(c.name for c in columns)],
            select(literal(target_id, capacity_line.c.version_id.type), *columns).where(
                capacity_line.c.version_id == source_id,
                capacity_line.c.cost_center_id.in_(productive),
            ),
        )
    )


def _copy_values(
    conn: Connection,
    source: RoundRef,
    target_id: int,
    fiscal_year: int,
    planned: set[int],
    uplift: Decimal,
    actuals_through: date | None = None,
) -> int:
    """The source round's plan values in the new year, month by month, times 1 + uplift %.
    Only cells the new round plans: a planned cost center, an account planned in the round,
    inside the cost center's account set and not credited by capacity planning, in a month the
    new round hasn't closed. Returns the number of cells written."""
    if not planned:
        return 0
    old_periods = fiscal_periods(conn, source.fiscal_year)
    new_periods = fiscal_periods(conn, fiscal_year)
    moved = {
        old: new
        for old, new in zip(old_periods, new_periods, strict=True)
        if actuals_through is None or new > actuals_through
    }
    old_periods = list(moved)
    excluded = set(
        conn.execute(
            select(not_planned_account.c.account_id).where(
                not_planned_account.c.version_id == target_id,
                not_planned_account.c.module == MODULE_COST_CENTER,
            )
        ).scalars()
    ) | set(conn.execute(select(credit_account.c.account_id)).scalars())
    # Forwarding values are computed in the new round, not carried (ADR 0018).
    excluded |= set(
        conn.execute(
            select(forwarding_account.c.account_id).where(
                forwarding_account.c.version_id == source.version_id
            )
        ).scalars()
    )
    scopes = account_scopes(conn, target_id, planned)
    factor = 1 + uplift / 100
    f = fact_cost_center
    rows = []
    for period, entity_id, cc_id, account_id, amount in conn.execute(
        select(f.c.period, f.c.dim_entity, f.c.dim_cost_center, f.c.dim_account, f.c.amount).where(
            f.c.version_id == source.version_id,
            f.c.layer_id == BASE_LAYER,
            f.c.measure_id == AMOUNT,
            f.c.period.in_(old_periods),
            f.c.dim_cost_center.in_(planned),
        )
    ):
        scope = scopes.get(cc_id)
        if account_id in excluded or (scope is not None and account_id not in scope):
            continue
        rows.append(
            {
                "version_id": target_id,
                "layer_id": BASE_LAYER,
                "measure_id": AMOUNT,
                "period": moved[period],
                "dim_entity": entity_id,
                "dim_cost_center": cc_id,
                "dim_account": account_id,
                "amount": (amount * factor).quantize(CENT, ROUND_HALF_UP),
            }
        )
    insert_all(conn, f, rows)  # one statement: the lock and audit triggers run once
    return len(rows)
