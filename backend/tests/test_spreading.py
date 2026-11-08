"""Spreading (D-28) against hand-checked results (TD-14 subset) and exactness properties."""

import random
from decimal import Decimal as D

import pytest

from planlayer.domain.spreading import (
    Profile,
    SpreadError,
    equal,
    from_reference,
    spread,
    spread_cells,
)


def ds(*values: str) -> list[D]:
    return [D(v) for v in values]


# --- Hand-checked results ------------------------------------------------------------------------


def test_equal_remainder_goes_to_first_cell() -> None:
    assert spread(D("100.00"), equal(3)) == ds("33.34", "33.33", "33.33")


def test_one_cent_over_three_cells() -> None:
    assert spread(D("0.01"), equal(3)) == ds("0.01", "0.00", "0.00")


def test_negative_total_mirrors_positive() -> None:
    assert spread(D("-100.00"), equal(3)) == ds("-33.34", "-33.33", "-33.33")


def test_zero_total() -> None:
    assert spread(D("0"), equal(4)) == ds("0", "0", "0", "0")


def test_relative_numbers() -> None:
    assert spread(D("1000.00"), [3, 3, 3]) == ds("333.34", "333.33", "333.33")


def test_largest_remainder_not_first_cell() -> None:
    # Exact shares 16.666.., 33.333.., 50.00: the 1 cent left goes to 16.66 (remainder .666).
    assert spread(D("100.00"), [1, 2, 3]) == ds("16.67", "33.33", "50.00")


def test_shutdown_month_stays_zero() -> None:
    weights = [1, 1, 1, 1, 1, 1, 1, 0, 1, 1, 1, 2]
    result = spread(D("1200.00"), weights)
    assert result[7] == 0
    assert result[11] == D("200.00")
    assert result[0] == D("100.00")


def test_percentage_profile() -> None:
    profile = Profile.percentages([8, 8, 8, 8, 8, 8, 8, 4, 8, 8, 8, 16])
    result = spread(D("1000.00"), profile.weights())
    assert result == ds(*["80.00"] * 7, "40.00", *["80.00"] * 3, "160.00")


def test_profile_partial_year_is_renormalised() -> None:
    # Apr-Dec of a profile with a December peak: 9 months, weights 1 x 8 and 2 -> 10 units.
    profile = Profile.relative([1] * 11 + [2])
    result = spread(D("1000.00"), profile.weights(range(4, 13)))
    assert result == ds(*["100.00"] * 8, "200.00")
    assert sum(result) == D("1000.00")


def test_reference_with_gaps_and_negative_month() -> None:
    weights = from_reference([D(100), None, D(-20), D(100)], negative="zero")
    assert spread(D("50.00"), weights) == ds("25.00", "0.00", "0.00", "25.00")


def test_reference_negative_is_an_error_by_default() -> None:
    with pytest.raises(SpreadError, match="negative"):
        from_reference([D(100), D(-1)])


def test_all_zero_reference() -> None:
    with pytest.raises(SpreadError, match="sums to zero"):
        from_reference([D(0), None, D(0)])
    assert spread(D("90.00"), from_reference([D(0), None, D(0)], if_zero="equal")) == ds(
        "30.00", "30.00", "30.00"
    )


def test_node_to_leaves_and_months_at_once() -> None:
    # A cost center that didn't exist last year has no reference and receives nothing.
    reference = {
        ("CC1", 1): D(100), ("CC1", 2): D(100),
        ("CC2", 1): D(50), ("CC2", 2): D(50),
        ("NEW", 1): D(0), ("NEW", 2): D(0),
    }  # fmt: skip
    result = spread_cells(D("1000.00"), reference)
    # Exact: 333.333.. and 166.666..; the two cents left go to the larger remainders (.667).
    assert result[("CC1", 1)] == result[("CC1", 2)] == D("333.33")
    assert result[("CC2", 1)] == result[("CC2", 2)] == D("166.67")
    assert result[("NEW", 1)] == result[("NEW", 2)] == 0
    assert sum(result.values()) == D("1000.00")


# --- Validation ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "values",
    [
        [D("8.325")] * 12,  # 99.9
        [D("8.3425")] * 12,  # 100.11
        [10] * 11,  # 11 values
        [-1] + [10] * 11,
        [0] * 12,
    ],
)
def test_invalid_percentage_profiles(values: list[D]) -> None:
    with pytest.raises(SpreadError):
        Profile.percentages(values)


def test_relative_profile_needs_no_sum() -> None:
    assert Profile.relative([7, 8, 9, 10, 11, 12, 13, 0, 9, 10, 11, 20]).shares()[7] == 0


def test_total_with_too_many_decimals() -> None:
    with pytest.raises(SpreadError, match="decimal places"):
        spread(D("10.005"), equal(2))


@pytest.mark.parametrize("weights", [[], [1, -1], [0, 0]])
def test_invalid_weights(weights: list[int]) -> None:
    with pytest.raises(SpreadError):
        spread(D("10.00"), weights)


# --- Properties ----------------------------------------------------------------------------------


def test_exact_tie_and_within_one_cent() -> None:
    rng = random.Random(7)
    for _ in range(2000):
        n = rng.randint(1, 40)
        weights = [D(rng.randint(0, 10_000)) / 100 for _ in range(n)]
        if sum(weights) == 0:
            continue
        total = D(rng.randint(-(10**9), 10**9)) / 100
        result = spread(total, weights)
        assert sum(result) == total
        exact = [total * w / sum(weights) for w in weights]
        assert all(abs(r - e) < D("0.01") for r, e in zip(result, exact, strict=True))
        assert all(r == 0 for r, w in zip(result, weights, strict=True) if w == 0)
