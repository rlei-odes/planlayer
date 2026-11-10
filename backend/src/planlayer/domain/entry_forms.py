"""Entry forms (ADR 0007): how a planning line is entered, and how its year total is spread.

Every form stores monthly values. The form decides which cells the planner types in and which
basis turns a year total into months.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Literal, get_args

from planlayer.domain.spreading import Profile, equal, from_reference

Form = Literal["monthly", "annual", "annual_even", "annual_profile", "annual_reference"]
FORMS: tuple[Form, ...] = get_args(Form)
Basis = Literal["even", "profile", "reference"]


@dataclass(frozen=True)
class EntryForm:
    form: Form
    profile_id: int | None = None
    # None: prior-year actuals.
    reference_version_id: int | None = None
    # A basis chosen for one line on the sheet, instead of the one the form implies.
    spread: Basis | None = None

    @property
    def months_visible(self) -> bool:
        return self.form != "annual"

    @property
    def months_editable(self) -> bool:
        return self.form == "monthly"

    @property
    def spreads(self) -> bool:
        """Whether a line's spread can be chosen: not for year-only lines, whose months aren't
        shown and are stored evenly."""
        return self.form != "annual"

    def with_spread(self, basis: Basis, profile_id: int | None) -> "EntryForm":
        """The form with a line's own basis; a reference is prior-year actuals."""
        if not self.spreads:
            return self
        if (basis == "profile") != (profile_id is not None):
            raise ValueError("a profile basis needs a profile, and only it")
        return replace(self, spread=basis, profile_id=profile_id, reference_version_id=None)

    @property
    def basis(self) -> Basis:
        if self.spread is not None:
            return self.spread
        match self.form:
            case "monthly":
                return "profile" if self.profile_id is not None else "reference"
            case "annual" | "annual_even":
                return "even"
            case "annual_profile":
                return "profile"
            case "annual_reference":
                return "reference"


def resolve(chain: Sequence[EntryForm | None], default: EntryForm) -> EntryForm:
    """The nearest setting: ``chain`` runs from the account itself up to the root."""
    return next((f for f in chain if f is not None), default)


@dataclass(frozen=True)
class Weights:
    weights: list[Decimal] | list[int]
    # The reference was all zero or missing, so the line is spread evenly.
    fallback: bool


def weights(
    basis: Basis, months: int, profile: Profile | None, reference: Sequence[Decimal | None]
) -> Weights:
    """Spread weights for a line (ADR 0007 fallbacks: negative reference values count as 0, an
    all-zero or missing reference spreads evenly)."""
    match basis:
        case "even":
            return Weights(equal(months), fallback=False)
        case "profile":
            if profile is None:
                raise ValueError("a profile basis needs a profile")
            return Weights(profile.weights(), fallback=False)
        case "reference":
            fallback = sum(max(v, Decimal(0)) for v in reference if v is not None) == 0
            return Weights(from_reference(reference, negative="zero", if_zero="equal"), fallback)
