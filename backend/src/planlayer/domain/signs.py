"""Published signs (ADR 0019, T-7): plan values are stored natural-positive and published also
with the sign of a convention, derived from each account's role. Pure: no I/O.

- ``ledger``: debit positive, as the ERP books: costs +, revenue and credits -.
- ``result``: profit positive, as P&L reports read: revenue and credits +, costs -.
"""

from decimal import Decimal
from typing import Literal

Role = Literal["cost", "revenue", "credit"]
Convention = Literal["ledger", "result"]

CONVENTIONS: tuple[Convention, ...] = ("ledger", "result")
SIGN: dict[Convention, dict[Role, int]] = {
    "ledger": {"cost": 1, "revenue": -1, "credit": -1},
    "result": {"cost": -1, "revenue": 1, "credit": 1},
}


def role_of(account_type: str | None, credit: bool) -> Role:
    """An account's role: a credit (capacity or forwarding) whatever its type, revenue, or else
    a cost. An account without a type counts as a cost."""
    if credit:
        return "credit"
    return "revenue" if account_type == "revenue" else "cost"


def signed(amount: Decimal, role: Role, convention: Convention) -> Decimal:
    """A natural-positive amount with the convention's sign for the role."""
    return amount * SIGN[convention][role]
