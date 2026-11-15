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
from planlayer.api.planning import (
    HANDLED,
    CellOut,
    ComparisonOut,
    ConflictOut,
    LineSpreadIO,
    NotesMarkerOut,
    _errors,
    _s,
    notes_marker,
)
from planlayer.domain.entry_forms import Basis
from planlayer.domain.workflow import RoundStatus, Status
from planlayer.sheet import ConflictError, EditError

router = APIRouter(prefix="/api")


class CapacityUnitOut(BaseModel):
    code: str
    name: str
    status: Status
    can_write: bool
    staff_hours: bool  # it plans hours from its positions: the workforce tab (ADR 0027)


class CapacityRowOut(BaseModel):
    kind: Literal["group", "line", "credit"]
    activity_type_code: str
    activity_type_name: str
    plan: list[CellOut]
    # One list of 12 months per comparison column: hours, or amounts on a credit row.
    comparisons: list[list[str | None]]
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
    staffed: bool  # lines: hours from the cost center's positions, read only (ADR 0027)
    account_code: str | None  # credit rows: the account credited; None when the type names none
    account_name: str | None
    notes: NotesMarkerOut | None = None  # lines: their notes in this round (ADR 0017)


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
    comparisons: list[ComparisonOut]  # the round's columns (ADR 0016)
    rows: list[CapacityRowOut]
    inactive: list[InactiveLineOut]
    work_centers: list[CodeName]
    activity_types: list[CodeName]
    batch_id: str | None = None
    version_kind: str
    grain: Literal["month", "quarter"]
    # A forecast's closed months: the first ``closed_months`` cells hold actual hours, read only.
    actuals_through: date | None
    closed_months: int
    typed_hours: bool  # hours typed per line (ADR 0027)
    staff_hours: bool  # hours from the cost center's positions


class HoursEditIn(BaseModel):
    """One edit: a month, a quarter (rounds planned by quarter), or neither for the year."""

    work_center_code: str
    activity_type_code: str
    month: Annotated[int, Field(ge=0, le=11)] | None = None
    quarter: Annotated[int, Field(ge=0, le=3)] | None = None
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
        raise EditError(f"not a rate: {value!r}") from None
    if not rate.is_finite():
        raise EditError(f"not a rate: {value!r}")
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
        version_kind=s.version_kind,
        grain=s.grain,
        actuals_through=s.actuals_through,
        closed_months=s.closed_months,
        typed_hours=s.typed_hours,
        staff_hours=s.staff_hours,
        comparisons=[
            ComparisonOut(
                kind=c.kind,
                label=c.label,
                note=c.note,
                actuals_until=c.actuals_until,
                partial=c.partial,
            )
            for c in s.comparisons
        ],
        rows=[
            CapacityRowOut(
                kind=r.kind,
                activity_type_code=r.activity_type_code,
                activity_type_name=r.activity_type_name,
                plan=[CellOut(value=_s(c.value), row_version=c.row_version) for c in r.plan],
                comparisons=[[_s(v) for v in months] for months in r.comparisons],
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
                staffed=r.staffed,
                account_code=r.account_code,
                account_name=r.account_name,
                notes=notes_marker(r.notes),
            )
            for r in s.rows
        ],
        inactive=[InactiveLineOut.model_validate(i, from_attributes=True) for i in s.inactive],
        work_centers=[CodeName(code=c, name=n) for c, n in s.work_centers],
        activity_types=[CodeName(code=c, name=n) for c, n in s.activity_types],
    )


def _conflict(e: ConflictError) -> JSONResponse:
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
        capacity.HoursEdit(
            e.work_center_code, e.activity_type_code, e.month, e.value, e.expected, e.quarter
        )
        for e in body.edits
    ]
    try:
        batch = capacity.apply_edits(engine, user, version_code, cc_code, edits)
    except ConflictError as e:
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
        batch = capacity.change_line(
            engine, user, version_code, cc_code, work_center_code, activity_type_code, change
        )
        return _sheet_out(capacity.read_sheet(engine, user, version_code, cc_code), batch)
    except HANDLED as e:
        raise _errors(e) from None


@router.post(V + "/{cc_code}/lines", responses={404: {}, 422: {}, 423: {}})
def add_line(
    request: Request, user: CurrentUser, version_code: str, cc_code: str, body: LineIn
) -> CapacitySheetOut:
    """Add a line to the sheet in this round, creating a new work center when named."""
    engine = engine_of(request)
    try:
        batch = capacity.add_line(
            engine,
            user,
            version_code,
            cc_code,
            body.work_center_code,
            body.activity_type_code,
            _rate(body.rate),
            body.new_work_center_name,
        )
        return _sheet_out(capacity.read_sheet(engine, user, version_code, cc_code), batch)
    except HANDLED as e:
        raise _errors(e) from None


class UndoIn(BaseModel):
    batch_id: Annotated[str, Field(min_length=1, max_length=64)]


@router.post(V + "/{cc_code}/undo", responses={404: {}, 409: {"model": list[ConflictOut]}, 423: {}})
def undo(
    request: Request, user: CurrentUser, version_code: str, cc_code: str, body: UndoIn
) -> CapacitySheetOut:
    """Revert one of the user's change batches on the sheet; returns the sheet with the undo's
    own batch (undoing it redoes the change), or 409 with what changed since."""
    engine = engine_of(request)
    try:
        batch = capacity.undo_batch(engine, user, version_code, cc_code, body.batch_id)
    except ConflictError as e:
        return _conflict(e)  # type: ignore[return-value]
    except HANDLED as e:
        raise _errors(e) from None
    return _sheet_out(capacity.read_sheet(engine, user, version_code, cc_code), batch)


# --- Hours from the workforce (ADR 0027) ---------------------------------------------------


class StaffLineOut(BaseModel):
    work_center_code: str
    work_center_name: str
    activity_type_code: str
    activity_type_name: str
    rate: str
    hours: str | None  # what the positions give it in the open months


class StaffPositionOut(BaseModel):
    position_id: int
    code: str
    title: str
    person_name: str | None
    staff_type: str
    entry_date: date | None
    leave_date: date | None
    contract_percent: str | None
    productive_percent: str | None  # its own; null takes the default
    work_center_code: str | None  # its own line; null takes the default
    activity_type_code: str | None
    hours: str | None  # in the open months


class StaffOut(BaseModel):
    cost_center_code: str
    cost_center_name: str
    lock_reason: str | None
    fte_hours: str | None
    typed_hours: bool
    default_work_center_code: str | None
    default_activity_type_code: str | None
    default_percent: str | None
    lines: list[StaffLineOut]
    positions: list[StaffPositionOut]
    batch_id: str | None = None


class LineRefIO(BaseModel):
    work_center_code: Annotated[str, Field(min_length=1)]
    activity_type_code: Annotated[str, Field(min_length=1)]


class StaffDefaultsIn(BaseModel):
    line: LineRefIO | None
    percent: str | None


class StaffPositionIn(BaseModel):
    """Null takes the cost center's default."""

    line: LineRefIO | None
    percent: str | None


def _staff_out(v: capacity.StaffView, batch_id: str | None = None) -> StaffOut:
    return StaffOut(
        batch_id=batch_id,
        cost_center_code=v.cost_center_code,
        cost_center_name=v.cost_center_name,
        lock_reason=v.lock_reason,
        fte_hours=_s(v.fte_hours),
        typed_hours=v.typed_hours,
        default_work_center_code=v.default_work_center_code,
        default_activity_type_code=v.default_activity_type_code,
        default_percent=_s(v.default_percent),
        lines=[
            StaffLineOut(
                work_center_code=x.work_center_code,
                work_center_name=x.work_center_name,
                activity_type_code=x.activity_type_code,
                activity_type_name=x.activity_type_name,
                rate=_rate_text(x.rate),
                hours=_s(x.hours),
            )
            for x in v.lines
        ],
        positions=[
            StaffPositionOut(
                position_id=x.position_id,
                code=x.code,
                title=x.title,
                person_name=x.person_name,
                staff_type=x.staff_type,
                entry_date=x.entry_date,
                leave_date=x.leave_date,
                contract_percent=_s(x.contract_percent),
                productive_percent=_s(x.productive_percent),
                work_center_code=x.work_center_code,
                activity_type_code=x.activity_type_code,
                hours=_s(x.hours),
            )
            for x in v.positions
        ],
    )


def _codes(line: LineRefIO | None) -> tuple[str, str] | None:
    return None if line is None else (line.work_center_code, line.activity_type_code)


@router.get(V + "/{cc_code}/workforce", responses={404: {}})
def staff(request: Request, user: CurrentUser, version_code: str, cc_code: str) -> StaffOut:
    """The cost center's positions with their productive share, line and hours."""
    try:
        return _staff_out(capacity.read_staff(engine_of(request), user, version_code, cc_code))
    except HANDLED as e:
        raise _errors(e) from None


@router.put(V + "/{cc_code}/workforce", responses={404: {}, 422: {}, 423: {}})
def staff_defaults(
    request: Request, user: CurrentUser, version_code: str, cc_code: str, body: StaffDefaultsIn
) -> StaffOut:
    """The default line and productive share of the cost center's positions; hours follow."""
    engine = engine_of(request)
    try:
        batch = capacity.save_defaults(
            engine, user, version_code, cc_code, _codes(body.line), body.percent
        )
        return _staff_out(capacity.read_staff(engine, user, version_code, cc_code), batch)
    except HANDLED as e:
        raise _errors(e) from None


@router.put(
    V + "/{cc_code}/workforce/positions/{position_id}", responses={404: {}, 422: {}, 423: {}}
)
def staff_position(
    request: Request,
    user: CurrentUser,
    version_code: str,
    cc_code: str,
    position_id: int,
    body: StaffPositionIn,
) -> StaffOut:
    """A position's own line and productive share; hours follow."""
    engine = engine_of(request)
    try:
        batch = capacity.save_position(
            engine, user, version_code, cc_code, position_id, _codes(body.line), body.percent
        )
        return _staff_out(capacity.read_staff(engine, user, version_code, cc_code), batch)
    except HANDLED as e:
        raise _errors(e) from None
