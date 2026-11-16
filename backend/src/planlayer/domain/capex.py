"""Capex planning's rules (ADR 0028): the periods cash out is scheduled in, an entry capped at
what is left of the investment, a schedule trimmed to a lower amount, and when an investment is
better planned as a large project. Pure functions, no I/O."""

from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal
from typing import Literal

Status = Literal["open", "confirmed", "declined"]
STATUSES: tuple[Status, ...] = ("open", "confirmed", "declined")
PRIORITIES = (1, 2, 3)
ZERO = Decimal(0)


class CapexError(ValueError):
    """A capex change that can't be made as asked; nothing was changed."""


def cash_out_periods(periods: Sequence[date], grain: Literal["month", "quarter"]) -> list[date]:
    """The periods of a round's fiscal year that cash out is scheduled in: its months, or its
    quarters' first months in a round planned by quarter."""
    return list(periods) if grain == "month" else list(periods[::3])


def rest(amount: Decimal | None, schedule: Mapping[date, Decimal], period: date) -> Decimal:
    """What is left of the investment for ``period``: its amount less the other periods'
    cash out."""
    others = sum((v for p, v in schedule.items() if p != period), ZERO)
    return max(ZERO, (amount or ZERO) - others)


def capped(amount: Decimal | None, schedule: Mapping[date, Decimal], period: date,
           value: Decimal) -> Decimal:  # fmt: skip
    """An entry for ``period``, reduced to the rest when it is more, so the schedule never
    exceeds the investment."""
    if value < 0:
        raise CapexError("cash out is not negative")
    return min(value, rest(amount, schedule, period))


def trimmed(schedule: Mapping[date, Decimal], amount: Decimal | None) -> dict[date, Decimal]:
    """The schedule for a lower amount: cut from the latest period backwards until it fits.
    Periods left with nothing drop out."""
    left = amount or ZERO
    out: dict[date, Decimal] = {}
    for period in sorted(schedule):
        take = min(schedule[period], left)
        if take > 0:
            out[period] = take
        left -= take
    return out


def check_schedule(
    amount: Decimal | None, schedule: Mapping[date, Decimal], allowed: Sequence[date]
) -> None:
    """A schedule as saved: in the allowed periods, positive, cents, at most the amount."""
    for period, value in schedule.items():
        if period not in allowed:
            raise CapexError(f"{period:%Y-%m} is not a period of the cash-out schedule")
        if value <= 0:
            raise CapexError("cash out is more than 0; leave a period empty instead")
        if value != value.quantize(Decimal("0.01")):
            raise CapexError(f"{value} has more than 2 decimal places")
    total = sum(schedule.values(), ZERO)
    if total > (amount or ZERO):
        raise CapexError(f"cash out of {total} is more than the investment's {amount or ZERO}")


def above_threshold(amount: Decimal | None, threshold: Decimal | None) -> bool:
    """A single investment above the round's threshold is better planned as a large project,
    broken down into lines with their own dates."""
    return threshold is not None and amount is not None and amount > threshold
