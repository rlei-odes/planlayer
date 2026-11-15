"""The setup page's view of a round and saving it all or nothing (E-03, ADR 0014)."""

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Connection,
    Engine,
    delete,
    func,
    insert,
    select,
    tuple_,
    union,
    update,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert

from planlayer.access import User
from planlayer.domain.entry_forms import Form
from planlayer.domain.periods import (
    closed_months,
)
from planlayer.domain.workforce import Mode
from planlayer.rounds.core import (
    Grain,
    RoundError,
    RoundRef,
    _check_periods,
    _member_id,
    account_scopes,
    fiscal_periods,
    get_round,
    latest_actuals,
)
from planlayer.rounds.creating import CARRY_ITEMS, CarryOver, _round_members
from planlayer.rounds.forms import (
    Comparison,
    _exclude,
    _include,
    _set_comparisons,
    _set_default_form,
    _set_entry_form,
)
from planlayer.store.schema import (
    ACCOUNT,
    COST_CENTER,
    MODULE_CAPACITY,
    MODULE_COST_CENTER,
    MODULE_WORKFORCE,
    account_set,
    account_set_account,
    account_set_assignment,
    capacity_line,
    comparison,
    credit_account,
    dataset_mapping,
    entry_form,
    fact_capacity,
    fact_cost_center,
    member,
    not_planned_account,
    planning_round,
    round_member,
    round_module,
    seasonality_profile,
    version,
)
from planlayer.store.uow import unit_of_work


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
    # Workforce planning's mode (ADR 0020); None: the round doesn't plan its workforce.
    workforce_mode: Mode | None = None
    # Personnel costs from positions (ADR 0022).
    personnel_costs: bool = False


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
    hours: int | None = None  # planned hour cells copied (ADR 0016)


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
    carry = CarryOver(
        **{k: bool(record.get(k, False)) for k in CARRY_ITEMS}, uplift=Decimal(record["uplift"])
    )
    return Created(row.created_by, row.created_at, carry, record.get("cells"), record.get("hours"))


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
        workforce: Mode | None = conn.execute(
            select(round_module.c.mode).where(
                round_module.c.version_id == r.version_id,
                round_module.c.module == MODULE_WORKFORCE,
            )
        ).scalar()
        # Imported here: personnel costs build on this module.
        from planlayer.personnel import module_on

        personnel = module_on(conn, r.version_id)
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
            workforce,
            personnel,
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
            # Forwarding values leave cost centers no longer planned while they still are, then
            # the forwardings are recomputed without them (ADR 0018). Imported here: forwarding
            # builds on this module.
            from planlayer.forwarding import recompute, release

            release(conn, r, _planned_codes_dropped(conn, r, setup.planned_cost_centers))
            _set_planned(conn, r, setup.planned_cost_centers)
            recompute(conn, r)
        _set_account_sets(conn, r, setup.account_sets)
        _clear_outside_sets(conn, r)
        _set_productive(conn, r, setup.productive_cost_centers)
        _set_periods(conn, r, setup.grain, setup.actuals_through)
        # Imported here: workforce planning and personnel costs build on this module. Personnel
        # costs go off before positions do, and come on once the round plans them.
        from planlayer.personnel import switch
        from planlayer.workforce import set_mode

        if not setup.personnel_costs:
            switch(conn, r, actor, on=False)
        set_mode(conn, r, setup.workforce_mode)
        if setup.personnel_costs:
            switch(conn, r, actor, on=True)
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


def _planned_codes_dropped(conn: Connection, r: RoundRef, codes: list[str]) -> set[int]:
    """The cost centers planned in the round now that ``codes`` no longer lists."""
    wanted = {_member_id(conn, COST_CENTER, code, leaf=True) for code in codes}
    return _round_members(conn, r.version_id, MODULE_COST_CENTER, enabled=True) - wanted


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
