"""Fiscal periods (T-18): months, one fiscal year definition per installation.

A fiscal year is named after the calendar year in which it starts: with an April start, fiscal
year 2027 runs from April 2027 to March 2028. With the default January start, fiscal and
calendar years coincide.
"""

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


def fiscal_year_of(period: date, start_month: int) -> int:
    return period.year if period.month >= start_month else period.year - 1
