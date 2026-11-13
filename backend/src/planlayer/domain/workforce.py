"""Workforce planning rules (ADR 0020): FTE and headcount are levels at month end.

A year or a quarter shows the value of its last month; a typed year or quarter value fills its
months. Positions count in a month when they are active on its last day, and roll up per staff
type x category into headcount and FTE.
"""

from calendar import monthrange
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal

Mode = Literal["fte", "headcount", "both", "positions"]
MODES: tuple[Mode, ...] = ("fte", "headcount", "both", "positions")
Measure = Literal["fte", "headcount"]
HUNDRED = Decimal(100)


def measures(mode: Mode) -> tuple[Measure, ...]:
    """The measures a round's sheets show: positions give both."""
    match mode:
        case "fte":
            return ("fte",)
        case "headcount":
            return ("headcount",)
        case _:
            return ("fte", "headcount")


def level(months: Sequence[Decimal | None]) -> Decimal | None:
    """The value of a year or a quarter: its last month's."""
    return months[-1] if months else None


def month_end(period: date) -> date:
    return period.replace(day=monthrange(period.year, period.month)[1])


@dataclass(frozen=True)
class Position:
    staff_type: int
    staff_category: int
    entry_date: date | None
    leave_date: date | None
    contract_percent: Decimal | None
    is_plan: bool = False
    is_replacement: bool = False

    @property
    def line(self) -> tuple[int, int]:
        return (self.staff_type, self.staff_category)

    def counts(self, period: date) -> bool:
        """Active on the month's last day: entered by then, and not left before it."""
        end = month_end(period)
        return (self.entry_date is None or self.entry_date <= end) and (
            self.leave_date is None or self.leave_date >= end
        )

    @property
    def fte(self) -> Decimal | None:
        return None if self.contract_percent is None else self.contract_percent / HUNDRED


class PositionError(ValueError):
    pass


def check(p: Position, external: bool) -> None:
    """A position as ADR 0020 allows it; the store checks the rest (dates, flags, range)."""
    if p.contract_percent is None and not external:
        raise PositionError("employees need a contract percentage")
    if p.contract_percent is not None and not 0 < p.contract_percent <= HUNDRED:
        raise PositionError("a contract percentage is above 0 and at most 100")
    if p.entry_date and p.leave_date and p.leave_date < p.entry_date:
        raise PositionError("the leave date is before the entry date")
    if p.is_replacement and not p.is_plan:
        raise PositionError("only a plan position replaces someone")


Months = list[Decimal | None]


def rollup(
    positions: Iterable[Position], periods: Sequence[date]
) -> dict[tuple[int, int], dict[Measure, Months]]:
    """Headcount and FTE per line and month. A line with positions has a headcount in every
    month (0 once everyone has left); its FTE is blank in a month where none of the positions
    counted has a contract percentage."""
    by_line: dict[tuple[int, int], list[Position]] = defaultdict(list)
    for p in positions:
        by_line[p.line].append(p)
    out: dict[tuple[int, int], dict[Measure, Months]] = {}
    for line, members in by_line.items():
        heads: Months = []
        ftes: Months = []
        for period in periods:
            active = [p for p in members if p.counts(period)]
            heads.append(Decimal(len(active)))
            parts = [p.fte for p in active if p.fte is not None]
            ftes.append(sum(parts, Decimal(0)) if parts or not active else None)
        out[line] = {"headcount": heads, "fte": ftes}
    return out


@dataclass
class BuildUp:
    """Who comes and who goes in each month, as the month-end headcount shows it: headcount
    changes from one month to the next by ``entering`` minus ``leaving``."""

    entering: list[int]  # positions counted for the first time
    new: list[int]  # of them: plan positions that aren't replacements
    replacements: list[int]  # of them: plan positions replacing a leaver
    leaving: list[int]  # positions no longer counted, after the month before


def build_up(positions: Iterable[Position], periods: Sequence[date]) -> BuildUp:
    """A position entering in the year enters in the first month it counts in; one leaving in
    the year leaves in the first month it no longer counts in. A position counted from before
    the year enters in no month, and one counted to the year's end leaves in none."""
    n = len(periods)
    out = BuildUp([0] * n, [0] * n, [0] * n, [0] * n)
    before = periods[0].replace(day=1)
    for p in positions:
        active = [p.counts(period) for period in periods]
        if not any(active):
            continue
        first = active.index(True)
        last = n - 1 - active[::-1].index(True)
        if first > 0 or (p.entry_date is not None and p.entry_date >= before):
            out.entering[first] += 1
            if p.is_plan:
                (out.replacements if p.is_replacement else out.new)[first] += 1
        if last < n - 1:
            out.leaving[last + 1] += 1
    return out
