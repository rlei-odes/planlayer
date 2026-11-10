"""Capacity planning endpoints (ADR 0013): productive cost centers of a round, their capacity
sheets, planned hours, lines and plan rates. Amounts and rates are decimal strings."""

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Annotated, Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from planlayer import capacity, planning
from planlayer.api.auth import CurrentUser, engine_of
from planlayer.api.planning import HANDLED, CellOut, ConflictOut, LineSpreadIO, _errors, _s
from planlayer.domain.entry_forms import Basis
from planlayer.domain.workflow import RoundStatus, Status

router = APIRouter(prefix="/api")


class CapacityUnitOut(BaseModel):
    code: str
    name: str
    status: Status
    can_write: bool


class CapacityRowOut(BaseModel):
    kind: Literal["group", "line", "credit"]
    activity_type_code: str
    activity_type_name: str
    plan: list[CellOut]
    prior: list[str | None]  # actual hours of the prior fiscal year
    work_center_code: str | None
    work_center_name: str | None
    added: bool  # a work center added by hand
    rate: str | None
    basis: str | None
    spread: Basis | None
    chosen_spread: LineSpreadIO | None
    profile: str | None
    fallback: bool
    editable: bool
    account_code: str | None  # credit rows: the account credited; None when the type names none
    account_name: str | None


class CodeName(BaseModel):
    code: str
    name: str


class InactiveLineOut(BaseModel):
    work_center_code: str
    work_center_name: str
    activity_type_code: str
    activity_type_name: str


class CapacitySheetOut(BaseModel):
    version_code: str
    version_name: str
    fiscal_year: int
    round_status: RoundStatus
    deadline: date | None
    cost_center_code: str
    cost_center_name: str
    status: Status
    lock_reason: str | None
    periods: list[date]
    rows: list[CapacityRowOut]
    inactive: list[InactiveLineOut]
    work_centers: list[CodeName]
    activity_types: list[CodeName]
    batch_id: str | None = None


class HoursEditIn(BaseModel):
    work_center_code: str
    activity_type_code: str
    month: Annotated[int, Field(ge=0, le=11)] | None = None
    value: str | None
    expected: Annotated[list[int | None], Field(min_length=12, max_length=12)]


class HoursEditsIn(BaseModel):
    edits: Annotated[list[HoursEditIn], Field(min_length=1, max_length=20_000)]


class LineChangeIn(BaseModel):
    """Only the given keys change; ``spread: null`` goes back to prior-year actual hours."""

    rate: str | None = None
    active: bool | None = None
    spread: LineSpreadIO | None = None


class LineIn(BaseModel):
    work_center_code: Annotated[str, Field(min_length=1)]
    activity_type_code: Annotated[str, Field(min_length=1)]
    rate: str
    new_work_center_name: str | None = None  # creates the work center for this cost center


def _rate(value: str) -> Decimal:
    try:
        rate = Decimal(value.strip().replace(",", "."))
    except InvalidOperation:
        raise planning.EditError(f"not a rate: {value!r}") from None
    if not rate.is_finite():
        raise planning.EditError(f"not a rate: {value!r}")
    return rate


def _rate_text(rate: Decimal) -> str:
    """A rate with 2 to 4 decimal places: 85.5 is "85.50", 12.3456 stays."""
    text = f"{rate.quantize(Decimal('0.0001')):f}"
    whole, _, decimals = text.partition(".")
    return f"{whole}.{decimals.rstrip('0').ljust(2, '0')}"


def _sheet_out(s: capacity.CapacitySheet, batch_id: str | None = None) -> CapacitySheetOut:
    return CapacitySheetOut(
        batch_id=batch_id,
        version_code=s.version_code,
        version_name=s.version_name,
        fiscal_year=s.fiscal_year,
        round_status=s.round_status,
        deadline=s.deadline,
        cost_center_code=s.cost_center_code,
        cost_center_name=s.cost_center_name,
        status=s.status,
        lock_reason=s.lock_reason,
        periods=s.periods,
        rows=[
            CapacityRowOut(
                kind=r.kind,
                activity_type_code=r.activity_type_code,
                activity_type_name=r.activity_type_name,
                plan=[CellOut(value=_s(c.value), row_version=c.row_version) for c in r.plan],
                prior=[_s(v) for v in r.prior],
                work_center_code=r.work_center_code,
                work_center_name=r.work_center_name,
                added=r.added,
                rate=None if r.rate is None else _rate_text(r.rate),
                basis=r.basis,
                spread=r.spread,
                chosen_spread=None
                if r.chosen_spread is None
                else LineSpreadIO(
                    basis=r.chosen_spread.basis, profile_code=r.chosen_spread.profile_code
                ),
                profile=r.profile,
                fallback=r.fallback,
                editable=r.editable,
                account_code=r.account_code,
                account_name=r.account_name,
            )
            for r in s.rows
        ],
        inactive=[InactiveLineOut.model_validate(i, from_attributes=True) for i in s.inactive],
        work_centers=[CodeName(code=c, name=n) for c, n in s.work_centers],
        activity_types=[CodeName(code=c, name=n) for c, n in s.activity_types],
    )


def _conflict(e: planning.ConflictError) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content=[{"account_code": c.account_code, "month": c.month} for c in e.conflicts],
    )


V = "/versions/{version_code}/capacity"


@router.get(V)
def units(request: Request, user: CurrentUser, version_code: str) -> list[CapacityUnitOut]:
    """The round's productive cost centers whose capacity the user may read."""
    try:
        found = capacity.units(engine_of(request), user, version_code)
    except HANDLED as e:
        raise _errors(e) from None
    return [CapacityUnitOut.model_validate(u, from_attributes=True) for u in found]


@router.get(V + "/{cc_code}", responses={404: {}})
def sheet(request: Request, user: CurrentUser, version_code: str, cc_code: str) -> CapacitySheetOut:
    try:
        return _sheet_out(capacity.read_sheet(engine_of(request), user, version_code, cc_code))
    except HANDLED as e:
        raise _errors(e) from None


@router.post(
    V + "/{cc_code}/edits",
    responses={404: {}, 409: {"model": list[ConflictOut]}, 422: {}, 423: {}},
)
def edit(
    request: Request, user: CurrentUser, version_code: str, cc_code: str, body: HoursEditsIn
) -> CapacitySheetOut:
    """Planned hours, all or none; the credits follow. Returns the sheet with the change batch,
    or 409 with the conflicting cells."""
    engine = engine_of(request)
    edits = [
        capacity.HoursEdit(e.work_center_code, e.activity_type_code, e.month, e.value, e.expected)
        for e in body.edits
    ]
    try:
        batch = capacity.apply_edits(engine, user, version_code, cc_code, edits)
    except planning.ConflictError as e:
        return _conflict(e)  # type: ignore[return-value]
    except HANDLED as e:
        raise _errors(e) from None
    return _sheet_out(capacity.read_sheet(engine, user, version_code, cc_code), batch)


@router.patch(
    V + "/{cc_code}/lines/{work_center_code}/{activity_type_code}",
    responses={403: {}, 404: {}, 422: {}, 423: {}},
)
def change_line(
    request: Request,
    user: CurrentUser,
    version_code: str,
    cc_code: str,
    work_center_code: str,
    activity_type_code: str,
    body: LineChangeIn,
) -> CapacitySheetOut:
    """A line's plan rate, state (controllers) or spread; the credits follow."""
    engine, given = engine_of(request), body.model_fields_set
    try:
        change = capacity.LineChange(
            rate=_rate(body.rate) if "rate" in given and body.rate is not None else None,
            active=body.active if "active" in given else None,
            set_spread="spread" in given,
            spread=None
            if body.spread is None
            else planning.LineSpread(body.spread.basis, body.spread.profile_code),
        )
        capacity.change_line(
            engine, user, version_code, cc_code, work_center_code, activity_type_code, change
        )
        return _sheet_out(capacity.read_sheet(engine, user, version_code, cc_code))
    except HANDLED as e:
        raise _errors(e) from None


@router.post(V + "/{cc_code}/lines", responses={404: {}, 422: {}, 423: {}})
def add_line(
    request: Request, user: CurrentUser, version_code: str, cc_code: str, body: LineIn
) -> CapacitySheetOut:
    """Add a line to the sheet in this round, creating a new work center when named."""
    engine = engine_of(request)
    try:
        capacity.add_line(
            engine,
            user,
            version_code,
            cc_code,
            body.work_center_code,
            body.activity_type_code,
            _rate(body.rate),
            body.new_work_center_name,
        )
        return _sheet_out(capacity.read_sheet(engine, user, version_code, cc_code))
    except HANDLED as e:
        raise _errors(e) from None
