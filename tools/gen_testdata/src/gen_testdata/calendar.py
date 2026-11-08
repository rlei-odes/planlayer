"""Months covered by the actuals, in fixed or relative date mode (TD-11)."""

from dataclasses import dataclass
from datetime import date, timedelta

# Fixed mode: actuals end with June 2026, so golden results never move.
FIXED_ANCHOR = date(2026, 6, 30)


def add_months(d: date, months: int) -> date:
    index = d.year * 12 + d.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def month_end(d: date) -> date:
    return add_months(d, 1) - timedelta(days=1)


def fiscal_year_start(d: date, start_month: int) -> date:
    year = d.year if d.month >= start_month else d.year - 1
    return date(year, start_month, 1)


def relative_anchor(today: date) -> date:
    """Last day of the month before ``today``: the demo always has actuals up to last month."""
    return date(today.year, today.month, 1) - timedelta(days=1)


@dataclass(frozen=True)
class Calendar:
    """``full_years`` complete fiscal years, then the current fiscal year up to ``anchor``."""

    anchor: date
    fiscal_start_month: int
    full_years: int

    @property
    def current_fy_start(self) -> date:
        return fiscal_year_start(self.anchor, self.fiscal_start_month)

    @property
    def prior_fy_start(self) -> date:
        return add_months(self.current_fy_start, -12)

    @property
    def first_period(self) -> date:
        return add_months(self.current_fy_start, -12 * self.full_years)

    @property
    def periods(self) -> list[date]:
        """First day of every month with actuals."""
        last = date(self.anchor.year, self.anchor.month, 1)
        months = (last.year - self.first_period.year) * 12 + last.month - self.first_period.month
        return [add_months(self.first_period, i) for i in range(months + 1)]
