"""The demo installation (ADR 0030): the Nordwerk test company in a budget round and its forecast,
with every module in use, from the test warehouse into an empty app store.

Budget 2026 is open and partly planned: entry forms with a seasonality profile, comparison
columns, productive cost centers with planned hours, workforce by positions with personnel costs
released and handed over by HR, cost forwarding, investments, notes, item lists, a proposed
transfer, and the approved cost centers published. Forecast 2026 (6+6) is set up like it: actuals
through June, the budget's values in the open months, comparing with the budget and a projection.

Each step acts as the person who would take it (controller, owners, HR), so statuses, the change
log and Home look as they would after real use. The building blocks are the development seeds
(``devdata``); this module puts them in order.
"""

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Engine, func, select

from planlayer import access, capacity, devdata, personnel, publish, rounds, workforce
from planlayer.domain.entry_forms import Form
from planlayer.domain.spreading import Profile
from planlayer.store.schema import (
    COST_CENTER,
    app_user,
    cost_center_owner,
    member,
    seasonality_profile,
    version,
    workforce_position,
)
from planlayer.store.uow import unit_of_work

if TYPE_CHECKING:
    from planlayer.adapters.source import PostgresSource

BUDGET, FORECAST, YEAR = devdata.DEMO_ROUND, "F2026", devdata.DEMO_YEAR
ACTUALS_THROUGH = date(YEAR, 6, 1)  # the test warehouse's actuals end in June
CONTROLLER, HR = "controller@nordwerk.example", "hr@nordwerk.example"

# Cost centers whose work centers plan hours (ADR 0013): production and engineering.
PRODUCTIVE = ("CH1010", "CH1100", "DE1010", "DE1020", "DE1030", "DE1040", "DE1090", "DE1110",
              "DE1120")  # fmt: skip
# Cost centers whose owners have planned their hours, the rest left to the planner.
HOURS_PLANNED = ("DE1010", "DE1030", "DE1110", "CH1010")
WORKING_DAYS = "WORKING_DAYS"
# Account groups with an entry form other than monthly (ADR 0007).
ENTRY_FORMS: tuple[tuple[str, Form, str | None], ...] = (
    ("DEPRECIATION", "annual", None),
    ("IT", "annual_profile", WORKING_DAYS),
    ("TRAVEL", "annual_even", None),
)
# HR has handed over pay for every cost center but these, still being prepared.
PAY_IN_PREPARATION = ("DE1240", "DE1250", "CH1140")
# A machinist moves from Machining to Assembly Line 1 next year, proposed by Machining's owner.
TRANSFER = ("DE1010", "DE1030")


def _user(engine: Engine, login: str) -> access.User:
    with engine.connect() as conn:
        return access.user_by_login(conn, login)


def seed_setup(engine: Engine, round_code: str) -> None:
    """The round's setup as a controller would make it: entry forms, a working-days profile,
    comparison columns, productive cost centers, and workforce by positions with personnel costs,
    so the positions taken from HR bring HR's salaries along."""
    controller = _user(engine, CONTROLLER)
    with engine.connect() as conn:
        exists = conn.execute(
            select(func.count()).where(seasonality_profile.c.code == WORKING_DAYS)
        ).scalar_one()
    if not exists:
        days = rounds.profile_preset(engine, controller, "working_days", YEAR)
        profile = Profile(tuple(days), "relative")
        rounds.add_profile(engine, controller, WORKING_DAYS, "Working days", profile)
    view = rounds.setup_of(engine, controller, round_code)
    setup = view.setup
    setup.entry_forms = [rounds.EntryFormRule(a, f, p, None) for a, f, p in ENTRY_FORMS]
    setup.comparisons = [rounds.Comparison("prior_year_actuals"), rounds.Comparison("actuals")]
    setup.capacity_work_centers = list(PRODUCTIVE)
    setup.workforce_mode = "positions"
    setup.personnel_costs = True
    rounds.save_setup(engine, controller, round_code, setup)


def seed_capacity(engine: Engine, round_code: str) -> int:
    """Planned hours on the work centers of some productive cost centers, typed by their owners
    as year totals near last year's hours. Returns the lines planned."""
    planned = 0
    with engine.connect() as conn:
        owners = dict(
            conn.execute(
                select(member.c.code, app_user.c.login)
                .join(cost_center_owner, cost_center_owner.c.cost_center_id == member.c.member_id)
                .join(app_user, app_user.c.user_id == cost_center_owner.c.user_id)
                .where(member.c.dim_id == COST_CENTER, member.c.code.in_(HOURS_PLANNED))
            ).all()
        )
    for code in HOURS_PLANNED:
        planner = _user(engine, owners.get(code, CONTROLLER))
        sheet = capacity.read_sheet(engine, planner, round_code, code)
        edits = []
        for row in sheet.rows:
            if row.kind != "line" or not row.editable or row.work_center_code is None:
                continue
            last_year = sum((v for v in row.comparisons[0] if v is not None), Decimal(0))
            if last_year <= 0:
                continue
            hours = (last_year * Decimal("1.02") / 10).quantize(Decimal(1)) * 10
            edits.append(
                capacity.HoursEdit(
                    row.work_center_code, row.activity_type_code, None, str(hours), [None] * 12
                )
            )
        if edits:
            capacity.apply_edits(engine, planner, round_code, code, edits)
            planned += len(edits)
    return planned


def seed_personnel(engine: Engine, round_code: str) -> tuple[int, int]:
    """Personnel costs from the positions (ADR 0022), switched on in the setup: the rules entered
    by HR (a 13th salary, a raise from July, contributions up to their ceilings, a long-service
    award), access released and pay handed over. Returns (rules, handed over)."""
    hr = _user(engine, HR)
    rules = [
        personnel.RuleIO("extra", "13th salary", "6040", "DE", salaries=Decimal(1)),
        personnel.RuleIO(
            "raise", "Collective raise", "6050", percent=Decimal(2), starts=date(YEAR, 7, 1)
        ),
        personnel.RuleIO(
            "contribution",
            "Pension and unemployment",
            "6060",
            "DE",
            percent=Decimal("10.6"),
            ceiling=Decimal(8450),
        ),
        personnel.RuleIO(
            "contribution",
            "Health and care",
            "6070",
            "DE",
            percent=Decimal("9.1"),
            ceiling=Decimal("5812.50"),
        ),
        personnel.RuleIO(
            "award",
            "Long-service award",
            "6080",
            steps=[
                personnel.StepIO(10, salaries=Decimal("0.5")),
                personnel.StepIO(25, salaries=Decimal(1)),
                personnel.StepIO(40, amount=Decimal(2500)),
            ],
        ),
    ]
    page = personnel.read_rules(engine, hr, round_code)
    accounts = {"APP": "6000", "EMP": "6000", "FRL": "6020", "TMP": "6030"}
    types = [
        personnel.StaffTypeIO(t.code, accounts.get(t.code, t.account), t.code in ("FRL", "TMP"))
        for t in page.rules.staff_types
    ]
    # A minimum group of one: no small groups, so the approved cost centers can be published.
    personnel.save_rules(
        engine, hr, round_code, personnel.RulesIO(1, "6090", types, rules, review_required=False)
    )
    personnel.release(engine, hr, round_code, True)
    _, units = personnel.units(engine, hr, round_code)
    handed = 0
    for unit in units:
        if unit.code not in PAY_IN_PREPARATION:
            personnel.hand_over(engine, hr, round_code, unit.code, True)
            handed += 1
    return len(rules), handed


def seed_transfer(engine: Engine, round_code: str) -> int:
    """A position proposed for transfer to another cost center from the start of next year,
    waiting for the receiving owner on Home. Returns how many were proposed (0 or 1)."""
    source, target = TRANSFER
    with unit_of_work(engine, access.system_user(engine)) as conn:
        r = rounds.get_round(conn, round_code)
        owner = conn.execute(
            select(app_user.c.login)
            .join(cost_center_owner, cost_center_owner.c.user_id == app_user.c.user_id)
            .join(member, member.c.member_id == cost_center_owner.c.cost_center_id)
            .where(member.c.code == source)
        ).scalar()
        p = workforce_position
        position = conn.execute(
            select(p.c.position_id)
            .join(member, member.c.member_id == p.c.cost_center_id)
            .where(
                p.c.version_id == r.version_id,
                member.c.code == source,
                p.c.leave_date.is_(None),
                p.c.person_name.is_not(None),
            )
            .order_by(p.c.position_id)
            .limit(1)
        ).scalar()
    if owner is None or position is None:
        return 0
    workforce.propose_transfer(
        engine, _user(engine, owner), round_code, source, position, target, "full", None,
        "Moves to Assembly Line 1 with the new line in January.",
    )  # fmt: skip
    return 1


def seed_publish(engine: Engine, output: Engine, output_schema: str, round_code: str) -> int:
    """The approved cost centers published by the controller. Returns the rows written."""
    controller = _user(engine, CONTROLLER)
    preview = publish.preview(engine, output, output_schema, controller, round_code, "approved")
    done = publish.run(
        engine, output, output_schema, controller, round_code, "approved", preview.fingerprint
    )
    return done.rows


def seed_forecast(engine: Engine, budget: str) -> int:
    """Forecast 2026 (6+6) set up like the budget: actuals through June, the budget's values in
    the open months, comparing with last year, the year to date, the budget and a projection.
    Opened for planning. Returns the plan cells carried."""
    controller = _user(engine, CONTROLLER)
    rounds.create_round(
        engine, controller, FORECAST, "Forecast 2026 (6+6)", "forecast", YEAR,
        copy_from=budget, carry=rounds.CarryOver(values=True), actuals_through=ACTUALS_THROUGH,
    )  # fmt: skip
    rounds.set_comparisons(
        engine,
        controller,
        FORECAST,
        [
            rounds.Comparison("prior_year_actuals"),
            rounds.Comparison("actuals"),
            rounds.Comparison("version", budget),
            rounds.Comparison("projection"),
        ],
    )
    rounds.set_round_status(engine, controller, FORECAST, "open")
    created = rounds.setup_of(engine, controller, FORECAST).created
    return created.cells or 0 if created else 0


def seed_demo(
    engine: Engine,
    source: "PostgresSource",
    source_schema: str,
    output: Engine,
    output_schema: str,
) -> list[str]:
    """The demo into an empty app store. Returns what was done, one line each."""
    # Imported here: sync builds on what this module imports.
    from planlayer import sync

    system = access.system_user(engine)
    with engine.connect() as conn:
        users = conn.execute(
            select(func.count()).where(app_user.c.is_system.is_(False))
        ).scalar_one()
        made = conn.execute(select(func.count()).where(version.c.kind != "actuals")).scalar_one()
    if users or made:
        raise ValueError("seed-demo needs an empty app store: it has users or rounds already")
    for m in sync.default_mappings(source_schema):
        sync.save_mapping(engine, system, m)
    sync.set_pay_from_source(engine, system, True)  # the test warehouse delivers salaries
    # The source is read before anything else is written, so a warehouse that doesn't fit
    # leaves the app store empty for the next try.
    if problems := sync.plan(engine, source).problems:
        raise sync.SyncError(problems)
    synced = sync.run(engine, source, system)  # every year the warehouse delivers
    created = devdata.seed(engine)
    controller = _user(engine, CONTROLLER)
    # After the sync: a new round enables the cost centers there are.
    rounds.create_round(engine, controller, BUDGET, "Budget 2026", "budget", YEAR)
    seed_setup(engine, BUDGET)
    rounds.set_round_status(engine, controller, BUDGET, "open")
    # In this order: forwardings mark accounts not planned before anyone plans; positions and
    # personnel costs come before the owners' plans, which submit and approve some cost centers.
    forwardings = devdata.seed_forwarding(engine, BUDGET)
    taken, replaced, new = devdata.seed_workforce(engine, BUDGET)
    transfers = seed_transfer(engine, BUDGET)
    rules, handed = seed_personnel(engine, BUDGET)
    hours = seed_capacity(engine, BUDGET)
    plans = devdata.seed_plans(engine, BUDGET)
    projects, lines = devdata.seed_capex(engine, BUDGET)
    threads = devdata.seed_notes(engine, BUDGET)
    lists = devdata.seed_item_lists(engine)
    published = seed_publish(engine, output, output_schema, BUDGET)
    carried = seed_forecast(engine, BUDGET)
    return [
        f"synced {synced.members} members, {synced.actual_rows} actual cells from {source_schema}",
        f"created {len(created)} users",
        f"opened {BUDGET}: entry forms, comparisons, {len(PRODUCTIVE)} productive cost centers",
        f"plans begun for {len(plans)} cost centers; hours planned on {hours} work center lines",
        f"{taken} positions from HR, {replaced} replacements, {new} new positions, "
        f"{transfers} transfer proposed",
        f"personnel costs: {rules} rules, pay handed over for {handed} cost centers",
        f"{projects} large projects and {lines} investments",
        f"{len(forwardings)} forwardings, {threads} note threads, {lists} item lists",
        f"published the approved cost centers: {published} rows",
        f"opened {FORECAST}, actuals through {ACTUALS_THROUGH:%B}: {carried} plan cells carried",
    ]
