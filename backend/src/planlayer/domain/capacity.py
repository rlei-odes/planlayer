"""Productive hours of positions (ADR 0027).

A position with a contract percentage works the hours a full-time position works in the year,
times its contract percentage and productive share, spread over the months by the line's
weights and counted for the days it is active. A position without a productive share gives no
hours.

A position without a contract percentage (e.g. a freelancer) has its year's hours typed. They
are spread over the months it is active, by the line's weights, and add up to the typed total."""

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
    typed_hours: Decimal | None = None  # without a contract %: the year's hours

    @property
    def typed(self) -> bool:
        return self.contract_percent is None


def hours(
    p: StaffPosition, fte_hours: Decimal, weights: Sequence[Decimal], periods: Sequence[date]
) -> list[Decimal] | None:
    """A position's productive hours per month, unrounded; None without a productive share or
    typed hours. ``weights`` are the line's month weights; they needn't sum to 1."""
    total = sum(weights, ZERO)
    if total <= 0:
        raise ValueError("month weights sum to zero")
    active = [active_share(period, p.entry_date, p.leave_date) for period in periods]
    if p.typed:
        if p.typed_hours is None:
            return None
        parts = [w * a for w, a in zip(weights, active, strict=True)]
        worked = sum(parts, ZERO)
        if worked == 0:
            return [ZERO] * len(periods)
        return [p.typed_hours * part / worked for part in parts]
    if p.productive_percent is None:
        return None
    assert p.contract_percent is not None
    year = fte_hours * p.contract_percent / HUNDRED * p.productive_percent / HUNDRED
    return [year * w / total * a for w, a in zip(weights, active, strict=True)]
