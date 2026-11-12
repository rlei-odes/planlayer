"""Planning rounds and their setup (ADR 0007, ADR 0014): modules, planned cost centers, entry
forms, comparisons, accounts not planned, account sets, seasonality profiles; round status
(ADR 0008); new rounds set up like an earlier one, with what they carry over.

The yearly setup page (E-03) edits a round through these functions.
"""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from sqlalchemy import (
    Connection,
    Engine,
    Table,
    delete,
    func,
    insert,
    literal,
    select,
    tuple_,
    union,
    update,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert

from planlayer.access import User
from planlayer.domain.entry_forms import FORMS, Form
from planlayer.domain.periods import (
    calendar_days,
    closed_months,
    fiscal_year_periods,
    working_days,
)
from planlayer.domain.spreading import Profile
from planlayer.domain.workflow import RoundStatus, round_transition
from planlayer.store.bulk import insert_all
from planlayer.store.schema import (
    ACCOUNT,
    AMOUNT,
    BASE_LAYER,
    COST_CENTER,
    H_ACCOUNT,
    H_COST_CENTER,
    MODULE_CAPACITY,
    MODULE_COST_CENTER,
    account_set,
    account_set_account,
    account_set_assignment,
    capacity_line,
    closure,
    comparison,
    credit_account,
    dataset_mapping,
    entry_form,
    fact_capacity,
    fact_cost_center,
    installation,
    line_spread,
    member,
    not_planned_account,
    planning_round,
    round_member,
    round_module,
    seasonality_profile,
    sheet_account,
    version,
)
from planlayer.store.uow import unit_of_work

ComparisonKind = Literal["prior_year_actuals", "actuals", "version"]
Grain = Literal["month", "quarter"]  # how a round is planned (ADR 0015)
GRAINS: tuple[Grain, ...] = ("month", "quarter")


@dataclass
class Comparison:
    """A comparison column: prior-year actuals, actuals of the year, or another version; with
    the controller's note to planners, e.g. "January to June only"."""

    kind: ComparisonKind
    version_code: str | None = None  # kind ``version`` only
    note: str | None = None


class NotFoundError(LookupError):
    pass


class RoundError(ValueError):
    """A setup change that can't be made as asked; nothing was changed."""


@dataclass
class RoundRef:
    version_id: int
    code: str
    name: str
    kind: str
    fiscal_year: int
    status: RoundStatus
    deadline: date | None
    grain: Grain = "month"
    # A forecast's last closed month (ADR 0015); None: no month is closed.
    actuals_through: date | None = None


_ROUNDS = select(
    version.c.version_id,
    version.c.code,
    version.c.name,
    version.c.kind,
    planning_round.c.fiscal_year,
    planning_round.c.status,
    planning_round.c.deadline,
    planning_round.c.grain,
    planning_round.c.actuals_through,
).join(planning_round, planning_round.c.version_id == version.c.version_id)


def get_round(
    conn: Connection, code: str, lock: Literal["share", "update"] | None = None
) -> RoundRef:
    query = _ROUNDS.where(version.c.code == code)
    if lock is not None:
        query = query.with_for_update(read=lock == "share", of=planning_round)
    row = conn.execute(query).first()
    if row is None:
        raise NotFoundError(f"planning round {code} not found")
    return RoundRef(**row._mapping)


def fiscal_periods(conn: Connection, fiscal_year: int) -> list[date]:
    start = conn.execute(select(installation.c.fiscal_year_start_month)).scalar_one()
    return fiscal_year_periods(fiscal_year, start)


def valid_cost_centers(conn: Connection, fiscal_year: int) -> list[int]:
    """Leaf cost centers valid at some point of the fiscal year."""
    periods = fiscal_periods(conn, fiscal_year)
    return list(
        conn.execute(
            select(member.c.member_id).where(
                member.c.dim_id == COST_CENTER,
                member.c.is_group.is_(False),
                member.c.valid_from.is_(None) | (member.c.valid_from <= periods[-1]),
                member.c.valid_to.is_(None) | (member.c.valid_to >= periods[0]),
            )
        ).scalars()
    )


def _member_id(conn: Connection, dim_id: int, code: str, *, leaf: bool | None = None) -> int:
    query = select(member.c.member_id).where(member.c.dim_id == dim_id, member.c.code == code)
    if leaf is not None:
        query = query.where(member.c.is_group.is_(not leaf))
    found = conn.execute(query).scalar()
    if found is None:
        raise NotFoundError(f"{'account' if dim_id == ACCOUNT else 'member'} {code} not found")
    return int(found)


def _version_id(conn: Connection, code: str) -> int:
    found = conn.execute(select(version.c.version_id).where(version.c.code == code)).scalar()
    if found is None:
        raise NotFoundError(f"version {code} not found")
    return int(found)


def closed_periods(conn: Connection, r: RoundRef) -> list[date]:
    """The round's closed months (ADR 0015): a forecast's months up to its actuals through."""
    periods = fiscal_periods(conn, r.fiscal_year)
    return periods[: closed_months(periods, r.actuals_through)]


def _check_periods(
    conn: Connection, kind: str, fiscal_year: int, grain: str, actuals_through: date | None
) -> None:
    """A round's grain and closed months as ADR 0015 allows them."""
    if grain not in GRAINS:
        raise RoundError(f"unknown grain {grain}")
    if actuals_through is None:
        return
    if kind != "forecast":
        raise RoundError("only a forecast closes months")
    periods = fiscal_periods(conn, fiscal_year)
    if actuals_through not in periods[:-1]:
        raise RoundError(
            "actuals through must be a month of the fiscal year before its last, "
            "as the first day of the month"
        )
    if grain == "quarter" and closed_months(periods, actuals_through) % 3:
        raise RoundError(
            "a forecast planned by quarter closes whole quarters; plan it by month to close "
            f"{closed_months(periods, actuals_through)} months"
        )


def start_month(engine: Engine) -> int:
    """The installation's first fiscal month (T-18)."""
    with engine.connect() as conn:
        return int(conn.execute(select(installation.c.fiscal_year_start_month)).scalar_one())


def latest_actuals(engine: Engine, user: User) -> date | None:
    """The latest month holding synced actuals the user may read: what a forecast proposes as
    its actuals through (ADR 0015)."""
    f = fact_cost_center
    with unit_of_work(engine, user) as conn:
        found: date | None = conn.execute(
            select(func.max(f.c.period))
            .join(version, version.c.version_id == f.c.version_id)
            .where(version.c.kind == "actuals", f.c.measure_id == AMOUNT)
        ).scalar()
    return found


@dataclass
class CarryOver:
    """What a new round takes from the round it is set up like (ADR 0014)."""

    setup: bool = True  # default form, entry forms, comparison columns
    compare_with_source: bool = True  # the source round as a comparison column
    scope: bool = True  # cost centers not planned
    accounts: bool = True  # accounts not planned, account sets and their assignments
    capacity: bool = True  # productive cost centers and their capacity lines
    sheets: bool = True  # accounts added to sheets, line spreads
    values: bool = False  # plan values, moved to the new year and raised by ``uplift``
    uplift: Decimal = Decimal(0)  # percent

    def record(self, cells: int | None) -> dict[str, object]:
        """What the round carried, as kept on it: the items, and the plan values copied."""
        items = {k: getattr(self, k) for k in CARRY_ITEMS}
        return {**items, "uplift": str(self.uplift), "cells": cells}


CARRY_ITEMS = ("setup", "compare_with_source", "scope", "accounts", "capacity", "sheets", "values")


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
        conn.execute(
            update(planning_round)
            .where(planning_round.c.version_id == vid)
            .values(carried=carry.record(cells))
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


def account_scopes(
    conn: Connection, version_id: int, cost_centers: Iterable[int]
) -> dict[int, set[int] | None]:
    """Per cost center, the leaf accounts of its account set: the nearest assignment on it or a
    node above it (ADR 0014). None: no set, every account."""
    ids = list(cost_centers)
    if not ids:
        return {}
    a = account_set_assignment
    nearest: dict[int, tuple[int, int]] = {}
    for cc_id, depth, set_id in conn.execute(
        select(closure.c.descendant_id, closure.c.depth, a.c.set_id)
        .join(a, a.c.member_id == closure.c.ancestor_id)
        .where(
            closure.c.hier_id == H_COST_CENTER,
            closure.c.descendant_id.in_(ids),
            a.c.version_id == version_id,
            a.c.module == MODULE_COST_CENTER,
        )
    ):
        if cc_id not in nearest or depth < nearest[cc_id][0]:
            nearest[cc_id] = (depth, set_id)
    leaves: dict[int, set[int]] = defaultdict(set)
    used = {set_id for _, set_id in nearest.values()}
    if used:
        for set_id, account_id in conn.execute(
            select(account_set_account.c.set_id, closure.c.descendant_id)
            .join(closure, closure.c.ancestor_id == account_set_account.c.account_id)
            .join(member, member.c.member_id == closure.c.descendant_id)
            .where(
                closure.c.hier_id == H_ACCOUNT,
                account_set_account.c.set_id.in_(used),
                member.c.is_group.is_(False),
            )
        ):
            leaves[set_id].add(account_id)
    return {cc: (leaves[nearest[cc][1]] if cc in nearest else None) for cc in ids}


def set_round_status(engine: Engine, actor: User, code: str, status: RoundStatus) -> None:
    actor.require_controller()
    with unit_of_work(engine, actor) as conn:
        r = get_round(conn, code, lock="update")
        round_transition(r.status, status)
        conn.execute(
            update(planning_round)
            .where(planning_round.c.version_id == r.version_id)
            .values(status=status)
        )


def set_deadline(engine: Engine, actor: User, code: str, deadline: date | None) -> None:
    actor.require_controller()
    with unit_of_work(engine, actor) as conn:
        r = get_round(conn, code, lock="update")
        conn.execute(
            update(planning_round)
            .where(planning_round.c.version_id == r.version_id)
            .values(deadline=deadline)
        )


def set_member_enabled(engine: Engine, actor: User, code: str, cc_code: str, enabled: bool) -> None:
    actor.require_controller()
    with unit_of_work(engine, actor) as conn:
        r = get_round(conn, code)
        cc_id = _member_id(conn, COST_CENTER, cc_code, leaf=True)
        stmt = pg_insert(round_member).values(
            version_id=r.version_id, module=MODULE_COST_CENTER, member_id=cc_id, enabled=enabled
        )
        conn.execute(
            stmt.on_conflict_do_update(
                index_elements=list(round_member.primary_key.columns),
                set_={"enabled": stmt.excluded.enabled},
            )
        )


def add_profile(engine: Engine, actor: User, code: str, name: str, profile: Profile) -> int:
    actor.require_controller()
    with unit_of_work(engine, actor) as conn:
        taken = conn.execute(
            select(seasonality_profile.c.profile_id).where(seasonality_profile.c.code == code)
        ).first()
        if taken is not None:
            raise RoundError(f"a profile {code} exists")
        return int(
            conn.execute(
                insert(seasonality_profile)
                .values(
                    code=code, name=name, entered_as=profile.entered_as, values=list(profile.values)
                )
                .returning(seasonality_profile.c.profile_id)
            ).scalar_one()
        )


def update_profile(engine: Engine, actor: User, code: str, name: str, profile: Profile) -> None:
    """New name and values; they apply to the next spread. Months already stored stay."""
    actor.require_controller()
    with unit_of_work(engine, actor) as conn:
        changed = conn.execute(
            update(seasonality_profile)
            .where(seasonality_profile.c.code == code)
            .values(name=name, entered_as=profile.entered_as, values=list(profile.values))
        ).rowcount
        if changed == 0:
            raise NotFoundError(f"seasonality profile {code} not found")


def delete_profile(engine: Engine, actor: User, code: str) -> None:
    """Refused while an entry form or a line in any round uses the profile."""
    actor.require_controller()
    with unit_of_work(engine, actor) as conn:
        profile_id = conn.execute(
            select(seasonality_profile.c.profile_id)
            .where(seasonality_profile.c.code == code)
            .with_for_update()
        ).scalar()
        if profile_id is None:
            raise NotFoundError(f"seasonality profile {code} not found")
        users = union(
            select(entry_form.c.version_id).where(entry_form.c.profile_id == profile_id),
            select(line_spread.c.version_id).where(line_spread.c.profile_id == profile_id),
        ).subquery()
        used_by = (
            conn.execute(
                select(version.c.code)
                .where(version.c.version_id.in_(select(users.c.version_id)))
                .order_by(version.c.code)
            )
            .scalars()
            .all()
        )
        if used_by:
            raise RoundError(f"profile {code} is used by round {', '.join(used_by)}")
        conn.execute(
            delete(seasonality_profile).where(seasonality_profile.c.profile_id == profile_id)
        )


Preset = Literal["working_days", "calendar_days"]


def profile_preset(engine: Engine, actor: User, preset: Preset, fiscal_year: int) -> list[Decimal]:
    """Starting values for a new profile, one per fiscal month of the given year."""
    actor.require_controller()
    with unit_of_work(engine, actor) as conn:
        start = conn.execute(select(installation.c.fiscal_year_start_month)).scalar_one()
    match preset:
        case "working_days":
            days = working_days(fiscal_year, start)
        case "calendar_days":
            days = calendar_days(fiscal_year, start)
    return [Decimal(d) for d in days]


def set_entry_form(
    engine: Engine,
    actor: User,
    code: str,
    account_code: str | None,
    form: Form | None,
    profile_code: str | None = None,
    reference_version_code: str | None = None,
) -> None:
    """Set the entry form of an account group or account, or with ``account_code`` None the
    module default. ``form`` None removes an account's own setting."""
    actor.require_controller()
    with unit_of_work(engine, actor) as conn:
        r = get_round(conn, code)
        if account_code is None:
            if form is None or profile_code or reference_version_code:
                raise RoundError("the module default is a form without a basis")
            _set_default_form(conn, r, form)
        else:
            _set_entry_form(conn, r, account_code, form, profile_code, reference_version_code)


def _set_default_form(conn: Connection, r: RoundRef, form: Form) -> None:
    if form not in FORMS:
        raise RoundError(f"unknown entry form {form}")
    conn.execute(
        update(round_module)
        .where(
            round_module.c.version_id == r.version_id, round_module.c.module == MODULE_COST_CENTER
        )
        .values(default_form=form)
    )


def _set_entry_form(
    conn: Connection,
    r: RoundRef,
    account_code: str,
    form: Form | None,
    profile_code: str | None,
    reference_version_code: str | None,
) -> None:
    if form is not None and form not in FORMS:
        raise RoundError(f"unknown entry form {form}")
    account_id = _member_id(conn, ACCOUNT, account_code)
    conn.execute(
        delete(entry_form).where(
            entry_form.c.version_id == r.version_id,
            entry_form.c.module == MODULE_COST_CENTER,
            entry_form.c.account_id == account_id,
        )
    )
    if form is None:
        return
    profile_id = None
    if profile_code is not None:
        profile_id = conn.execute(
            select(seasonality_profile.c.profile_id).where(
                seasonality_profile.c.code == profile_code
            )
        ).scalar()
        if profile_id is None:
            raise NotFoundError(f"seasonality profile {profile_code} not found")
    reference = _version_id(conn, reference_version_code) if reference_version_code else None
    if form == "annual_profile" and profile_id is None:
        raise RoundError(f"{account_code}: a seasonality profile is needed")
    if profile_id is not None and form not in ("monthly", "annual_profile"):
        raise RoundError(f"{account_code}: {form} doesn't spread by a profile")
    if reference is not None and form not in ("monthly", "annual_reference"):
        raise RoundError(f"{account_code}: {form} doesn't spread by a reference")
    conn.execute(
        insert(entry_form).values(
            version_id=r.version_id,
            module=MODULE_COST_CENTER,
            account_id=account_id,
            form=form,
            profile_id=profile_id,
            reference_version_id=reference,
        )
    )


def set_comparisons(engine: Engine, actor: User, code: str, columns: list[Comparison]) -> None:
    """The comparison columns, in order."""
    actor.require_controller()
    with unit_of_work(engine, actor) as conn:
        _set_comparisons(conn, get_round(conn, code), columns)


def _set_comparisons(conn: Connection, r: RoundRef, columns: list[Comparison]) -> None:
    rows = []
    for position, column in enumerate(columns, start=1):
        kind, other = column.kind, column.version_code
        if kind not in ("prior_year_actuals", "actuals", "version"):
            raise RoundError(f"unknown comparison {kind}")
        if (kind == "version") != (other is not None):
            raise RoundError("a version comparison names a version, the others don't")
        other_id = _version_id(conn, other) if other else None
        if other_id == r.version_id:
            raise RoundError("a round can't compare with itself")
        rows.append(
            {
                "version_id": r.version_id,
                "module": MODULE_COST_CENTER,
                "position": position,
                "kind": kind,
                "compare_version_id": other_id,
                "note": (column.note or "").strip() or None,
            }
        )
    conn.execute(
        delete(comparison).where(
            comparison.c.version_id == r.version_id, comparison.c.module == MODULE_COST_CENTER
        )
    )
    if rows:
        conn.execute(insert(comparison), rows)


def set_account_planned(
    engine: Engine, actor: User, code: str, account_code: str, planned: bool
) -> None:
    """Mark an account planned or not planned in a round (ADR 0014). Not planned is refused while
    any cost center has plan values on it."""
    actor.require_controller()
    with unit_of_work(engine, actor) as conn:
        r = get_round(conn, code)
        if planned:
            _include(conn, r, account_code)
        else:
            _exclude(conn, r, account_code)


def _exclude(conn: Connection, r: RoundRef, account_code: str) -> None:
    """Mark an account not planned. In a round in setup its plan values, which the controller
    carried over or prepared, are cleared with it; in an open round it must have none."""
    account_id = _member_id(conn, ACCOUNT, account_code, leaf=True)
    f = fact_cost_center
    in_round = (f.c.version_id == r.version_id) & (f.c.dim_account == account_id)
    if r.status == "setup":
        conn.execute(delete(f).where(in_round))
    with_values = list(
        conn.execute(
            select(member.c.code)
            .distinct()
            .join(f, f.c.dim_cost_center == member.c.member_id)
            .where(in_round)
            .order_by(member.c.code)
        ).scalars()
    )
    if with_values:
        raise RoundError(
            f"account {account_code} still has plan values on {', '.join(with_values)}"
        )
    conn.execute(
        pg_insert(not_planned_account)
        .values(version_id=r.version_id, module=MODULE_COST_CENTER, account_id=account_id)
        .on_conflict_do_nothing()
    )


def _include(conn: Connection, r: RoundRef, account_code: str) -> None:
    account_id = _member_id(conn, ACCOUNT, account_code, leaf=True)
    conn.execute(
        delete(not_planned_account).where(
            not_planned_account.c.version_id == r.version_id,
            not_planned_account.c.module == MODULE_COST_CENTER,
            not_planned_account.c.account_id == account_id,
        )
    )


# --- The setup page (E-03) ------------------------------------------------------------------------


@dataclass
class EntryFormRule:
    account_code: str  # an account group or an account
    form: Form
    profile_code: str | None = None
    reference_version_code: str | None = None


@dataclass
class AccountSet:
    """A named list of accounts and account groups, assigned to cost center nodes or cost
    centers (ADR 0014)."""

    name: str
    accounts: list[str]  # account or account group codes
    assigned_to: list[str] = field(default_factory=list)  # cost center node or cost center codes


@dataclass
class Setup:
    """A round's whole setup, as the setup page edits it."""

    default_form: Form
    entry_forms: list[EntryFormRule]
    comparisons: list[Comparison]
    not_planned_accounts: list[str]
    deadline: date | None
    # Cost centers planned with capacity planning (ADR 0013), by code.
    productive_cost_centers: list[str] = field(default_factory=list)
    # Cost centers planned in the round, by code; None leaves them as they are.
    planned_cost_centers: list[str] | None = None
    account_sets: list[AccountSet] = field(default_factory=list)
    grain: Grain = "month"
    actuals_through: date | None = None  # forecasts: the last closed month (ADR 0015)


@dataclass
class SetupView:
    round: RoundRef
    copied_from: str | None
    setup: Setup
    actuals_available: bool  # synced actuals exist, so actuals comparisons and references work
    enabled_cost_centers: int
    sample_cost_center: str | None  # a name for the preview
    periods: list[date]
    created: "Created | None" = None
    latest_actuals: date | None = None  # the latest month with synced actuals


@dataclass
class Created:
    """How a round was created (ADR 0014); ``carried`` is None for a round set up from the
    defaults."""

    by: str | None
    at: datetime | None
    carried: CarryOver | None
    cells: int | None  # plan values copied; None: none were carried


def _created(conn: Connection, version_id: int) -> Created | None:
    row = conn.execute(
        select(
            planning_round.c.created_by, planning_round.c.created_at, planning_round.c.carried
        ).where(planning_round.c.version_id == version_id)
    ).one()
    if row.created_at is None:
        return None  # created before rounds kept a record
    record = row.carried
    if record is None:
        return Created(row.created_by, row.created_at, None, None)
    carry = CarryOver(**{k: bool(record[k]) for k in CARRY_ITEMS}, uplift=Decimal(record["uplift"]))
    return Created(row.created_by, row.created_at, carry, record.get("cells"))


def setup_of(engine: Engine, user: User, code: str) -> SetupView:
    user.require_controller()
    with engine.connect() as conn:
        r = get_round(conn, code)
        default = conn.execute(
            select(round_module.c.default_form).where(
                round_module.c.version_id == r.version_id,
                round_module.c.module == MODULE_COST_CENTER,
            )
        ).scalar_one()
        acc, ref = member.alias("acc"), version.alias("ref")
        rules = [
            EntryFormRule(a, f, p, v)
            for a, f, p, v in conn.execute(
                select(acc.c.code, entry_form.c.form, seasonality_profile.c.code, ref.c.code)
                .join(acc, acc.c.member_id == entry_form.c.account_id)
                .outerjoin(
                    seasonality_profile,
                    seasonality_profile.c.profile_id == entry_form.c.profile_id,
                )
                .outerjoin(ref, ref.c.version_id == entry_form.c.reference_version_id)
                .where(
                    entry_form.c.version_id == r.version_id,
                    entry_form.c.module == MODULE_COST_CENTER,
                )
                .order_by(acc.c.code)
            )
        ]
        columns = [
            Comparison(k, v, n)
            for k, v, n in conn.execute(
                select(comparison.c.kind, version.c.code, comparison.c.note)
                .outerjoin(version, version.c.version_id == comparison.c.compare_version_id)
                .where(
                    comparison.c.version_id == r.version_id,
                    comparison.c.module == MODULE_COST_CENTER,
                )
                .order_by(comparison.c.position)
            )
        ]
        not_planned = list(
            conn.execute(
                select(member.c.code)
                .join(not_planned_account, not_planned_account.c.account_id == member.c.member_id)
                .where(not_planned_account.c.version_id == r.version_id)
                .order_by(member.c.code)
            ).scalars()
        )
        sets = _account_sets(conn, r.version_id)
        copied = conn.execute(
            select(version.c.code)
            .join(planning_round, planning_round.c.copied_from == version.c.version_id)
            .where(planning_round.c.version_id == r.version_id)
        ).scalar()
        periods = fiscal_periods(conn, r.fiscal_year)
        created = _created(conn, r.version_id)
        actuals = (
            conn.execute(
                select(dataset_mapping.c.dataset).where(dataset_mapping.c.dataset == "actuals")
            ).first()
            is not None
        )
        enabled = conn.execute(
            select(member.c.code, member.c.name)
            .join(round_member, round_member.c.member_id == member.c.member_id)
            .where(
                round_member.c.version_id == r.version_id,
                round_member.c.module == MODULE_COST_CENTER,
                round_member.c.enabled.is_(True),
            )
            .order_by(member.c.code)
        ).all()
        productive = list(
            conn.execute(
                select(member.c.code)
                .join(round_member, round_member.c.member_id == member.c.member_id)
                .where(
                    round_member.c.version_id == r.version_id,
                    round_member.c.module == MODULE_CAPACITY,
                    round_member.c.enabled.is_(True),
                )
                .order_by(member.c.code)
            ).scalars()
        )
    return SetupView(
        r,
        copied,
        Setup(
            default,
            rules,
            columns,
            not_planned,
            r.deadline,
            productive,
            [c for c, _ in enabled],
            sets,
            r.grain,
            r.actuals_through,
        ),
        actuals,
        len(enabled),
        enabled[0][1] if enabled else None,
        periods,
        created,
        latest_actuals(engine, user),
    )


def _account_sets(conn: Connection, version_id: int) -> list[AccountSet]:
    sets = {
        set_id: AccountSet(name, [], [])
        for set_id, name in conn.execute(
            select(account_set.c.set_id, account_set.c.name)
            .where(
                account_set.c.version_id == version_id,
                account_set.c.module == MODULE_COST_CENTER,
            )
            .order_by(account_set.c.name)
        )
    }
    for set_id, code in conn.execute(
        select(account_set_account.c.set_id, member.c.code)
        .join(member, member.c.member_id == account_set_account.c.account_id)
        .where(account_set_account.c.set_id.in_(sets))
        .order_by(member.c.code)
    ):
        sets[set_id].accounts.append(code)
    a = account_set_assignment
    for set_id, code in conn.execute(
        select(a.c.set_id, member.c.code)
        .join(member, member.c.member_id == a.c.member_id)
        .where(a.c.version_id == version_id, a.c.module == MODULE_COST_CENTER)
        .order_by(member.c.code)
    ):
        sets[set_id].assigned_to.append(code)
    return list(sets.values())


def save_setup(engine: Engine, actor: User, code: str, setup: Setup) -> None:
    """Replace a round's setup in one transaction; any invalid part changes nothing."""
    actor.require_controller()
    with unit_of_work(engine, actor) as conn:
        r = get_round(conn, code, lock="update")
        if r.status == "closed":
            raise RoundError("the round is closed; reopen it to change its setup")
        _set_default_form(conn, r, setup.default_form)
        conn.execute(
            delete(entry_form).where(
                entry_form.c.version_id == r.version_id,
                entry_form.c.module == MODULE_COST_CENTER,
            )
        )
        seen: set[str] = set()
        for rule in setup.entry_forms:
            if rule.account_code in seen:
                raise RoundError(f"{rule.account_code} has two entry forms")
            seen.add(rule.account_code)
            _set_entry_form(
                conn,
                r,
                rule.account_code,
                rule.form,
                rule.profile_code,
                rule.reference_version_code,
            )
        _set_comparisons(conn, r, setup.comparisons)
        now_excluded = set(
            conn.execute(
                select(member.c.code)
                .join(not_planned_account, not_planned_account.c.account_id == member.c.member_id)
                .where(not_planned_account.c.version_id == r.version_id)
            ).scalars()
        )
        for account in sorted(now_excluded - set(setup.not_planned_accounts)):
            _include(conn, r, account)
        for account in sorted(set(setup.not_planned_accounts) - now_excluded):
            _exclude(conn, r, account)
        if setup.planned_cost_centers is not None:
            _set_planned(conn, r, setup.planned_cost_centers)
        _set_account_sets(conn, r, setup.account_sets)
        _clear_outside_sets(conn, r)
        _set_productive(conn, r, setup.productive_cost_centers)
        _set_periods(conn, r, setup.grain, setup.actuals_through)
        conn.execute(
            update(planning_round)
            .where(planning_round.c.version_id == r.version_id)
            .values(deadline=setup.deadline)
        )


def _set_periods(conn: Connection, r: RoundRef, grain: Grain, actuals_through: date | None) -> None:
    """The round's grain and closed months (ADR 0015). Months it closes now may hold plan values:
    in a round in setup they are cleared in the same save; in an open round the save is refused
    and names the cost centers."""
    _check_periods(conn, r.kind, r.fiscal_year, grain, actuals_through)
    periods = fiscal_periods(conn, r.fiscal_year)
    before = closed_months(periods, r.actuals_through)
    closing = periods[before : closed_months(periods, actuals_through)]
    if closing:
        f = fact_cost_center
        in_closing = (f.c.version_id == r.version_id) & f.c.period.in_(closing)
        if r.status == "setup":
            conn.execute(delete(f).where(in_closing))
        with_values = list(
            conn.execute(
                select(member.c.code)
                .distinct()
                .join(f, f.c.dim_cost_center == member.c.member_id)
                .where(in_closing)
                .order_by(member.c.code)
            ).scalars()
        )
        if with_values:
            raise RoundError(
                "the months to close still have plan values on " + ", ".join(with_values)
            )
    conn.execute(
        update(planning_round)
        .where(planning_round.c.version_id == r.version_id)
        .values(grain=grain, actuals_through=actuals_through)
    )


def _set_planned(conn: Connection, r: RoundRef, codes: list[str]) -> None:
    """The cost centers planned in the round. One the round hasn't seen and that isn't listed
    stays without a row: not planned, and new on the status page (ADR 0007)."""
    wanted = {_member_id(conn, COST_CENTER, code, leaf=True) for code in codes}
    seen = _round_members(conn, r.version_id, MODULE_COST_CENTER, enabled=True) | _round_members(
        conn, r.version_id, MODULE_COST_CENTER, enabled=False
    )
    rows = [
        {
            "version_id": r.version_id,
            "module": MODULE_COST_CENTER,
            "member_id": m,
            "enabled": m in wanted,
        }
        for m in sorted(wanted | seen)
    ]
    if rows:
        stmt = pg_insert(round_member).values(rows)
        conn.execute(
            stmt.on_conflict_do_update(
                index_elements=list(round_member.primary_key.columns),
                set_={"enabled": stmt.excluded.enabled},
            )
        )


def _set_account_sets(conn: Connection, r: RoundRef, sets: list[AccountSet]) -> None:
    """Replace the round's account sets and their assignments."""
    conn.execute(
        delete(account_set).where(
            account_set.c.version_id == r.version_id,
            account_set.c.module == MODULE_COST_CENTER,
        )
    )
    names: set[str] = set()
    assigned: dict[str, str] = {}
    for s in sets:
        name = s.name.strip()
        if not name:
            raise RoundError("an account set needs a name")
        if name in names:
            raise RoundError(f"two account sets are named {name}")
        names.add(name)
        set_id = conn.execute(
            insert(account_set)
            .values(version_id=r.version_id, module=MODULE_COST_CENTER, name=name)
            .returning(account_set.c.set_id)
        ).scalar_one()
        accounts = {_member_id(conn, ACCOUNT, code) for code in s.accounts}
        if accounts:
            conn.execute(
                insert(account_set_account),
                [{"set_id": set_id, "account_id": a} for a in sorted(accounts)],
            )
        for code in s.assigned_to:
            if code in assigned:
                raise RoundError(f"{code} is assigned to {assigned[code]} and {name}")
            assigned[code] = name
            conn.execute(
                insert(account_set_assignment).values(
                    version_id=r.version_id,
                    module=MODULE_COST_CENTER,
                    member_id=_member_id(conn, COST_CENTER, code),
                    set_id=set_id,
                )
            )


def _clear_outside_sets(conn: Connection, r: RoundRef) -> None:
    """Plan values outside a cost center's account set: cleared in a round in setup, where they
    are values the controller carried over or prepared; refused in an open round, where a
    planner entered them and couldn't clear them any more (ADR 0014)."""
    f = fact_cost_center
    with_values = {
        cc: set(accounts)
        for cc, accounts in conn.execute(
            select(f.c.dim_cost_center, func.array_agg(f.c.dim_account.distinct()))
            .where(f.c.version_id == r.version_id)
            .group_by(f.c.dim_cost_center)
        )
    }
    credited = set(conn.execute(select(credit_account.c.account_id)).scalars())
    outside: list[tuple[int, int]] = []
    for cc, scope in account_scopes(conn, r.version_id, with_values).items():
        if scope is not None:
            outside += [(cc, a) for a in with_values[cc] - scope - credited]
    if not outside:
        return
    if r.status != "setup":
        codes = dict(
            conn.execute(
                select(member.c.member_id, member.c.code).where(
                    member.c.member_id.in_({cc for cc, _ in outside})
                )
            ).all()
        )
        raise RoundError(
            "plan values outside the account set on "
            f"{', '.join(sorted({codes[cc] for cc, _ in outside}))}; clear them first"
        )
    conn.execute(
        delete(f).where(
            f.c.version_id == r.version_id,
            tuple_(f.c.dim_cost_center, f.c.dim_account).in_(outside),
        )
    )


def _set_productive(conn: Connection, r: RoundRef, codes: list[str]) -> None:
    """The round's productive cost centers (ADR 0013). One that stops being productive must
    have no capacity planned in the round."""
    wanted = {_member_id(conn, COST_CENTER, code, leaf=True) for code in codes}
    current = set(
        conn.execute(
            select(round_member.c.member_id).where(
                round_member.c.version_id == r.version_id,
                round_member.c.module == MODULE_CAPACITY,
                round_member.c.enabled.is_(True),
            )
        ).scalars()
    )
    removed = current - wanted
    if removed:
        used = union(
            select(capacity_line.c.cost_center_id).where(
                capacity_line.c.version_id == r.version_id,
                capacity_line.c.cost_center_id.in_(removed),
            ),
            select(fact_capacity.c.dim_cost_center).where(
                fact_capacity.c.version_id == r.version_id,
                fact_capacity.c.dim_cost_center.in_(removed),
            ),
        ).subquery()
        names = (
            conn.execute(
                select(member.c.code)
                .where(member.c.member_id.in_(select(used.c.cost_center_id)))
                .order_by(member.c.code)
            )
            .scalars()
            .all()
        )
        if names:
            raise RoundError(
                f"capacity is planned for {', '.join(names)} in this round; "
                "clear it before making them non-productive"
            )
    if wanted:
        conn.execute(
            pg_insert(round_module)
            .values(version_id=r.version_id, module=MODULE_CAPACITY)
            .on_conflict_do_nothing()
        )
    conn.execute(
        delete(round_member).where(
            round_member.c.version_id == r.version_id,
            round_member.c.module == MODULE_CAPACITY,
        )
    )
    if wanted:
        conn.execute(
            insert(round_member),
            [
                {
                    "version_id": r.version_id,
                    "module": MODULE_CAPACITY,
                    "member_id": m,
                    "enabled": True,
                }
                for m in sorted(wanted)
            ],
        )


@dataclass
class ProfileRow:
    code: str
    name: str
    entered_as: str
    values: list[Decimal]


def profiles(engine: Engine) -> list[ProfileRow]:
    with engine.connect() as conn:
        return [
            ProfileRow(r.code, r.name, r.entered_as, list(r.values))
            for r in conn.execute(select(seasonality_profile).order_by(seasonality_profile.c.code))
        ]


def rounds(engine: Engine) -> list[RoundRef]:
    with engine.connect() as conn:
        rows = conn.execute(
            _ROUNDS.order_by(planning_round.c.fiscal_year.desc(), version.c.code)
        ).all()
    return [RoundRef(**r._mapping) for r in rows]


# --- The round's scope (ADR 0014) -----------------------------------------------------------------


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
