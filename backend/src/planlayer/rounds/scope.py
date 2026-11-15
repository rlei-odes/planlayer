"""The round's scope on the setup page (ADR 0014): cost centers and accounts with their totals."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import (
    Connection,
    Engine,
    func,
    select,
)

from planlayer.access import User
from planlayer.rounds.core import fiscal_periods, get_round, valid_cost_centers
from planlayer.rounds.creating import _round_members
from planlayer.store.schema import (
    ACCOUNT,
    AMOUNT,
    BASE_LAYER,
    COST_CENTER,
    H_ACCOUNT,
    H_COST_CENTER,
    MODULE_COST_CENTER,
    closure,
    fact_cost_center,
    member,
    not_planned_account,
    planning_round,
    round_member,
    version,
)
from planlayer.store.uow import unit_of_work


@dataclass
class ScopeRow:
    code: str
    name: str
    depth: int
    is_group: bool
    parent: str | None
    planned: bool | None  # leaves: planned in the round; None for groups
    new: bool  # a leaf the source round didn't have
    missing: bool  # no longer delivered by the source
    prior_actuals: Decimal | None  # prior-year actuals, summed over the year
    source_plan: Decimal | None  # in the round this one was set up from


@dataclass
class Scope:
    source_round: str | None
    cost_centers: list[ScopeRow]
    accounts: list[ScopeRow]


def scope_of(engine: Engine, user: User, code: str) -> Scope:
    """The cost center and account trees as the round's scope: what is planned, what is new or
    no longer delivered, and last year's figures beside them."""
    from planlayer.masterdata import tree  # masterdata depends on the sync, not on rounds

    user.require_controller()
    cost_centers = tree(engine, user, "cost_centers")
    accounts = tree(engine, user, "accounts")
    with unit_of_work(engine, user) as conn:
        r = get_round(conn, code)
        source = conn.execute(
            select(version.c.version_id, version.c.code)
            .join(planning_round, planning_round.c.copied_from == version.c.version_id)
            .where(planning_round.c.version_id == r.version_id)
        ).first()
        members = dict(
            conn.execute(
                select(member.c.code, member.c.member_id).where(
                    member.c.dim_id.in_((COST_CENTER, ACCOUNT))
                )
            ).all()
        )
        state = dict(
            conn.execute(
                select(round_member.c.member_id, round_member.c.enabled).where(
                    round_member.c.version_id == r.version_id,
                    round_member.c.module == MODULE_COST_CENTER,
                )
            ).all()
        )
        valid = set(valid_cost_centers(conn, r.fiscal_year))
        known_before = (
            _round_members(conn, source.version_id, MODULE_COST_CENTER, enabled=True)
            | _round_members(conn, source.version_id, MODULE_COST_CENTER, enabled=False)
            if source is not None
            else None
        )
        excluded = set(
            conn.execute(
                select(not_planned_account.c.account_id).where(
                    not_planned_account.c.version_id == r.version_id,
                    not_planned_account.c.module == MODULE_COST_CENTER,
                )
            ).scalars()
        )
        actuals_id = conn.execute(
            select(version.c.version_id).where(version.c.kind == "actuals")
        ).scalar()
        prior = _totals(conn, actuals_id, fiscal_periods(conn, r.fiscal_year - 1))
        before = _totals(conn, source.version_id if source else None, None)

    shown = {
        row.code
        for row in cost_centers
        if not row.is_group and (members[row.code] in valid or members[row.code] in state)
    }
    parents = {row.code: row.parent for row in cost_centers}
    for leaf in list(shown):
        node = parents.get(leaf)
        while node is not None and node not in shown:
            shown.add(node)
            node = parents.get(node)

    def leaf_new(m: int) -> bool:
        return known_before is not None and m not in known_before

    cc_rows = [
        ScopeRow(
            row.code,
            row.name,
            row.depth,
            row.is_group,
            row.parent,
            None if row.is_group else bool(state.get(members[row.code])),
            not row.is_group and leaf_new(members[row.code]),
            row.missing_since is not None,
            prior[0].get(members[row.code]),
            before[0].get(members[row.code]),
        )
        for row in cost_centers
        if row.code in shown
    ]
    account_rows = [
        ScopeRow(
            row.code,
            row.name,
            row.depth,
            row.is_group,
            row.parent,
            None if row.is_group else members[row.code] not in excluded,
            False,
            row.missing_since is not None,
            prior[1].get(members[row.code]),
            before[1].get(members[row.code]),
        )
        for row in accounts
    ]
    return Scope(source.code if source else None, cc_rows, account_rows)


def _totals(
    conn: Connection, version_id: int | None, periods: list[date] | None
) -> tuple[dict[int, Decimal], dict[int, Decimal]]:
    """A version's values summed per cost center and per account, rolled up through the
    hierarchies so groups carry their totals too."""
    if version_id is None:
        return {}, {}
    f = fact_cost_center
    out: list[dict[int, Decimal]] = []
    for column, hier_id in ((f.c.dim_cost_center, H_COST_CENTER), (f.c.dim_account, H_ACCOUNT)):
        query = (
            select(closure.c.ancestor_id, func.sum(f.c.amount))
            .join(closure, (closure.c.descendant_id == column) & (closure.c.hier_id == hier_id))
            .where(f.c.version_id == version_id, f.c.layer_id == BASE_LAYER)
            .where(f.c.measure_id == AMOUNT)
            .group_by(closure.c.ancestor_id)
        )
        if periods is not None:
            query = query.where(f.c.period.in_(periods))
        out.append(dict(conn.execute(query).all()))
    return out[0], out[1]
