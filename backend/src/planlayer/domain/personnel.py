"""Personnel cost rules (ADR 0022): a position's costs per account and month.

Base pay is the monthly salary times the share of the month's days the position is active.
Salaried positions add yearly extras (``salaries`` per year, spread over the months), raises from
a month (compounding, on base pay and extras), long-service awards in the anniversary month, the
yearly bonus spread over the months worked, and contributions on the month's gross pay up to a
ceiling. A typed position's monthly amount is all there is.
"""

from calendar import monthrange
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

ZERO = Decimal(0)
ONE = Decimal(1)
TWELVE = Decimal(12)
HUNDRED = Decimal(100)


@dataclass(frozen=True)
class PayPosition:
    entity: int
    salary_account: int
    typed: bool
    salary: Decimal  # monthly: the planned salary, else HR's, else 0
    entry_date: date | None = None  # None: before the year
    leave_date: date | None = None  # None: stays
    bonus: Decimal | None = None  # yearly


@dataclass(frozen=True)
class Extra:
    account: int
    salaries: Decimal  # per year: 1 for a 13th salary; percent of base pay x 12 / 100
    entity: int | None = None


@dataclass(frozen=True)
class Raise:
    account: int
    percent: Decimal
    starts: date  # the first month it applies to
    entity: int | None = None


@dataclass(frozen=True)
class Step:
    years: int
    salaries: Decimal | None = None
    amount: Decimal | None = None


@dataclass(frozen=True)
class Award:
    account: int
    steps: tuple[Step, ...]
    entity: int | None = None


@dataclass(frozen=True)
class Contribution:
    account: int
    percent: Decimal
    ceiling: Decimal | None = None  # monthly
    entity: int | None = None


@dataclass(frozen=True)
class Rules:
    extras: tuple[Extra, ...] = ()
    raises: tuple[Raise, ...] = ()
    awards: tuple[Award, ...] = ()
    contributions: tuple[Contribution, ...] = ()
    bonus_account: int | None = None


def percent_as_salaries(percent: Decimal) -> Decimal:
    """A yearly extra typed as a percentage of base pay: 10.2 % is 1.224 salaries."""
    return percent * TWELVE / HUNDRED


def salaries_as_percent(salaries: Decimal) -> Decimal:
    return salaries * HUNDRED / TWELVE


def active_share(period: date, entry: date | None, leave: date | None) -> Decimal:
    """The share of the month's days between entry and leave, both included."""
    days = monthrange(period.year, period.month)[1]
    first, last = 1, days
    if entry is not None:
        if (entry.year, entry.month) > (period.year, period.month):
            return ZERO
        if (entry.year, entry.month) == (period.year, period.month):
            first = entry.day
    if leave is not None:
        if (leave.year, leave.month) < (period.year, period.month):
            return ZERO
        if (leave.year, leave.month) == (period.year, period.month):
            last = leave.day
    if last < first:
        return ZERO
    if first == 1 and last == days:
        return ONE
    return Decimal(last - first + 1) / Decimal(days)


def _applies(entity: int | None, p: PayPosition) -> bool:
    return entity is None or entity == p.entity


Costs = dict[int, list[Decimal]]  # account: months


@dataclass
class _Months:
    n: int
    by_account: Costs = field(default_factory=dict)

    def add(self, account: int, m: int, value: Decimal) -> None:
        if value:
            self.by_account.setdefault(account, [ZERO] * self.n)[m] += value


def costs(p: PayPosition, rules: Rules, periods: Sequence[date]) -> Costs:
    """The position's costs per account over ``periods`` (the fiscal year's months), unrounded;
    accounts with nothing are left out."""
    out = _Months(len(periods))
    shares = [active_share(period, p.entry_date, p.leave_date) for period in periods]
    if p.typed:
        for m, share in enumerate(shares):
            out.add(p.salary_account, m, p.salary * share)
        return out.by_account

    raises = sorted((r for r in rules.raises if _applies(r.entity, p)), key=lambda r: r.starts)
    extras = [e for e in rules.extras if _applies(e.entity, p)]
    awards = [a for a in rules.awards if _applies(a.entity, p)]
    contributions = [c for c in rules.contributions if _applies(c.entity, p)]
    worked = sum(shares, ZERO)
    with_extras = ONE + sum((e.salaries for e in extras), ZERO) / TWELVE

    for m, (period, share) in enumerate(zip(periods, shares, strict=True)):
        if not share:
            continue
        base = p.salary * share
        gross = ZERO
        out.add(p.salary_account, m, base)
        for e in extras:
            out.add(e.account, m, base * e.salaries / TWELVE)
        gross += base * with_extras
        # Raises compound in the order they start; each adds its step on base pay and extras.
        factor = ONE
        for r in raises:
            if r.starts <= period:
                step = base * with_extras * factor * r.percent / HUNDRED
                out.add(r.account, m, step)
                gross += step
                factor *= ONE + r.percent / HUNDRED
        if p.entry_date is not None and p.entry_date.month == period.month:
            years = period.year - p.entry_date.year
            for a in awards:
                for s in a.steps:
                    if s.years == years:
                        paid = s.amount if s.amount is not None else ZERO
                        if s.salaries is not None:
                            paid = s.salaries * p.salary * factor
                        out.add(a.account, m, paid)
                        gross += paid
        if p.bonus and rules.bonus_account is not None and worked:
            part = p.bonus * share / worked
            out.add(rules.bonus_account, m, part)
            gross += part
        for c in contributions:
            assessed = gross if c.ceiling is None else min(gross, c.ceiling * share)
            out.add(c.account, m, assessed * c.percent / HUNDRED)
    return out.by_account


def group_counts(per_position: Sequence[Costs]) -> dict[int, int]:
    """How many positions have costs on each account: a group's size (ADR 0022)."""
    counts: dict[int, int] = defaultdict(int)
    for c in per_position:
        for account, months in c.items():
            if any(months):
                counts[account] += 1
    return dict(counts)
