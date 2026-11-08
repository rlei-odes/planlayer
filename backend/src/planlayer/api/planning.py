"""Planning endpoints: rounds, the cost center switcher, the grid and its edits, sheet accounts,
the workflow and the status page.

Amounts travel as strings (T-12); ``null`` is a blank cell, distinct from ``"0.00"``.
Every endpoint acts for the logged-in user (ADR 0009).
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from planlayer import access, planning, rounds, workflow
from planlayer.api.auth import CurrentUser, engine_of
from planlayer.domain.entry_forms import Form
from planlayer.domain.workflow import Action, RoundStatus, Status, WorkflowError

router = APIRouter(prefix="/api")
_engine = engine_of


def _s(value: Decimal | None) -> str | None:
    return None if value is None else f"{value:.2f}"


class Version(BaseModel):
    code: str
    name: str
    kind: str
    fiscal_year: int
    status: RoundStatus
    deadline: date | None


class PlanningUnit(BaseModel):
    code: str
    name: str
    entity_code: str | None
    owner: str | None
    status: Status
    can_write: bool


class CellOut(BaseModel):
    value: str | None
    row_version: int | None


class ComparisonOut(BaseModel):
    kind: str
    label: str


class RowOut(BaseModel):
    kind: Literal["group", "account"]
    code: str
    name: str
    group_code: str
    comparisons: list[list[str | None]]
    plan: list[CellOut]
    entry_form: Form | None
    basis: str | None
    fallback: bool
    months_visible: bool
    months_editable: bool
    total_editable: bool


class GridOut(BaseModel):
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
    comparisons: list[ComparisonOut]
    rows: list[RowOut]


class EditIn(BaseModel):
    account_code: str
    month: Annotated[int, Field(ge=0, le=11)] | None = None
    value: str | None
    expected: Annotated[list[int | None], Field(min_length=12, max_length=12)]


class EditsIn(BaseModel):
    edits: Annotated[list[EditIn], Field(min_length=1, max_length=20_000)]


class ConflictOut(BaseModel):
    account_code: str
    month: int


class AccountOut(BaseModel):
    code: str
    name: str


class AddAccountIn(BaseModel):
    account_code: str


class ActionIn(BaseModel):
    action: Action
    cost_centers: list[str] | None = None
    node: str | None = None
    comment: str | None = None


class RoundStatusIn(BaseModel):
    status: RoundStatus


class EnableIn(BaseModel):
    enabled: bool


class StatusRowOut(BaseModel):
    kind: Literal["node", "cost_center"]
    code: str
    name: str
    depth: int
    counts: dict[Status, int]
    status: Status | None
    owner: str | None
    approvers: list[str]
    changed_at: datetime | None
    changed_by: str | None
    overdue: bool
    actions: list[Action]


class NotPlannedOut(BaseModel):
    code: str
    name: str
    reason: Literal["new", "disabled"]


class OverviewOut(BaseModel):
    round: Version
    rows: list[StatusRowOut]
    not_planned: list[NotPlannedOut]


def _version(r: rounds.RoundRef) -> Version:
    return Version(
        code=r.code,
        name=r.name,
        kind=r.kind,
        fiscal_year=r.fiscal_year,
        status=r.status,
        deadline=r.deadline,
    )


def _grid_out(grid: planning.Grid) -> GridOut:
    return GridOut(
        version_code=grid.version_code,
        version_name=grid.version_name,
        fiscal_year=grid.fiscal_year,
        round_status=grid.round_status,
        deadline=grid.deadline,
        cost_center_code=grid.cost_center_code,
        cost_center_name=grid.cost_center_name,
        status=grid.status,
        lock_reason=grid.lock_reason,
        periods=grid.periods,
        comparisons=[ComparisonOut(kind=c.kind, label=c.label) for c in grid.comparisons],
        rows=[
            RowOut(
                kind=r.kind,
                code=r.code,
                name=r.name,
                group_code=r.group_code,
                comparisons=[[_s(v) for v in values] for values in r.comparisons],
                plan=[CellOut(value=_s(c.value), row_version=c.row_version) for c in r.plan],
                entry_form=r.entry_form,
                basis=r.basis,
                fallback=r.fallback,
                months_visible=r.months_visible,
                months_editable=r.months_editable,
                total_editable=r.total_editable,
            )
            for r in grid.rows
        ],
    )


def _errors(e: Exception) -> HTTPException:
    match e:
        case rounds.NotFoundError():
            return HTTPException(404, str(e))
        case access.AccessError():
            return HTTPException(403, str(e))
        case planning.LockedError():
            return HTTPException(423, str(e))
        case planning.EditError() | WorkflowError() | rounds.RoundError():
            return HTTPException(422, str(e))
    raise e


HANDLED = (
    rounds.NotFoundError,
    access.AccessError,
    planning.LockedError,
    planning.EditError,
    WorkflowError,
    rounds.RoundError,
)


@router.get("/versions")
def versions(request: Request, user: CurrentUser) -> list[Version]:
    return [_version(r) for r in rounds.rounds(_engine(request))]


@router.post("/versions/{version_code}/status", responses={404: {}, 422: {}})
def round_status(
    request: Request, user: CurrentUser, version_code: str, body: RoundStatusIn
) -> Version:
    engine = _engine(request)
    try:
        rounds.set_round_status(engine, user, version_code, body.status)
    except HANDLED as e:
        raise _errors(e) from None
    with engine.connect() as conn:
        return _version(rounds.get_round(conn, version_code))


@router.get("/versions/{version_code}/cost-centers")
def cost_centers(request: Request, user: CurrentUser, version_code: str) -> list[PlanningUnit]:
    try:
        units = planning.planning_units(_engine(request), user, version_code)
    except HANDLED as e:
        raise _errors(e) from None
    return [PlanningUnit.model_validate(u) for u in units]


@router.post("/versions/{version_code}/cost-centers/{cc_code}/enabled", responses={404: {}})
def enable(
    request: Request, user: CurrentUser, version_code: str, cc_code: str, body: EnableIn
) -> None:
    try:
        rounds.set_member_enabled(_engine(request), user, version_code, cc_code, body.enabled)
    except HANDLED as e:
        raise _errors(e) from None


@router.get("/versions/{version_code}/cost-centers/{cc_code}/grid")
def grid(request: Request, user: CurrentUser, version_code: str, cc_code: str) -> GridOut:
    try:
        return _grid_out(planning.read_grid(_engine(request), user, version_code, cc_code))
    except HANDLED as e:
        raise _errors(e) from None


@router.post(
    "/versions/{version_code}/cost-centers/{cc_code}/edits",
    responses={409: {"model": list[ConflictOut]}, 422: {}, 423: {}},
)
def edit(
    request: Request, user: CurrentUser, version_code: str, cc_code: str, body: EditsIn
) -> GridOut:
    """Apply edits atomically; returns the updated grid, or 409 with the conflicting cells."""
    engine = _engine(request)
    edits = [planning.Edit(e.account_code, e.month, e.value, e.expected) for e in body.edits]
    try:
        planning.apply_edits(engine, user, version_code, cc_code, edits)
    except planning.ConflictError as e:
        return JSONResponse(  # type: ignore[return-value]
            status_code=409,
            content=[{"account_code": c.account_code, "month": c.month} for c in e.conflicts],
        )
    except HANDLED as e:
        raise _errors(e) from None
    return _grid_out(planning.read_grid(engine, user, version_code, cc_code))


@router.get("/versions/{version_code}/accounts")
def accounts(request: Request, user: CurrentUser, version_code: str) -> list[AccountOut]:
    """Accounts a planner may add to a sheet."""
    try:
        found = planning.addable_accounts(_engine(request), version_code)
    except HANDLED as e:
        raise _errors(e) from None
    return [AccountOut.model_validate(a) for a in found]


@router.post(
    "/versions/{version_code}/cost-centers/{cc_code}/accounts", responses={422: {}, 423: {}}
)
def add_account(
    request: Request, user: CurrentUser, version_code: str, cc_code: str, body: AddAccountIn
) -> GridOut:
    engine = _engine(request)
    try:
        planning.add_account(engine, user, version_code, cc_code, body.account_code)
        return _grid_out(planning.read_grid(engine, user, version_code, cc_code))
    except HANDLED as e:
        raise _errors(e) from None


@router.post("/versions/{version_code}/workflow", responses={404: {}, 422: {}})
def act(request: Request, user: CurrentUser, version_code: str, body: ActionIn) -> list[str]:
    """Submit, approve, reject or reopen cost centers; returns the codes changed."""
    try:
        return workflow.act(
            _engine(request),
            user,
            version_code,
            body.action,
            cost_centers=body.cost_centers,
            node=body.node,
            comment=body.comment,
        )
    except HANDLED as e:
        raise _errors(e) from None


@router.get("/versions/{version_code}/overview")
def overview(request: Request, user: CurrentUser, version_code: str) -> OverviewOut:
    """The status page: every enabled cost center, rolled up per hierarchy node."""
    try:
        o = workflow.overview(_engine(request), user, version_code, date.today())
    except HANDLED as e:
        raise _errors(e) from None
    return OverviewOut(
        round=_version(o.round),
        rows=[StatusRowOut.model_validate(r, from_attributes=True) for r in o.rows],
        not_planned=[NotPlannedOut.model_validate(n, from_attributes=True) for n in o.not_planned],
    )
