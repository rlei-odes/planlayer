"""Master data of Nordwerk Industrietechnik (TD-01 to TD-05).

Everything is built from fixed tables below; the variant (TD-11) only selects and replicates.
Special cases that tests rely on are named constants, so tests refer to them by name.
"""

import random
from dataclasses import dataclass, field
from datetime import date, timedelta

from gen_testdata.calendar import Calendar, add_months

OPEN_END = date(9999, 12, 31)
EARLY_START = date(2010, 1, 1)
ONE_DAY = timedelta(days=1)


@dataclass(frozen=True)
class Variant:
    name: str
    entities: tuple[str, ...]
    full_years: int
    # Replication factor for cost centers and SKUs (large = 20).
    scale: int = 1
    # Selection limits for the small variant (None = everything).
    max_cc_per_department: int | None = None
    max_accounts_per_group: int | None = None
    max_groups_per_line: int | None = None
    max_skus_per_group: int | None = None
    # Share of eligible accounts a cost center posts to.
    account_share: float = 0.6


VARIANTS = {
    "small": Variant(
        "small",
        entities=("DE",),
        full_years=1,
        max_cc_per_department=3,
        max_accounts_per_group=2,
        max_groups_per_line=2,
        max_skus_per_group=3,
    ),
    "standard": Variant("standard", entities=("DE", "CH", "PL", "US"), full_years=3),
    "large": Variant(
        "large", entities=("DE", "CH", "PL", "US"), full_years=5, scale=20, account_share=0.65
    ),
}

# --- Entities and countries (TD-01, TD-05) -------------------------------------------------------


@dataclass(frozen=True)
class Entity:
    code: str
    name: str
    country: str
    currency: str


ENTITIES = (
    Entity("DE", "Nordwerk Industrietechnik GmbH", "DE", "EUR"),
    Entity("CH", "Nordwerk Armaturen AG", "CH", "CHF"),
    Entity("PL", "Nordwerk Polska Sp. z o.o.", "PL", "PLN"),
    Entity("US", "Nordwerk Flow Inc.", "US", "USD"),
)

REGIONS = {
    "DACH": "DACH",
    "WEU": "Western Europe",
    "CEE": "Central & Eastern Europe",
    "AMER": "Americas",
}


@dataclass(frozen=True)
class Country:
    code: str
    name: str
    region: str
    entity: str
    # Cost center (by name) that books this country's revenue.
    sales_cc: str


COUNTRIES = (
    Country("DE", "Germany", "DACH", "DE", "Sales DACH"),
    Country("AT", "Austria", "DACH", "DE", "Sales DACH"),
    Country("CH", "Switzerland", "DACH", "CH", "Sales Switzerland"),
    Country("FR", "France", "WEU", "DE", "Sales Western Europe"),
    Country("NL", "Netherlands", "WEU", "DE", "Sales Western Europe"),
    Country("IT", "Italy", "WEU", "DE", "Sales Western Europe"),
    Country("ES", "Spain", "WEU", "DE", "Sales Western Europe"),
    Country("PL", "Poland", "CEE", "PL", "Sales CEE"),
    Country("CZ", "Czechia", "CEE", "PL", "Sales CEE"),
    Country("HU", "Hungary", "CEE", "PL", "Sales CEE"),
    Country("US", "United States", "AMER", "US", "Sales US"),
    Country("CA", "Canada", "AMER", "US", "Sales Canada"),
)

# --- Users that cost center owners refer to (TD-09 adds roles and grants) ------------------------

USERS = (
    "anna.keller@nordwerk.example",  # Pump production
    "jonas.brandt@nordwerk.example",  # Pump engineering
    "marta.nowak@nordwerk.example",  # Pump plant Poland
    "luca.meier@nordwerk.example",  # Valve production and engineering
    "emily.carter@nordwerk.example",  # US operations
    "felix.wagner@nordwerk.example",  # Sales Europe and service
    "ryan.brooks@nordwerk.example",  # Sales US
    "sabine.roth@nordwerk.example",  # Corporate
)
# An owner the source still delivers, but who has no Planlayer user (T-17 matching).
UNMATCHED_OWNER = "k.weber@nordwerk.example"

# --- Cost centers (TD-02) ------------------------------------------------------------------------

ROOT = "NW"
FUNCTION_ROOT = "FUNC"
FUNCTIONS = {"production": "Production", "sales": "Sales", "admin": "Administration"}

DIVISIONS = {
    "DIV_PUMPS": "Division Pumps",
    "DIV_VALVES": "Division Valves",
    "DIV_SALES": "Sales & Service",
    "DIV_CORP": "Corporate",
}


@dataclass(frozen=True)
class Department:
    code: str
    name: str
    division: str
    function: str
    owner: str | None
    # (cost center name, entity)
    cost_centers: tuple[tuple[str, str], ...]


DEPARTMENTS = (
    Department(
        "DEP_PUMP_PROD",
        "Pump Production",
        "DIV_PUMPS",
        "production",
        USERS[0],
        (
            ("Machining", "DE"),
            ("Quality Assurance", "DE"),
            ("Assembly Line 1", "DE"),
            ("Assembly Line 2", "DE"),
            ("Painting", "DE"),
            ("Test Bench", "DE"),
            ("Maintenance", "DE"),
            ("Plant Logistics", "DE"),
            ("Tooling", "DE"),
            ("Shift Management", "DE"),
        ),
    ),
    Department(
        "DEP_PUMP_ENG",
        "Pump Engineering",
        "DIV_PUMPS",
        "production",
        USERS[1],
        (
            ("Hydraulic Design", "DE"),
            ("Mechanical Design", "DE"),
            ("Prototyping", "DE"),
            ("Product Management Pumps", "DE"),
            ("Certification", "DE"),
        ),
    ),
    Department(
        "DEP_PUMP_PL",
        "Pump Plant Poland",
        "DIV_PUMPS",
        "production",
        USERS[2],
        (
            ("Casting", "PL"),
            ("Machining PL", "PL"),
            ("Assembly PL", "PL"),
            ("Quality PL", "PL"),
            ("Maintenance PL", "PL"),
            ("Warehouse PL", "PL"),
            ("Plant Management PL", "PL"),
            ("Training Centre PL", "PL"),
        ),
    ),
    Department(
        "DEP_VALVE_PROD",
        "Valve Production",
        "DIV_VALVES",
        "production",
        USERS[3],
        (
            ("Forging", "CH"),
            ("Valve Machining", "CH"),
            ("Valve Assembly", "CH"),
            ("Pressure Testing", "CH"),
            ("Coating", "CH"),
            ("Valve Logistics", "CH"),
            ("Valve Quality", "CH"),
            ("Valve Maintenance", "CH"),
        ),
    ),
    Department(
        "DEP_VALVE_ENG",
        "Valve Engineering",
        "DIV_VALVES",
        "production",
        USERS[3],
        (
            ("Valve Design", "CH"),
            ("Materials Lab", "CH"),
            ("Product Management Valves", "CH"),
            ("Actuator Development", "CH"),
        ),
    ),
    Department(
        "DEP_VALVE_US",
        "Valve Assembly US",
        "DIV_VALVES",
        "production",
        USERS[4],
        (
            ("US Assembly", "US"),
            ("US Testing", "US"),
            ("US Warehouse", "US"),
            ("US Quality", "US"),
            ("US Plant Management", "US"),
            ("Legacy Assembly", "US"),
        ),
    ),
    Department(
        "DEP_SALES_EU",
        "Sales Europe",
        "DIV_SALES",
        "sales",
        USERS[5],
        (
            ("Sales DACH", "DE"),
            ("Sales Western Europe", "DE"),
            ("Sales Switzerland", "CH"),
            ("Sales CEE", "PL"),
            ("Key Account Management", "DE"),
        ),
    ),
    Department(
        "DEP_SALES_US",
        "Sales Americas",
        "DIV_SALES",
        "sales",
        USERS[6],
        (("Sales US", "US"), ("Sales Canada", "US"), ("Application Engineering US", "US")),
    ),
    Department(
        "DEP_SERVICE",
        "Service",
        "DIV_SALES",
        "sales",
        USERS[5],
        (
            ("Field Service", "DE"),
            ("Spare Parts", "DE"),
            ("Service Hotline", "DE"),
            ("Digital Service", "DE"),
        ),
    ),
)

# Corporate cost centers hang directly under their division: the tree is ragged.
CORPORATE = (
    ("Executive Office", "DE"),
    ("Finance", "DE"),
    ("Human Resources", "DE"),
    ("IT", "DE"),
    ("Legal", "DE"),
    ("Facilities", "DE"),
    ("Administration CH", "CH"),
    ("Administration PL", "PL"),
    ("Administration US", "US"),
)

# Special cases (TD-02), by cost center name.
MOVED_CC = "Quality Assurance"  # DEP_PUMP_PROD -> DEP_PUMP_ENG at the start of the prior year
MOVED_TO = "DEP_PUMP_ENG"
NEW_CC = "Digital Service"  # created six months into the prior fiscal year
CLOSED_CC = "Legacy Assembly"  # closed at the end of the prior fiscal year
RENAMED_DEPARTMENT = "DEP_PUMP_PL"  # renamed at the start of the current fiscal year
RENAMED_TO = "Pump Plant Wrocław"
NO_OWNER = ("Digital Service", "Tooling", "Legal")
# The next fiscal year, for preparing its round (ADR 0014): a cost center created ahead of it
# (added after all others, so existing codes stay), one closed at the end of the current year,
# and an account no one has booked on yet.
NEXT_NEW_CC = ("Additive Manufacturing", "DE", "DEP_PUMP_PROD")
NEXT_CLOSED_CC = "Shift Management"
NEW_ACCOUNT = ("6950", "Cloud subscriptions", "IT")
UNMATCHED_OWNER_CC = "Test Bench"


@dataclass(frozen=True)
class CostCenter:
    code: str
    name: str
    description: str
    entity: str
    function: str
    owner: str | None
    valid_from: date
    valid_to: date


@dataclass(frozen=True)
class Tranche:
    """A dated row: a hierarchy node's name and parent, or a cost center's parent."""

    code: str
    name: str
    parent: str | None
    valid_from: date
    valid_to: date


@dataclass(frozen=True)
class Account:
    code: str
    name: str
    group: str
    # "revenue" accounts are credit-side in the ledger; "cost" accounts debit-side.
    account_type: str
    functions: tuple[str, ...]
    # Typical monthly amount per cost center in EUR.
    monthly_eur: int


@dataclass(frozen=True)
class Product:
    code: str
    name: str
    group: str
    line: str
    price_eur_cents: int
    countries: tuple[str, ...]
    # Typical units sold per month and country (lumpy for pumps).
    monthly_qty: float


@dataclass
class Master:
    variant: Variant
    calendar: Calendar
    entities: list[Entity]
    countries: list[Country]
    cost_centers: list[CostCenter]
    cc_nodes: list[Tranche]  # root, divisions, departments
    cc_assignments: list[Tranche]  # cost center -> node
    account_sections: dict[str, str]
    account_groups: dict[str, tuple[str, str]]  # group -> (name, section)
    accounts: list[Account]
    product_lines: dict[str, str]
    product_groups: dict[str, tuple[str, str]]  # group -> (name, line)
    products: list[Product]
    work_centers: list["WorkCenter"] = field(default_factory=list)
    users: tuple[str, ...] = field(default=USERS)

    def cc_by_name(self, name: str) -> CostCenter:
        return next(cc for cc in self.cost_centers if cc.name == name)


# --- Accounts (TD-03) ----------------------------------------------------------------------------

ACCOUNT_SECTIONS = {
    "REV": "Revenue",
    "COGS": "Cost of goods sold",
    "OPEX": "Operating expenses",
    "DA": "Depreciation and amortisation",
    "ALLOC": "Internal activity allocation",
}

ALL = ("production", "sales", "admin")
# group, name, section, first code, count, type, functions, monthly EUR per cost center
ACCOUNT_GROUPS = (
    ("SALES_REV", "Sales revenue", "REV", 4000, 5, "revenue", (), 0),
    ("OTHER_REV", "Other operating income", "REV", 4100, 5, "revenue", ("sales", "admin"), 600),
    ("DEDUCTIONS", "Sales deductions", "REV", 4400, 5, "cost", ("sales",), 4000),
    ("MATERIAL", "Direct material", "COGS", 5000, 20, "cost", ("production",), 6000),
    ("FREIGHT_IN", "Inbound freight", "COGS", 5400, 5, "cost", ("production",), 900),
    ("PERSONNEL", "Personnel expenses", "OPEX", 6000, 30, "cost", ALL, 2000),
    ("SUPPLIES", "Consumables and supplies", "OPEX", 6300, 15, "cost", ("production",), 600),
    ("TRAVEL", "Travel expenses", "OPEX", 6600, 10, "cost", ALL, 300),
    ("IT", "IT and software", "OPEX", 6800, 15, "cost", ALL, 400),
    ("FACILITIES", "Facilities", "OPEX", 7000, 15, "cost", ALL, 700),
    ("OTHER_OPEX", "Other operating expenses", "OPEX", 7300, 15, "cost", ("admin",), 1400),
    ("DEPRECIATION", "Depreciation", "DA", 7800, 10, "cost", ("production", "admin"), 1200),
    # Credited by capacity planning (ADR 0013), never booked directly: no functions.
    ("CREDITS", "Activity credits", "ALLOC", 8900, 4, "cost", (), 0),
)
CREDIT_ACCOUNT_NAMES = {
    "8900": "Credits machine hours",
    "8910": "Credits measurement and quality control",
    "8920": "Credits repair work",
    "8930": "Credits engineering hours",
}

# Revenue accounts per product line: sales revenue is booked by line (TD-07).
LINE_REVENUE_ACCOUNT = {
    "CP": "4000",
    "PD": "4010",
    "BV": "4020",
    "FV": "4030",
    "SP": "4040",
}

# --- Products (TD-04) ----------------------------------------------------------------------------

# line code, name, price range EUR, monthly quantity per country
PRODUCT_LINES = (
    ("CP", "Centrifugal pumps", (2_000, 15_000), 0.5),
    ("PD", "Positive displacement pumps", (3_000, 20_000), 0.35),
    ("BV", "Ball valves", (150, 1_500), 4.5),
    ("FV", "Butterfly valves", (300, 3_000), 2.5),
    ("SP", "Spare parts and service kits", (20, 400), 18),
)
GROUPS_PER_LINE = 8
SKUS_PER_GROUP = 10


def build(variant: Variant, calendar: Calendar, seed: int) -> Master:
    rng = random.Random(f"{seed}:master")
    entities = [e for e in ENTITIES if e.code in variant.entities]
    countries = [c for c in COUNTRIES if c.entity in variant.entities]

    cost_centers, cc_nodes, cc_assignments = _cost_centers(variant, calendar)
    account_groups = {g: (name, section) for g, name, section, *_ in ACCOUNT_GROUPS}
    accounts = _accounts(variant)
    product_lines = {code: name for code, name, *_ in PRODUCT_LINES}
    product_groups, products = _products(variant, [c.code for c in countries], rng)
    work_centers = _work_centers(cost_centers, variant)

    return Master(
        variant=variant,
        calendar=calendar,
        entities=entities,
        countries=countries,
        cost_centers=cost_centers,
        cc_nodes=cc_nodes,
        cc_assignments=cc_assignments,
        account_sections=dict(ACCOUNT_SECTIONS),
        account_groups=account_groups,
        accounts=accounts,
        product_lines=product_lines,
        product_groups=product_groups,
        products=products,
        work_centers=work_centers,
    )


def _cost_centers(
    variant: Variant, calendar: Calendar
) -> tuple[list[CostCenter], list[Tranche], list[Tranche]]:
    prior, current = calendar.prior_fy_start, calendar.current_fy_start
    nodes = [Tranche(ROOT, "Nordwerk Group", None, EARLY_START, OPEN_END)]
    nodes += [Tranche(code, name, ROOT, EARLY_START, OPEN_END) for code, name in DIVISIONS.items()]

    groups: list[tuple[str, str, str | None, tuple[tuple[str, str], ...]]] = [
        (d.code, d.function, d.owner, d.cost_centers) for d in DEPARTMENTS
    ]
    groups.append(("DIV_CORP", "admin", USERS[7], CORPORATE))

    cost_centers: list[CostCenter] = []
    assignments: list[Tranche] = []
    counters: dict[str, int] = {}
    for parent, function, owner, members in groups:
        selected = [m for m in members if m[1] in variant.entities]
        if variant.max_cc_per_department is not None:
            keep = {MOVED_CC, NEW_CC}
            specials = [m for m in selected if m[0] in keep]
            others = [m for m in selected if m[0] not in keep]
            selected = specials + others[: max(0, variant.max_cc_per_department - len(specials))]
        if not selected:
            continue
        if parent.startswith("DEP_"):
            dep = next(d for d in DEPARTMENTS if d.code == parent)
            if parent == RENAMED_DEPARTMENT:
                end = current - ONE_DAY
                nodes.append(Tranche(parent, dep.name, dep.division, EARLY_START, end))
                nodes.append(Tranche(parent, RENAMED_TO, dep.division, current, OPEN_END))
            else:
                nodes.append(Tranche(parent, dep.name, dep.division, EARLY_START, OPEN_END))

        for copy in range(variant.scale):
            for name, entity in selected:
                full_name = name if variant.scale == 1 else f"{name} {copy + 1:02}"
                counters[entity] = counters.get(entity, 0) + 1
                code = f"{entity}{1000 + 10 * counters[entity]}"
                valid_from, valid_to = EARLY_START, OPEN_END
                if name == NEW_CC:
                    valid_from = add_months(prior, 6)
                if name == CLOSED_CC:
                    valid_to = current - ONE_DAY
                if name == NEXT_CLOSED_CC:
                    valid_to = add_months(current, 12) - ONE_DAY
                cc_owner = owner
                if name in NO_OWNER:
                    cc_owner = None
                elif name == UNMATCHED_OWNER_CC:
                    cc_owner = UNMATCHED_OWNER
                cost_centers.append(
                    CostCenter(
                        code,
                        full_name,
                        f"{full_name} ({entity})",
                        entity,
                        function,
                        cc_owner,
                        valid_from,
                        valid_to,
                    )
                )
                if name == MOVED_CC:
                    assignments.append(
                        Tranche(code, full_name, parent, valid_from, prior - ONE_DAY)
                    )
                    assignments.append(Tranche(code, full_name, MOVED_TO, prior, valid_to))
                else:
                    assignments.append(Tranche(code, full_name, parent, valid_from, valid_to))

    name, entity, parent = NEXT_NEW_CC
    if entity in variant.entities and variant.max_cc_per_department is None:
        counters[entity] = counters.get(entity, 0) + 1
        code = f"{entity}{1000 + 10 * counters[entity]}"
        start = add_months(current, 12)
        dep = next(d for d in DEPARTMENTS if d.code == parent)
        cost_centers.append(
            CostCenter(
                code, name, f"{name} ({entity})", entity, dep.function, dep.owner, start, OPEN_END
            )
        )
        assignments.append(Tranche(code, name, parent, start, OPEN_END))

    # The moved cost center's target department must exist even in the small variant.
    if any(a.parent == MOVED_TO for a in assignments) and not any(
        n.code == MOVED_TO for n in nodes
    ):
        dep = next(d for d in DEPARTMENTS if d.code == MOVED_TO)
        nodes.append(Tranche(MOVED_TO, dep.name, dep.division, EARLY_START, OPEN_END))
    return cost_centers, nodes, assignments


def _accounts(variant: Variant) -> list[Account]:
    accounts: list[Account] = []
    for group, name, _section, first, count, kind, functions, monthly in ACCOUNT_GROUPS:
        n = count
        if variant.max_accounts_per_group is not None and group not in ("SALES_REV", "CREDITS"):
            n = min(count, variant.max_accounts_per_group)
        for i in range(n):
            code = str(first + 10 * i)
            label = name if count == 1 else f"{name} {i + 1:02}"
            if group == "SALES_REV":
                line = next(k for k, v in LINE_REVENUE_ACCOUNT.items() if v == code)
                label = f"Revenue {dict((c, n) for c, n, *_ in PRODUCT_LINES)[line]}"
            if group == "CREDITS":
                label = CREDIT_ACCOUNT_NAMES[code]
            accounts.append(Account(code, label, group, kind, functions, monthly))
    if variant.max_accounts_per_group is None:
        code, label, group = NEW_ACCOUNT
        accounts.append(Account(code, label, group, "cost", (), 0))  # no functions: no actuals
    return accounts


def _products(
    variant: Variant, country_codes: list[str], rng: random.Random
) -> tuple[dict[str, tuple[str, str]], list[Product]]:
    groups: dict[str, tuple[str, str]] = {}
    products: list[Product] = []
    n_groups = variant.max_groups_per_line or GROUPS_PER_LINE
    n_skus = (variant.max_skus_per_group or SKUS_PER_GROUP) * variant.scale
    for line, line_name, (low, high), qty in PRODUCT_LINES:
        for g in range(n_groups):
            group = f"{line}{g + 1:02}"
            group_name = f"{line_name} series {line}-{g + 1}"
            groups[group] = (group_name, line)
            for s in range(n_skus):
                k = min(len(country_codes), rng.randint(3, 9))
                products.append(
                    Product(
                        code=f"{group}-{s + 1:03}",
                        name=f"{group_name}, size {s + 1}",
                        group=group,
                        line=line,
                        price_eur_cents=rng.randint(low, high) * 100,
                        countries=tuple(sorted(rng.sample(country_codes, k))),
                        monthly_qty=qty * rng.uniform(0.3, 1.7),
                    )
                )
    return groups, products


# --- Capacity planning (ADR 0013) ----------------------------------------------------------------

# code, name, credited account
ACTIVITY_TYPES = (
    ("MACHINE_HOURS", "Machine hours", "8900"),
    ("QUALITY_MEASUREMENT", "Measurement and quality control", "8910"),
    ("REPAIR_WORK", "Repair work", "8920"),
    ("ENGINEERING_HOURS", "Engineering hours", "8930"),
)

# Work centers of production cost centers, by cost center name: (name, activity type, rate in
# EUR per hour, typical hours a month). A work center may output several activity types.
WORK_CENTERS: dict[str, tuple[tuple[str, tuple[tuple[str, int, int], ...]], ...]] = {
    "Machining": (
        ("Machining centre 1", (("MACHINE_HOURS", 95, 420), ("REPAIR_WORK", 70, 20))),
        ("Machining centre 2", (("MACHINE_HOURS", 95, 400),)),
        ("Lathe", (("MACHINE_HOURS", 80, 350),)),
    ),
    "Quality Assurance": (("Coordinate measuring machine", (("QUALITY_MEASUREMENT", 120, 250),)),),
    "Assembly Line 1": (("Assembly line 1", (("MACHINE_HOURS", 60, 380),)),),
    "Assembly Line 2": (("Assembly line 2", (("MACHINE_HOURS", 60, 360),)),),
    "Test Bench": (("Test bench", (("QUALITY_MEASUREMENT", 110, 300),)),),
    "Maintenance": (("Maintenance crew", (("REPAIR_WORK", 70, 600),)),),
    "Casting": (("Casting line", (("MACHINE_HOURS", 85, 400),)),),
    "Machining PL": (("Machining centre PL", (("MACHINE_HOURS", 55, 420),)),),
    "Forging": (
        ("Forging press 1", (("MACHINE_HOURS", 140, 380),)),
        ("Forging press 2", (("MACHINE_HOURS", 140, 340),)),
    ),
    "Valve Machining": (("Valve machining centre", (("MACHINE_HOURS", 105, 400),)),),
    "Pressure Testing": (("Pressure test rig", (("QUALITY_MEASUREMENT", 125, 280),)),),
    "Valve Maintenance": (("Valve maintenance crew", (("REPAIR_WORK", 85, 500),)),),
    "Painting": (("Paint line", (("MACHINE_HOURS", 75, 330),)),),
    "Plant Logistics": (("Forklift fleet", (("MACHINE_HOURS", 45, 500),)),),
    "Tooling": (
        ("Tool grinding", (("MACHINE_HOURS", 85, 260),)),
        ("Toolmaker team", (("REPAIR_WORK", 68, 300),)),
    ),
    "Prototyping": (
        ("Prototype workshop", (("ENGINEERING_HOURS", 110, 280), ("MACHINE_HOURS", 90, 120))),
    ),
    "Hydraulic Design": (("Hydraulic design team", (("ENGINEERING_HOURS", 105, 600),)),),
    "Mechanical Design": (("Mechanical design team", (("ENGINEERING_HOURS", 100, 520),)),),
    "Assembly PL": (("Assembly line PL", (("MACHINE_HOURS", 40, 400),)),),
    "Quality PL": (("Measuring room PL", (("QUALITY_MEASUREMENT", 60, 220),)),),
    "Maintenance PL": (("Maintenance crew PL", (("REPAIR_WORK", 45, 480),)),),
    "Valve Assembly": (
        ("Valve assembly cell 1", (("MACHINE_HOURS", 80, 360),)),
        ("Valve assembly cell 2", (("MACHINE_HOURS", 80, 340),)),
    ),
    "Coating": (("Coating booth", (("MACHINE_HOURS", 95, 300),)),),
    "Valve Quality": (("Valve measuring room", (("QUALITY_MEASUREMENT", 130, 240),)),),
    "Materials Lab": (
        (
            "Materials testing lab",
            (("QUALITY_MEASUREMENT", 140, 200), ("ENGINEERING_HOURS", 125, 160)),
        ),
    ),
    "US Assembly": (("US assembly line", (("MACHINE_HOURS", 70, 380),)),),
    "US Testing": (("US test rig", (("QUALITY_MEASUREMENT", 115, 260),)),),
}


@dataclass(frozen=True)
class WorkCenter:
    code: str
    name: str
    cost_center: str
    # (activity type, rate in EUR per hour, typical hours a month)
    outputs: tuple[tuple[str, int, int], ...]


def _work_centers(cost_centers: list[CostCenter], variant: Variant) -> list[WorkCenter]:
    out: list[WorkCenter] = []
    for cc in cost_centers:
        base = cc.name if variant.scale == 1 else cc.name.rsplit(" ", 1)[0]
        for i, (name, outputs) in enumerate(WORK_CENTERS.get(base, ()), 1):
            out.append(WorkCenter(f"WC_{cc.code}_{i}", name, cc.code, outputs))
    return out
