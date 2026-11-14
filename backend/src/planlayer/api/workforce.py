"""Workforce planning endpoints (ADR 0020): a cost center's workforce sheet, typed FTE and
headcount, lines, positions and *Add staff from HR*. Values are decimal strings."""

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Annotated, Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from planlayer import planning, workforce
from planlayer.api.auth import CurrentUser, engine_of
from planlayer.api.capacity import CodeName, UndoIn
from planlayer.api.planning import HANDLED, CellOut, ComparisonOut, ConflictOut, _errors, _s
from planlayer.domain.workflow import RoundStatus, Status
from planlayer.domain.workforce import Measure, Mode

router = APIRouter(prefix="/api")


class WorkforceRowOut(BaseModel):
    kind: Literal["line", "group", "total"]
    measure: Measure
    staff_type_code: str | None
    staff_type_name: str | None
    staff_category_code: str | None
    staff_category_name: str | None
    external: bool
    plan: list[CellOut]
    comparisons: list[list[str | None]]  # one list of 12 months per comparison column
    editable: bool


class PositionOut(BaseModel):
    position_id: int
    code: str | None
    title: str
    person_name: str | None
    staff_type_code: str
    staff_category_code: str
    entry_date: date | None
    leave_date: date | None
    contract_percent: str | None
    is_plan: bool
    is_replacement: bool
    description: str | None
    counted: list[bool]  # per month: active on its last day
    changed_by: str | None
    # hr: taken from HR's staff list; its number, name, entry date and flags stay as HR has them.
    origin: Literal["hr", "manual"]
    removable: bool  # whether this user may delete it (one from HR: controllers only)


class BuildUpOut(BaseModel):
    """Heads at the start of the year (the prior year's end) and at its end; per month the
    positions counted that weren't a month before, of them new and replacement plan positions,
    and those no longer counted. start + entering - leaving = end."""

    start: int
    end: int
    entering: list[int]
    new: list[int]
    replacements: list[int]
    leaving: list[int]


class StaffTypeOut(BaseModel):
    code: str
    name: str
    external: bool


class WorkforceSheetOut(BaseModel):
    version_code: str
    version_name: str
    fiscal_year: int
    round_status: RoundStatus
    deadline: date | None
    cost_center_code: str
    cost_center_name: str
    status: Status
    lock_reason: str | None
    mode: Mode
    periods: list[date]
    comparisons: list[ComparisonOut]
    rows: list[WorkforceRowOut]
    version_kind: str
    grain: Literal["month", "quarter"]
    # A forecast's closed months: the first ``closed_months`` cells hold actuals, read only.
    actuals_through: date | None
    closed_months: int
    staff_types: list[StaffTypeOut]
    staff_categories: list[CodeName]
    positions: list[PositionOut]
    build_up: BuildUpOut | None
    hr_available: int  # people in HR's list not yet among the positions
    batch_id: str | None = None


def _sheet_out(s: workforce.WorkforceSheet, batch_id: str | None = None) -> WorkforceSheetOut:
    b = s.build_up
    return WorkforceSheetOut(
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
        mode=s.mode,
        periods=s.periods,
        version_kind=s.version_kind,
        grain=s.grain,
        actuals_through=s.actuals_through,
        closed_months=s.closed_months,
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
            WorkforceRowOut(
                kind=r.kind,
                measure=r.measure,
                staff_type_code=r.staff_type_code,
                staff_type_name=r.staff_type_name,
                staff_category_code=r.staff_category_code,
                staff_category_name=r.staff_category_name,
                external=r.external,
                plan=[CellOut(value=_s(c.value), row_version=c.row_version) for c in r.plan],
                comparisons=[[_s(v) for v in months] for months in r.comparisons],
                editable=r.editable,
            )
            for r in s.rows
        ],
        staff_types=[
            StaffTypeOut(code=t.code, name=t.name, external=t.external) for t in s.staff_types
        ],
        staff_categories=[CodeName(code=c, name=n) for c, n in s.staff_categories],
        positions=[
            PositionOut(
                position_id=p.position_id,
                code=p.code,
                title=p.title,
                person_name=p.person_name,
                staff_type_code=p.staff_type_code,
                staff_category_code=p.staff_category_code,
                entry_date=p.entry_date,
                leave_date=p.leave_date,
                contract_percent=_s(p.contract_percent),
                is_plan=p.is_plan,
                is_replacement=p.is_replacement,
                description=p.description,
                counted=p.counted,
                changed_by=p.changed_by,
                origin=p.origin,  # type: ignore[arg-type]
                removable=p.removable,
            )
            for p in s.positions
        ],
        build_up=None
        if b is None
        else BuildUpOut(
            start=b.start,
            end=b.end,
            entering=b.entering,
            new=b.new,
            replacements=b.replacements,
            leaving=b.leaving,
        ),
        hr_available=s.hr_available,
    )


def _conflict(e: planning.ConflictError) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content=[{"account_code": c.account_code, "month": c.month} for c in e.conflicts],
    )


class WorkforceEditIn(BaseModel):
    """One edit: a month, a quarter (rounds planned by quarter), or neither for the year, which
    fills the open months."""

    staff_type_code: str
    staff_category_code: str
    measure: Measure
    month: Annotated[int, Field(ge=0, le=11)] | None = None
    quarter: Annotated[int, Field(ge=0, le=3)] | None = None
    value: str | None
    expected: Annotated[list[int | None], Field(min_length=12, max_length=12)]


class WorkforceEditsIn(BaseModel):
    edits: Annotated[list[WorkforceEditIn], Field(min_length=1, max_length=20_000)]


class WorkforceLineIn(BaseModel):
    staff_type_code: Annotated[str, Field(min_length=1)]
    staff_category_code: Annotated[str, Field(min_length=1)]


class WorkforcePositionIn(BaseModel):
    """A position as the planner enters it; its number is Planlayer's (N00001, ...)."""

    title: str
    staff_type_code: Annotated[str, Field(min_length=1)]
    staff_category_code: Annotated[str, Field(min_length=1)]
    person_name: str | None = None
    entry_date: date | None = None
    leave_date: date | None = None
    contract_percent: str | None = None  # e.g. "80" or "62.5"
    is_plan: bool = False
    is_replacement: bool = False
    description: str | None = None


class HrTakenOut(BaseModel):
    added: int


def _percent(value: str | None) -> Decimal | None:
    if value is None or not value.strip():
        return None
    text = value.strip().removesuffix("%").strip().replace(",", ".")
    try:
        percent = Decimal(text)
    except InvalidOperation:
        raise planning.EditError(f"not a percentage: {value!r}") from None
    if not percent.is_finite():
        raise planning.EditError(f"not a percentage: {value!r}")
    return percent


def _position(body: WorkforcePositionIn) -> workforce.PositionIn:
    return workforce.PositionIn(
        title=body.title,
        staff_type_code=body.staff_type_code,
        staff_category_code=body.staff_category_code,
        person_name=body.person_name,
        entry_date=body.entry_date,
        leave_date=body.leave_date,
        contract_percent=_percent(body.contract_percent),
        is_plan=body.is_plan,
        is_replacement=body.is_replacement,
        description=body.description,
    )


V = "/versions/{version_code}/workforce"


@router.post(V + "/hr", responses={403: {}, 404: {}, 422: {}, 423: {}})
def take_all_from_hr(request: Request, user: CurrentUser, version_code: str) -> HrTakenOut:
    """*Add staff from HR* for every planned cost center (controllers)."""
    try:
        added = workforce.take_all_from_hr(engine_of(request), user, version_code)
    except HANDLED as e:
        raise _errors(e) from None
    return HrTakenOut(added=added)


@router.get(V + "/{cc_code}", responses={404: {}})
def sheet(
    request: Request, user: CurrentUser, version_code: str, cc_code: str
) -> WorkforceSheetOut:
    try:
        return _sheet_out(workforce.read_sheet(engine_of(request), user, version_code, cc_code))
    except HANDLED as e:
        raise _errors(e) from None


@router.post(
    V + "/{cc_code}/edits",
    responses={404: {}, 409: {"model": list[ConflictOut]}, 422: {}, 423: {}},
)
def edit(
    request: Request, user: CurrentUser, version_code: str, cc_code: str, body: WorkforceEditsIn
) -> WorkforceSheetOut:
    """Typed FTE and headcount, all or none. Returns the sheet with the change batch, or 409 with
    the conflicting cells."""
    engine = engine_of(request)
    edits = [
        workforce.WorkforceEdit(
            e.staff_type_code,
            e.staff_category_code,
            e.measure,
            e.month,
            e.value,
            e.expected,
            e.quarter,
        )
        for e in body.edits
    ]
    try:
        batch = workforce.apply_edits(engine, user, version_code, cc_code, edits)
    except planning.ConflictError as e:
        return _conflict(e)  # type: ignore[return-value]
    except HANDLED as e:
        raise _errors(e) from None
    return _sheet_out(workforce.read_sheet(engine, user, version_code, cc_code), batch)


@router.post(V + "/{cc_code}/undo", responses={404: {}, 409: {"model": list[ConflictOut]}, 423: {}})
def undo(
    request: Request, user: CurrentUser, version_code: str, cc_code: str, body: UndoIn
) -> WorkforceSheetOut:
    """Revert one of the user's change batches of typed values; undoing the undo redoes it."""
    engine = engine_of(request)
    try:
        batch = workforce.undo_batch(engine, user, version_code, cc_code, body.batch_id)
    except planning.ConflictError as e:
        return _conflict(e)  # type: ignore[return-value]
    except HANDLED as e:
        raise _errors(e) from None
    return _sheet_out(workforce.read_sheet(engine, user, version_code, cc_code), batch)


@router.post(V + "/{cc_code}/lines", responses={404: {}, 422: {}, 423: {}})
def add_line(
    request: Request, user: CurrentUser, version_code: str, cc_code: str, body: WorkforceLineIn
) -> WorkforceSheetOut:
    engine = engine_of(request)
    try:
        batch = workforce.add_line(
            engine, user, version_code, cc_code, body.staff_type_code, body.staff_category_code
        )
        return _sheet_out(workforce.read_sheet(engine, user, version_code, cc_code), batch)
    except HANDLED as e:
        raise _errors(e) from None


@router.post(V + "/{cc_code}/positions", responses={404: {}, 422: {}, 423: {}})
def add_position(
    request: Request, user: CurrentUser, version_code: str, cc_code: str, body: WorkforcePositionIn
) -> WorkforceSheetOut:
    """A new position; the cost center's FTE and headcount follow."""
    engine = engine_of(request)
    try:
        batch, _ = workforce.save_position(engine, user, version_code, cc_code, _position(body))
        return _sheet_out(workforce.read_sheet(engine, user, version_code, cc_code), batch)
    except HANDLED as e:
        raise _errors(e) from None


@router.put(V + "/{cc_code}/positions/{position_id}", responses={404: {}, 422: {}, 423: {}})
def change_position(
    request: Request,
    user: CurrentUser,
    version_code: str,
    cc_code: str,
    position_id: int,
    body: WorkforcePositionIn,
) -> WorkforceSheetOut:
    engine = engine_of(request)
    try:
        batch, _ = workforce.save_position(
            engine, user, version_code, cc_code, _position(body), position_id
        )
        return _sheet_out(workforce.read_sheet(engine, user, version_code, cc_code), batch)
    except HANDLED as e:
        raise _errors(e) from None


@router.delete(V + "/{cc_code}/positions/{position_id}", responses={404: {}, 422: {}, 423: {}})
def delete_position(
    request: Request, user: CurrentUser, version_code: str, cc_code: str, position_id: int
) -> WorkforceSheetOut:
    engine = engine_of(request)
    try:
        batch = workforce.delete_position(engine, user, version_code, cc_code, position_id)
        return _sheet_out(workforce.read_sheet(engine, user, version_code, cc_code), batch)
    except HANDLED as e:
        raise _errors(e) from None


@router.post(V + "/{cc_code}/hr", responses={404: {}, 422: {}, 423: {}})
def take_from_hr(
    request: Request, user: CurrentUser, version_code: str, cc_code: str
) -> WorkforceSheetOut:
    """*Add staff from HR*: the cost center's people in HR's list not yet among the positions."""
    engine = engine_of(request)
    try:
        batch, _ = workforce.take_from_hr(engine, user, version_code, cc_code)
        return _sheet_out(workforce.read_sheet(engine, user, version_code, cc_code), batch)
    except HANDLED as e:
        raise _errors(e) from None
