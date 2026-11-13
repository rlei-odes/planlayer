"""HR data for workforce planning (TD-24, ADR 0020): job families, the staff list and month-end
FTE and headcount.

Each cost center has a number of seats by its function. A seat is held by one person after the
other: a tenure of a few years, then the next person enters one to three months after the last
one left. That gives turnover in the actuals and a staff list whose people entered at different
times. People whose exit date lies within four months after the anchor have announced their leaving;
later exits are not known yet, so HR shows them as staying. Nobody enters after the anchor: HR
lists contracts signed, and plan positions are the planners'.

Names are made up from two short lists. Staff types are Planlayer's seeded codes (EMP, APP,
TMP, FRL), so the data maps without staff types of its own.
"""

import random
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, timedelta

from gen_testdata.calendar import add_months, month_end
from gen_testdata.master import CostCenter, Master

# code, name
JOB_FAMILIES = (
    ("TECH", "Production and service technicians"),
    ("ENG", "Engineers"),
    ("SALES", "Sales and service sales"),
    ("OFFICE", "Office and administration"),
    ("MGMT", "Management"),
)

# Per function: seats (low, high), and the job titles with their family and weight.
SEATS = {"production": (6, 14), "sales": (3, 7), "admin": (2, 6)}
TITLES: dict[str, tuple[tuple[str, str, int], ...]] = {
    "production": (
        ("Machine operator", "TECH", 6),
        ("Service technician", "TECH", 3),
        ("Production engineer", "ENG", 2),
        ("Shift supervisor", "MGMT", 1),
    ),
    "sales": (
        ("Sales engineer", "SALES", 4),
        ("Inside sales", "SALES", 2),
        ("Field service technician", "TECH", 2),
        ("Sales assistant", "OFFICE", 1),
    ),
    "admin": (
        ("Clerk", "OFFICE", 4),
        ("Specialist", "OFFICE", 3),
        ("Team lead", "MGMT", 1),
    ),
}
# Cost centers whose people are engineers whatever their function says.
ENGINEERING = ("Design", "Prototyping", "Materials Lab", "R&D", "Engineering")

FIRST = (
    "Anna", "Ben", "Clara", "David", "Elena", "Felix", "Greta", "Hugo", "Ida", "Jonas", "Klara",
    "Lukas", "Mia", "Noah", "Olga", "Paul", "Rosa", "Simon", "Tara", "Uwe", "Vera", "Willi",
    "Zofia", "Marek", "Ewa", "Piotr", "Emily", "Jake", "Sofia", "Luca",
)  # fmt: skip
LAST = (
    "Albers", "Brandt", "Conrad", "Dietz", "Ebert", "Falk", "Gerber", "Hahn", "Imhof", "Jansen",
    "Kowalski", "Lorenz", "Maier", "Nowak", "Otto", "Peters", "Quast", "Roth", "Seidel", "Thiel",
    "Ullrich", "Vogel", "Wolff", "Zeller", "Baker", "Carter", "Miller", "Brunner", "Keller",
)  # fmt: skip


@dataclass(frozen=True)
class Person:
    code: str
    cost_center: str
    title: str
    name: str
    staff_type: str  # EMP / APP / TMP / FRL
    family: str
    entry: date
    exit: date | None
    percent: int | None  # None: a freelancer without one

    def counts(self, period: date) -> bool:
        """Active on the month's last day (ADR 0020)."""
        end = month_end(period)
        return self.entry <= end and (self.exit is None or self.exit >= end)


def _title(rng: random.Random, cc: CostCenter) -> tuple[str, str]:
    if any(word in cc.name for word in ENGINEERING):
        return rng.choice((("Design engineer", "ENG"), ("Development engineer", "ENG"),
                           ("Technician", "TECH")))  # fmt: skip
    options = TITLES[cc.function]
    title, family, _ = rng.choices(options, weights=[w for *_, w in options])[0]
    return title, family


def _kind(rng: random.Random, cc: CostCenter, family: str) -> tuple[str, int | None]:
    """Staff type and contract percentage."""
    roll = rng.random()
    if cc.function == "production" and family == "TECH" and roll < 0.08:
        return "APP", 100
    if cc.function == "production" and family == "TECH" and roll < 0.16:
        return "TMP", 100
    if family == "ENG" and roll < 0.15:
        return "FRL", None if rng.random() < 0.6 else rng.choice((40, 60, 80))
    return "EMP", rng.choices((100, 80, 60, 50), weights=(14, 3, 2, 1))[0]


def staff(master: Master, seed: int) -> list[Person]:
    """Everyone who held a seat from before the first period of actuals to the anchor."""
    cal = master.calendar
    start, anchor = cal.first_period, cal.anchor
    known_until = add_months(anchor, 4)  # exits announced up to a notice period ahead
    people: list[Person] = []
    number = 0
    for cc in master.cost_centers:
        rng = random.Random(f"{seed}:hr:{cc.code}")
        low, high = SEATS[cc.function]
        opened = max(cc.valid_from, date(2000, 1, 1))
        closed = min(cc.valid_to, date(2100, 1, 1))
        cc_end = closed if cc.valid_to < date(9999, 1, 1) else None
        for _seat in range(rng.randint(low, high)):
            # The seat's first holder may have entered long before the actuals start.
            entry = max(opened, start - timedelta(days=rng.randint(0, 15 * 365)))
            while entry <= anchor and entry <= closed:
                tenure = timedelta(days=rng.randint(400, 9 * 365))
                exit_ = min(month_end(entry + tenure), closed)
                title, family = _title(rng, cc)
                kind, percent = _kind(rng, cc, family)
                number += 1
                # A cost center's closure is known ahead: its people leave by then at the latest.
                shown_exit = exit_ if exit_ <= known_until else cc_end
                if exit_ >= start:  # left before the actuals: not in HR's list
                    people.append(
                        Person(
                            f"P{number:05d}",
                            cc.code,
                            title,
                            f"{rng.choice(FIRST)} {rng.choice(LAST)}",
                            kind,
                            family,
                            entry,
                            shown_exit,
                            percent,
                        )
                    )
                if exit_ >= closed:
                    break
                # The next holder enters one to three months later, on the first of a month.
                entry = add_months(exit_, rng.randint(1, 3))
    return people


def staff_rows(people: list[Person]) -> Iterator[tuple[object, ...]]:
    """(personalnr, kostl, stellenbez, name, mitarbeitergruppe, jobfamilie, eintritt, austritt,
    beschaeftigungsgrad)"""
    for p in people:
        yield (p.code, p.cost_center, p.title, p.name, p.staff_type, p.family, p.entry, p.exit,
               p.percent)  # fmt: skip


def snapshot_rows(people: list[Person], periods: list[date]) -> Iterator[tuple[object, ...]]:
    """(stichtag, kostl, mitarbeitergruppe, jobfamilie, koepfe, fte): month-end headcount and FTE
    per cost center, staff type and family; FTE blank where nobody counted has a percentage."""
    by_line: dict[tuple[str, str, str], list[Person]] = defaultdict(list)
    for p in people:
        by_line[(p.cost_center, p.staff_type, p.family)].append(p)
    for period in periods:
        for (cc, kind, family), members in sorted(by_line.items()):
            active = [p for p in members if p.counts(period)]
            if not active:
                continue
            parts = [p.percent for p in active if p.percent is not None]
            fte = f"{sum(parts) / 100:.2f}" if parts else None
            yield (period, cc, kind, family, len(active), fte)
