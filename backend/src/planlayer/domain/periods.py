"""Fiscal periods (T-18): months, one fiscal year definition per installation.

A fiscal year is named after the calendar year in which it starts: with an April start, fiscal
year 2027 runs from April 2027 to March 2028. With the default January start, fiscal and
calendar years coincide.
"""

from calendar import monthrange
from datetime import date


def fiscal_year_periods(year: int, start_month: int) -> list[date]:
    """First day of each of the fiscal year's twelve months."""
    if not 1 <= start_month <= 12:
        raise ValueError("start month must be 1-12")
    periods = []
    for i in range(12):
        index = year * 12 + start_month - 1 + i
        periods.append(date(index // 12, index % 12 + 1, 1))
    return periods


QUARTERS = 4


def quarter_months(quarter: int) -> range:
    """The fiscal months (0-based) of a quarter (0-based): Q1 is the year's first three."""
    if not 0 <= quarter < QUARTERS:
        raise ValueError("quarters run from 0 to 3")
    return range(3 * quarter, 3 * quarter + 3)


def closed_months(periods: list[date], actuals_through: date | None) -> int:
    """How many of the fiscal year's months are closed by ``actuals_through`` (ADR 0015)."""
    if actuals_through is None:
        return 0
    return sum(1 for p in periods if p <= actuals_through)


def fiscal_year_of(period: date, start_month: int) -> int:
    return period.year if period.month >= start_month else period.year - 1


def calendar_days(year: int, start_month: int) -> list[int]:
    """Number of days in each of the fiscal year's twelve months."""
    return [monthrange(p.year, p.month)[1] for p in fiscal_year_periods(year, start_month)]


def working_days(year: int, start_month: int) -> list[int]:
    """Monday to Friday in each of the fiscal year's twelve months; public holidays are not
    known here, so a controller deducts them by hand."""
    result = []
    for p in fiscal_year_periods(year, start_month):
        days = monthrange(p.year, p.month)[1]
        result.append(sum(1 for d in range(1, days + 1) if date(p.year, p.month, d).weekday() < 5))
    return result
