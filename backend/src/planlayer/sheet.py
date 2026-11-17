"""What every sheet shares: the cost center sheet (``planning``), capacity (ADR 0013) and
workforce (ADR 0020). A cell with its row version, conflicts between edits, comparison columns,
the context a cost center's sheet is read and written in, an amount as typed, and the lock
trigger's refusal (D-19, ADR 0008, ADR 0010)."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy.exc import DBAPIError

from planlayer.domain.periods import closed_months
from planlayer.domain.workflow import Status
from planlayer.rounds import RoundRef

MONTHS = 12
LOCKED_SQLSTATE = "PL001"  # raised by the lock trigger


class EditError(ValueError):
    """An edit that can't be applied as sent (bad value, unknown account, a form's rule)."""


class LockedError(Exception):
    """The cost center can't be edited in this round now."""


@dataclass
class Conflict:
    account_code: str
    month: int


class ConflictError(Exception):
    def __init__(self, conflicts: list[Conflict]) -> None:
        self.conflicts = conflicts
        super().__init__(f"{len(conflicts)} cell(s) changed by someone else")


@dataclass
class Cell:
    value: Decimal | None
    row_version: int | None


@dataclass
class ComparisonColumn:
    kind: str
    label: str
    note: str | None = None  # the controller's note to planners
    # Actuals columns: the last month holding actuals, and whether months after it are missing,
    # as for the current year's actuals in a budget prepared mid-year.
    actuals_until: date | None = None
    partial: bool = False


@dataclass
class SheetContext:
    round: RoundRef
    actuals_id: int
    cost_center_id: int
    cost_center_name: str
    entity_id: int
    status: Status
    lock_reason: str | None
    periods: list[date]
    prior_periods: list[date]

    @property
    def version_id(self) -> int:
        return self.round.version_id

    @property
    def closed(self) -> int:
        """How many months, from the year's first, are closed (ADR 0015)."""
        return closed_months(self.periods, self.round.actuals_through)

    @property
    def open_months(self) -> range:
        return range(self.closed, MONTHS)


def parse_amount(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        amount = Decimal(value)
    except InvalidOperation:
        raise EditError(f"not a number: {value!r}") from None
    if not amount.is_finite():
        raise EditError(f"not a number: {value!r}")
    exponent = amount.as_tuple().exponent
    if isinstance(exponent, int) and exponent < -2:
        raise EditError(f"{value} has more than 2 decimal places")
    if not fits(amount):
        raise EditError(f"{value} is too large")
    return amount


def fits(value: Decimal, precision: int = 18, scale: int = 2) -> bool:
    """Whether a value fits a ``numeric(precision, scale)`` column: amounts are
    ``numeric(18, 2)`` (T-12), so fewer than 16 digits before the point."""
    return abs(value) < Decimal(10) ** (precision - scale)


def locked_error(e: DBAPIError) -> LockedError | None:
    if getattr(e.orig, "sqlstate", None) == LOCKED_SQLSTATE:
        return LockedError(str(e.orig).splitlines()[0].removeprefix("plan values are locked: "))
    return None
