"""Development users (TD-09) and plan values (TD-19) for the Nordwerk test data; refused in
production.

The cost center owners are the generator's ``USERS`` (tools/gen_testdata, ``master.py``); its
``UNMATCHED_OWNER`` deliberately gets no user. Besides them: an admin, a controller, the CFO
(viewer, approver at the root node) and an auditor (viewer). One owner is also deputy for a
second, disjoint department.
"""

import random
from dataclasses import replace
from decimal import Decimal

from sqlalchemy import Engine, func, select

from planlayer import access, planning, workflow
from planlayer.domain.workflow import Status
from planlayer.rounds import fiscal_periods, get_round
from planlayer.store.schema import (
    COST_CENTER,
    app_user,
    cost_center_owner,
    credit_account,
    fact_cost_center,
    member,
    version,
    workflow_status,
)
from planlayer.store.uow import unit_of_work
from planlayer.workflow import enabled_members

OWNERS = (
    "anna.keller@nordwerk.example",
    "jonas.brandt@nordwerk.example",
    "marta.nowak@nordwerk.example",
    "luca.meier@nordwerk.example",
    "emily.carter@nordwerk.example",
    "felix.wagner@nordwerk.example",
    "ryan.brooks@nordwerk.example",
    "sabine.roth@nordwerk.example",
)
ROOT, DEPUTY = "NW", ("anna.keller@nordwerk.example", "DEP_VALVE_PROD")


def _name(email: str) -> str:
    return " ".join(p.capitalize() for p in email.split("@")[0].split("."))


def seed(engine: Engine) -> list[str]:
    """Create the users that don't exist yet; returns the logins created."""
    system = access.system_user(engine)
    # The system user creates the users; it acts as admin for this only.
    admin = replace(system, is_admin=True)
    existing = {u.login for u in access.users(engine)}
    wanted: list[tuple[str, str, dict[str, bool]]] = [(e, _name(e), {}) for e in OWNERS] + [
        ("admin@nordwerk.example", "Admin", {"is_admin": True}),
        ("controller@nordwerk.example", "Controller", {"is_controller": True}),
        ("cfo@nordwerk.example", "CFO", {"is_viewer": True}),
        ("auditor@nordwerk.example", "Auditor", {"is_viewer": True}),
    ]
    created = []
    for login, name, roles in wanted:
        if login not in existing:
            access.create_user(engine, admin, login, name, login, **roles)
            created.append(login)
    with engine.connect() as conn:
        nodes = set(
            conn.execute(
                select(member.c.code).where(
                    member.c.dim_id == COST_CENTER, member.c.code.in_([ROOT, DEPUTY[1]])
                )
            ).scalars()
        )
    if ROOT in nodes:
        access.add_approver(engine, system, ROOT, "cfo@nordwerk.example")
    if DEPUTY[1] in nodes:
        access.add_deputy(engine, system, DEPUTY[1], DEPUTY[0])
    return created


# --- Plan values (TD-19) --------------------------------------------------------------------------

PLAN_SEED = 42
# How far each seeded cost center got: (status, how many).
STAGES: tuple[tuple[Status, int], ...] = (("in_progress", 5), ("submitted", 3), ("approved", 2))


def seed_plans(engine: Engine, round_code: str) -> list[tuple[str, str, str]]:
    """Plan values for some cost centers of a round, as if their owners had begun planning: year
    totals near last year's actuals, entered by the owner (so the change log and statuses are
    theirs); some cost centers submitted, some approved by their approver. A cost center that
    already has plan values or a status is left alone, so running it again adds nothing new.
    Returns (cost center, owner, status) per seeded cost center."""
    system = access.system_user(engine)
    with unit_of_work(engine, system) as conn:
        r = get_round(conn, round_code)
        enabled = {m for m, on in enabled_members(conn, r.version_id).items() if on}
        busy = set(
            conn.execute(
                select(workflow_status.c.cost_center_id).where(
                    workflow_status.c.version_id == r.version_id
                )
            ).scalars()
        ) | set(
            conn.execute(
                select(fact_cost_center.c.dim_cost_center)
                .where(fact_cost_center.c.version_id == r.version_id)
                .distinct()
            ).scalars()
        )
        owners = {
            cc: login
            for cc, login in conn.execute(
                select(cost_center_owner.c.cost_center_id, app_user.c.login).join(
                    app_user, app_user.c.user_id == cost_center_owner.c.user_id
                )
            )
        }
        codes = dict(
            conn.execute(
                select(member.c.member_id, member.c.code).where(member.c.dim_id == COST_CENTER)
            ).all()
        )
        actuals = conn.execute(
            select(version.c.version_id).where(version.c.kind == "actuals")
        ).scalar()
        prior = fiscal_periods(conn, r.fiscal_year - 1)
        totals: dict[int, dict[str, Decimal]] = {}
        acc = member.alias("acc")
        for cc, account, amount in conn.execute(
            select(
                fact_cost_center.c.dim_cost_center,
                acc.c.code,
                func.sum(fact_cost_center.c.amount),
            )
            .join(acc, acc.c.member_id == fact_cost_center.c.dim_account)
            .where(
                fact_cost_center.c.version_id == actuals,
                fact_cost_center.c.period.in_(prior),
            )
            .group_by(fact_cost_center.c.dim_cost_center, acc.c.code)
        ):
            if amount > 0:
                totals.setdefault(cc, {})[account] = amount

    # Free cost centers with an owner and last year's actuals, spread over the owners.
    candidates = sorted(
        (cc for cc in enabled - busy if cc in owners and cc in totals), key=lambda c: codes[c]
    )
    by_owner: dict[str, list[int]] = {}
    for cc in candidates:
        by_owner.setdefault(owners[cc], []).append(cc)
    picked: list[int] = []
    while len(picked) < sum(n for _, n in STAGES) and any(by_owner.values()):
        for ccs in by_owner.values():
            if ccs and len(picked) < sum(n for _, n in STAGES):
                picked.append(ccs.pop(0))

    stages = [status for status, n in STAGES for _ in range(n)]
    seeded: list[tuple[str, str, str]] = []
    for cc, status in zip(picked, stages, strict=False):
        code, owner = codes[cc], owners[cc]
        with engine.connect() as conn:
            planner = access.user_by_login(conn, owner)
        rng = random.Random(f"{PLAN_SEED}:{round_code}:{code}")
        edits = [
            planning.Edit(
                account,
                None,
                str(_hundreds(amount * Decimal(str(rng.uniform(0.94, 1.09))))),
                [None] * planning.MONTHS,
            )
            for account, amount in sorted(totals[cc].items())
        ]
        planning.apply_edits(engine, planner, round_code, code, edits)
        if status in ("submitted", "approved"):
            workflow.act(engine, planner, round_code, "submit", cost_centers=[code])
        if status == "approved":
            with engine.connect() as conn:
                nearest = access.approvers(conn, [cc]).get(cc, [])
                approver = access.user_by_login(conn, nearest[0][1]) if nearest else system
            workflow.act(engine, approver, round_code, "approve", cost_centers=[code])
        seeded.append((code, owner, status))
    return seeded


def _hundreds(amount: Decimal) -> Decimal:
    """Rounded to hundreds, as planners round year totals; at least 100."""
    return max(Decimal(100), (amount / 100).quantize(Decimal(1)) * 100)


# Cost forwarding (ADR 0018, TD-23) as the test warehouse books it (TD-22), plus a fixed amount:
# code, name, method, source, receivers (cost center, share or quantity), rate or amount, unit,
# charge and credit account.
FORWARDINGS: tuple[
    tuple[str, str, str, str, tuple[tuple[str, str], ...], str | None, str | None, str, str], ...
] = (
    ("BLDG-A", "Building A, production hall", "rate", "DE1280",
     (("DE1010", "1800"), ("DE1030", "1300"), ("DE1040", "1200"), ("DE1050", "700"),
      ("DE1080", "1500"), ("DE1090", "500")), "95", "m²", "9000", "9005"),
    ("BLDG-B", "Building B, offices", "rate", "DE1280",
     (("DE1240", "220"), ("DE1250", "160"), ("DE1260", "200"), ("DE1230", "180"),
      ("DE1110", "260"), ("DE1120", "240")), "150", "m²", "9010", "9015"),
    ("IT-PC", "IT workplaces", "rate", "DE1260",
     (("DE1010", "12"), ("DE1020", "10"), ("DE1070", "8"), ("DE1080", "10"), ("DE1110", "24"),
      ("DE1120", "26"), ("DE1130", "8"), ("DE1160", "30"), ("DE1240", "18"), ("DE1250", "12"),
      ("DE1230", "9")), "1500", "PC", "9020", "9025"),
    ("IT-CAD", "CAD licences", "rate", "DE1260",
     (("DE1110", "12"), ("DE1120", "14"), ("DE1130", "4"), ("DE1090", "3")), "6000", "licence",
     "9030", "9035"),
    ("MGMT", "Plant management fee", "fixed", "DE1230",
     (("DE1010", "3"), ("DE1030", "2"), ("DE1040", "2"), ("DE1050", "1")), "180000", None,
     "9040", "9045"),
    ("TOOLS", "Tooling", "full", "DE1090",
     (("DE1010", "40"), ("DE1030", "25"), ("DE1040", "20"), ("DE1050", "15")), None, None,
     "9050", "9055"),
)  # fmt: skip
FORWARDING_GROUP = "FORWARDED"  # the test warehouse's group of 8800-8820
ERP_FORWARDING_ACCOUNTS = ("8800", "8810", "8820")


def seed_forwarding(engine: Engine, round_code: str) -> list[str]:
    """The test company's forwardings in a round, as the controller would enter them, and the
    ERP's forwarding accounts marked not planned there. Forwardings the round has are left alone;
    receivers not planned in the round are left out. Returns the codes created."""
    # Imported here: forwarding builds on planning, which this module imports.
    from planlayer import forwarding
    from planlayer.rounds import set_account_planned

    with engine.connect() as conn:
        found = conn.execute(
            select(app_user.c.login).where(app_user.c.login == "controller@nordwerk.example")
        ).scalar()
        controller = access.user_by_login(conn, found) if found else access.system_user(engine)
    page = forwarding.read_page(engine, controller, round_code)
    have = {f.code for f in page.forwardings}
    planned = {c for c, _ in page.cost_centers}
    for account in ERP_FORWARDING_ACCOUNTS:
        set_account_planned(engine, controller, round_code, account, planned=False)
    _plan_sources(engine, controller, round_code, sorted({f[3] for f in FORWARDINGS} & planned))
    created = []
    for code, name, method, source, receivers, value, unit, charge, credit in FORWARDINGS:
        if code in have or source not in planned:
            continue
        given = forwarding.ForwardingIn(
            code=code,
            name=name,
            method=method,  # type: ignore[arg-type]
            source_code=source,
            group_code=FORWARDING_GROUP,
            charge_account=forwarding.AccountIn(charge, f"{name} charged"),
            credit_account=forwarding.AccountIn(credit, f"{name} passed on"),
            receivers=[
                forwarding.ReceiverIn(cc, Decimal(q)) for cc, q in receivers if cc in planned
            ],
            shares=None if method == "rate" else ("portion" if method == "fixed" else "percent"),
            amount=Decimal(value) if method == "fixed" and value else None,
            rate=Decimal(value) if method == "rate" and value else None,
            unit=unit,
        )
        forwarding.save(engine, controller, round_code, given, None)
        created.append(code)
    return created


def _plan_sources(
    engine: Engine, controller: access.User, round_code: str, codes: list[str]
) -> None:
    """Sources without a plan of their own get last year's actuals as year totals, entered by
    the controller, so what they pass on has costs behind it. Accounts the round doesn't plan
    for the cost center are skipped."""
    # Imported here: forwarding builds on planning, which this module imports.
    from planlayer.forwarding import forwarded_accounts

    with unit_of_work(engine, controller) as conn:
        r = get_round(conn, round_code)
        ids = dict(
            conn.execute(
                select(member.c.code, member.c.member_id).where(
                    member.c.dim_id == COST_CENTER, member.c.code.in_(codes)
                )
            ).all()
        )
        forwarded = set(forwarded_accounts(conn, r.version_id))
        f, acc = fact_cost_center, member.alias("acc")
        own = {
            cc
            for (cc,) in conn.execute(
                select(f.c.dim_cost_center)
                .where(
                    f.c.version_id == r.version_id,
                    f.c.dim_cost_center.in_(list(ids.values())),
                    f.c.dim_account.not_in(forwarded or {-1}),
                )
                .distinct()
            )
        }
        actuals = conn.execute(
            select(version.c.version_id).where(version.c.kind == "actuals")
        ).scalar()
        totals: dict[int, list[tuple[str, Decimal]]] = {}
        for cc, account, amount in conn.execute(
            select(f.c.dim_cost_center, acc.c.code, func.sum(f.c.amount))
            .join(acc, acc.c.member_id == f.c.dim_account)
            .where(
                f.c.version_id == actuals,
                f.c.period.in_(fiscal_periods(conn, r.fiscal_year - 1)),
                f.c.dim_cost_center.in_(list(ids.values())),
                acc.c.account_type == "cost",
                acc.c.code.not_in(ERP_FORWARDING_ACCOUNTS),
                f.c.dim_account.not_in(select(credit_account.c.account_id)),
            )
            .group_by(f.c.dim_cost_center, acc.c.code)
            .order_by(acc.c.code)
        ):
            if amount > 0:
                totals.setdefault(cc, []).append((account, amount))
    for code, cc in ids.items():
        if cc in own:
            continue
        for account, amount in totals.get(cc, []):
            edit = planning.Edit(account, None, str(_hundreds(amount)), [None] * planning.MONTHS)
            try:
                planning.apply_edits(engine, controller, round_code, code, [edit])
            except planning.EditError:
                continue  # not planned for this cost center in the round
