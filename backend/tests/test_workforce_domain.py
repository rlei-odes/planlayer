"""Workforce planning rules (ADR 0020): month-end snapshot, roll-up and build-up."""

from datetime import date
from decimal import Decimal

import pytest

from planlayer.domain.periods import fiscal_year_periods
from planlayer.domain.workforce import (
    Position,
    PositionError,
    build_up,
    check,
    level,
    measures,
    rollup,
)

YEAR = fiscal_year_periods(2027, 1)
EMP, FRL, TECH, OFFICE = 1, 2, 10, 11


def pos(
    entry: date | None = None,
    leave: date | None = None,
    percent: str | None = "100",
    *,
    kind: int = EMP,
    category: int = TECH,
    plan: bool = False,
    replacement: bool = False,
) -> Position:
    return Position(
        kind,
        category,
        entry,
        leave,
        None if percent is None else Decimal(percent),
        plan,
        replacement,
    )


def test_a_position_counts_when_active_on_the_months_last_day() -> None:
    march = date(2027, 3, 1)
    assert pos(date(2027, 3, 15)).counts(march)  # entered during March
    assert not pos(date(2027, 4, 1)).counts(march)
    assert pos(leave=date(2027, 3, 31)).counts(march)  # leaves on the last day: still counts
    assert not pos(leave=date(2027, 3, 15)).counts(march)
    assert pos().counts(march)  # no dates: before the year, and stays


def test_rollup_counts_heads_and_sums_contract_percentages_per_line() -> None:
    lines = rollup(
        [
            pos(),
            pos(percent="50", leave=date(2027, 6, 30)),
            pos(date(2027, 9, 1), percent="80", plan=True),
            pos(category=OFFICE, percent="60"),
        ],
        YEAR,
    )
    tech = lines[(EMP, TECH)]
    assert tech["headcount"][:6] == [Decimal(2)] * 6
    assert tech["headcount"][6:8] == [Decimal(1)] * 2
    assert tech["headcount"][8:] == [Decimal(2)] * 4
    assert tech["fte"][0] == Decimal("1.5")
    assert tech["fte"][6] == Decimal(1)
    assert tech["fte"][11] == Decimal("1.8")
    assert lines[(EMP, OFFICE)]["fte"] == [Decimal("0.6")] * 12
    assert level(tech["headcount"]) == Decimal(2)  # the year is December


def test_a_line_whose_people_left_shows_zero_not_blank() -> None:
    line = rollup([pos(leave=date(2027, 4, 30))], YEAR)[(EMP, TECH)]
    assert line["headcount"][4:] == [Decimal(0)] * 8
    assert line["fte"][4:] == [Decimal(0)] * 8


def test_freelancers_without_contract_percentage_count_in_headcount_only() -> None:
    line = rollup([pos(percent=None, kind=FRL), pos(percent=None, kind=FRL)], YEAR)[(FRL, TECH)]
    assert line["headcount"] == [Decimal(2)] * 12
    assert line["fte"] == [None] * 12
    mixed = rollup([pos(percent=None, kind=FRL), pos(percent="50", kind=FRL)], YEAR)[(FRL, TECH)]
    assert mixed["fte"] == [Decimal("0.5")] * 12


def test_build_up_ties_month_to_month_headcount() -> None:
    positions = [
        pos(),
        pos(leave=date(2027, 3, 15)),  # last counted in February
        pos(date(2027, 5, 1), plan=True),  # new
        pos(date(2027, 4, 1), plan=True, replacement=True),
        pos(date(2027, 7, 1)),  # a hire already signed, not a plan position
        pos(date(2026, 10, 1)),  # entered before the year
    ]
    b = build_up(positions, YEAR)
    assert (b.start, b.end) == (3, 5)  # the year's start: the leaver, one without dates, Oct 2026
    assert b.start + sum(b.entering) - sum(b.leaving) == b.end
    assert b.leaving[2] == 1  # gone from March's month-end headcount
    assert b.new[4] == 1 and b.replacements[3] == 1
    assert b.entering == [0, 0, 0, 1, 1, 0, 1, 0, 0, 0, 0, 0]
    heads = rollup(positions, YEAR)[(EMP, TECH)]["headcount"]
    for m in range(1, 12):
        assert heads[m] - heads[m - 1] == b.entering[m] - b.leaving[m]
    assert heads[0] == b.start + b.entering[0] - b.leaving[0]
    # Someone who leaves in January before its end counts at the start and leaves in January.
    early = build_up([pos(leave=date(2027, 1, 15))], YEAR)
    assert (early.start, early.leaving[0], early.end) == (1, 1, 0)


def test_check_rules() -> None:
    check(pos(percent=None, kind=FRL), external=True)
    with pytest.raises(PositionError, match="contract percentage"):
        check(pos(percent=None), external=False)
    with pytest.raises(PositionError, match="at most 100"):
        check(pos(percent="120"), external=False)
    with pytest.raises(PositionError, match="before the entry"):
        check(pos(date(2027, 5, 1), date(2027, 4, 1)), external=False)
    with pytest.raises(PositionError, match="plan position"):
        check(pos(replacement=True), external=False)


def test_measures_per_mode() -> None:
    assert measures("fte") == ("fte",)
    assert measures("headcount") == ("headcount",)
    assert measures("both") == measures("positions") == ("fte", "headcount")
