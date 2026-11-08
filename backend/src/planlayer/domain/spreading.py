"""Spreading a total over cells (D-28), tying exactly to the total.

One rule serves both axes: over time (year -> months) and down a hierarchy (node -> leaves),
or both at once (node x year -> leaf x month cells). The total is split in proportion to
weights; amounts are rounded to whole cents by the largest-remainder method, so the parts
always sum to the total exactly. Cells with weight 0 always receive exactly 0.

Ties between equal remainders go to the earlier cell, so results are deterministic.
"""

from collections.abc import Hashable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from math import lcm
from typing import Literal

Weight = Decimal | int


class SpreadError(ValueError):
    """The spread can't be computed as asked (invalid weights, profile or total)."""


def spread(total: Decimal, weights: Sequence[Weight], places: int = 2) -> list[Decimal]:
    """Split ``total`` in proportion to ``weights``; the result sums exactly to ``total``."""
    if not weights:
        raise SpreadError("nothing to spread over")
    if any(w < 0 for w in weights):
        raise SpreadError("weights must not be negative")
    weight_sum = sum(Fraction(w) for w in weights)
    if weight_sum == 0:
        raise SpreadError("weights sum to zero")
    units = Fraction(total) * 10**places
    if units.denominator != 1:
        raise SpreadError(f"total {total} has more than {places} decimal places")

    sign = -1 if units < 0 else 1
    n = abs(int(units))
    # Integer arithmetic throughout: weights scaled to integers by their common denominator.
    scale = weight_sum.denominator
    for w in weights:
        scale = lcm(scale, Fraction(w).denominator)
    ints = [int(Fraction(w) * scale) for w in weights]
    total_weight = sum(ints)
    parts = [n * w // total_weight for w in ints]
    remainders = [n * w % total_weight for w in ints]
    # Hand the remaining units to the largest remainders, earlier cells first on ties.
    order = sorted(range(len(ints)), key=lambda i: (-remainders[i], i))
    for i in order[: n - sum(parts)]:
        parts[i] += 1
    return [Decimal(sign * p).scaleb(-places) for p in parts]


def spread_cells[K: Hashable](
    total: Decimal, weights: Mapping[K, Weight], places: int = 2
) -> dict[K, Decimal]:
    """``spread`` over keyed cells, e.g. (cost center, month); order of ``weights`` breaks ties."""
    keys = list(weights)
    return dict(zip(keys, spread(total, [weights[k] for k in keys], places), strict=True))


def equal(n: int) -> list[int]:
    """Weights for an equal spread over ``n`` cells."""
    if n <= 0:
        raise SpreadError("nothing to spread over")
    return [1] * n


def from_reference(
    reference: Sequence[Decimal | None],
    negative: Literal["error", "zero"] = "error",
    if_zero: Literal["error", "equal"] = "error",
) -> list[Decimal]:
    """Weights from reference values (prior-year actuals, another version, current values).

    Missing values (None) count as 0. ``negative`` decides whether a negative reference value is
    an error or counts as 0. ``if_zero`` decides what happens when the reference sums to zero:
    an error, or an equal spread.
    """
    weights = [Decimal(0) if v is None else v for v in reference]
    if any(w < 0 for w in weights):
        if negative == "error":
            raise SpreadError("the reference has negative values")
        weights = [max(w, Decimal(0)) for w in weights]
    if sum(weights) == 0:
        if if_zero == "error":
            raise SpreadError("the reference sums to zero")
        return [Decimal(1)] * len(weights)
    return weights


@dataclass(frozen=True)
class Profile:
    """A seasonality profile: one value per fiscal month, as entered (TD-17).

    Entered either as percentages, which must sum to exactly 100, or as any relative numbers
    (e.g. units per month), which are used as weights directly. Zero months are allowed (a
    shutdown); negative values and an all-zero profile are not.
    """

    values: tuple[Decimal, ...]
    entered_as: Literal["percent", "relative"]

    MONTHS = 12

    def __post_init__(self) -> None:
        if len(self.values) != self.MONTHS:
            raise SpreadError(f"a profile needs {self.MONTHS} values, got {len(self.values)}")
        if any(v < 0 for v in self.values):
            raise SpreadError("profile values must not be negative")
        if sum(self.values) == 0:
            raise SpreadError("a profile can't be all zero")
        if self.entered_as == "percent" and sum(self.values) != 100:
            raise SpreadError(f"percentages sum to {sum(self.values)}, not 100")

    @classmethod
    def percentages(cls, values: Sequence[Decimal | int]) -> "Profile":
        return cls(tuple(Decimal(v) for v in values), "percent")

    @classmethod
    def relative(cls, values: Sequence[Decimal | int]) -> "Profile":
        return cls(tuple(Decimal(v) for v in values), "relative")

    def shares(self) -> list[Fraction]:
        """Exact share of each month; sums to 1."""
        total = sum(Fraction(v) for v in self.values)
        return [Fraction(v) / total for v in self.values]

    def weights(self, months: Sequence[int] | None = None) -> list[Decimal]:
        """Weights for the given fiscal months (1-based), or all 12.

        A partial range (e.g. months 4-12) is re-normalised by ``spread``, because only the
        selected months' weights take part.
        """
        if months is None:
            return list(self.values)
        if any(not 1 <= m <= self.MONTHS for m in months):
            raise SpreadError("fiscal months run from 1 to 12")
        return [self.values[m - 1] for m in months]
