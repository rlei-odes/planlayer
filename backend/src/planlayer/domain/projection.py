"""Projecting a year from its booked months: the comparison column for planners without a
forecast, e.g. nine months of actuals extrapolated to twelve.

Flows (amounts, hours) continue at their monthly average, so the year is the booked months
times twelve over their number, to the cent. Stocks (FTE, headcount) carry the last booked
month forward: a headcount averaged over the year to date would undercount a growing team.
A blank booked month counts as zero in the average; a line with no booked month stays blank.
"""

from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from planlayer.domain.spreading import equal, spread

Carry = Literal["average", "last"]


def project(
    months: Sequence[Decimal | None], booked: int, carry: Carry = "average", places: int = 2
) -> list[Decimal | None]:
    """The first ``booked`` months as they are, the rest projected from them."""
    if not 0 <= booked <= len(months):
        raise ValueError(f"{booked} booked months of {len(months)}")
    head = list(months[:booked])
    rest = len(months) - booked
    known = [v for v in head if v is not None]
    if not known:
        return [None] * len(months)
    if rest == 0:
        return head
    tail: list[Decimal | None]
    if carry == "last":
        tail = [head[-1]] * rest  # a blank last month: nobody left, so blank onwards
    else:
        remaining = (sum(known, Decimal(0)) * rest / booked).quantize(
            Decimal(1).scaleb(-places), ROUND_HALF_UP
        )
        tail = list(spread(remaining, equal(rest), places))
    return head + tail
