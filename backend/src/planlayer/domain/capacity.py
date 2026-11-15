"""Productive hours of positions (ADR 0027): the hours a full-time position works in the year,
times the position's contract percentage and productive share, spread over the months by the
line's weights and counted for the days the position is active.

A position without a contract percentage (an external one) counts as full time. A position
without a productive share gives no hours."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from planlayer.domain.personnel import active_share

HUNDRED = Decimal(100)
ZERO = Decimal(0)


@dataclass(frozen=True)
class StaffPosition:
    contract_percent: Decimal | None
    productive_percent: Decimal | None
    entry_date: date | None
    leave_date: date | None

    def year_hours(self, fte_hours: Decimal) -> Decimal | None:
        """The hours a year at full activity; None without a productive share."""
        if self.productive_percent is None:
            return None
        contract = HUNDRED if self.contract_percent is None else self.contract_percent
        return fte_hours * contract / HUNDRED * self.productive_percent / HUNDRED


def hours(
    p: StaffPosition, fte_hours: Decimal, weights: Sequence[Decimal], periods: Sequence[date]
) -> list[Decimal] | None:
    """A position's productive hours per month, unrounded; None without a productive share.
    ``weights`` are the line's month weights; they needn't sum to 1."""
    year = p.year_hours(fte_hours)
    if year is None:
        return None
    total = sum(weights, ZERO)
    if total <= 0:
        raise ValueError("month weights sum to zero")
    return [
        year * w / total * active_share(period, p.entry_date, p.leave_date)
        for w, period in zip(weights, periods, strict=True)
    ]
