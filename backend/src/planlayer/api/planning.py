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

from planlayer import access, notes, planning, rounds, workflow
from planlayer.api.auth import CurrentUser, engine_of
from planlayer.domain.entry_forms import Basis, Form
from planlayer.domain.workflow import Action, RoundStatus, Status, WorkflowError
from planlayer.sheet import ConflictError, EditError, LockedError

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
    grain: Literal["month", "quarter"] = "month"  # how the round is planned (ADR 0015)
    actuals_through: date | None = None  # a forecast's last closed month


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
    note: str | None  # the controller's note to planners
    actuals_until: date | None  # actuals columns: the last month holding actuals
    partial: bool  # actuals end before the year does


class LineSpreadIO(BaseModel):
    """How one line spreads: ``reference`` is prior-year actuals; ``profile`` names a profile."""

    basis: Basis
    profile_code: str | None = None


class SpreadIn(BaseModel):
    spread: LineSpreadIO | None  # None: back to the entry form's basis


class NotesMarkerOut(BaseModel):
    open: int  # open threads shown in this round
    resolved: int
    task_for_me: bool  # an open thread is the user's task
    notepad: bool


class ListMarkerOut(BaseModel):
    list_id: int
    total: str
    visibility: Literal["private", "cost_center", "public"]


def notes_marker(m: notes.Marker | None) -> NotesMarkerOut | None:
    if m is None:
        return None
    return NotesMarkerOut(
        open=m.open, resolved=m.resolved, task_for_me=m.task_for_me, notepad=m.notepad
    )


class ForwardedOut(BaseModel):
    """A line written by cost forwarding (ADR 0018): the forwarding and the other side."""

    code: str
    name: str
    side: Literal["charge", "credit"]
    method: Literal["full", "fixed", "rate"]
    source_code: str
    source_name: str
    receivers: int


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
    spread: Basis | None
    chosen_spread: LineSpreadIO | None
    # The version a reference basis spreads like; None: prior-year actuals.
    reference_version: str | None
    profile: str | None  # the profile a profile basis spreads by
    months_visible: bool
    months_editable: bool
    total_editable: bool
    capacity: bool  # credited by capacity planning, read only
    forwarded: ForwardedOut | None = None  # written by cost forwarding, read only
    personnel: bool = False  # written by personnel costs, read only (ADR 0022)
    credit: bool = False  # a credit, shown negative (stored natural-positive, T-7)
    # Why the line takes no plan values (ADR 0014): not planned in the round, or outside the
    # cost center's account set; it shows read only for its comparison values.
    not_planned: Literal["round", "set"] | None = None
    notes: NotesMarkerOut | None = None  # the line's notes in this round (ADR 0017)
    item_list: ListMarkerOut | None = None  # the line's item list the user can see


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
    version_kind: str
    grain: Literal["month", "quarter"]
    # A forecast's closed months (ADR 0015): the first ``closed_months`` plan cells hold
    # actuals, read only, without a row version.
    actuals_through: date | None
    closed_months: int
    # The change batch a save or an undo wrote, for undo (E-05); null on a plain read.
    batch_id: str | None = None


class EditIn(BaseModel):
    """One edit: a month, a quarter (rounds planned by quarter), or neither for the year."""

    account_code: str
    month: Annotated[int, Field(ge=0, le=11)] | None = None
    quarter: Annotated[int, Field(ge=0, le=3)] | None = None
    value: str | None
    expected: Annotated[list[int | None], Field(min_length=12, max_length=12)]


class EditsIn(BaseModel):
    edits: Annotated[list[EditIn], Field(min_length=1, max_length=20_000)]


class UndoIn(BaseModel):
    batch_id: Annotated[str, Field(min_length=1, max_length=64)]


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


def version_out(r: rounds.RoundRef) -> Version:
    return Version(
        code=r.code,
        name=r.name,
        kind=r.kind,
        fiscal_year=r.fiscal_year,
        status=r.status,
        deadline=r.deadline,
        grain=r.grain,
        actuals_through=r.actuals_through,
    )


def _grid_out(grid: planning.Grid, batch_id: str | None = None) -> GridOut:
    return GridOut(
        batch_id=batch_id,
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
        version_kind=grid.version_kind,
        grain=grid.grain,
        actuals_through=grid.actuals_through,
        closed_months=grid.closed_months,
        comparisons=[
            ComparisonOut(
                kind=c.kind,
                label=c.label,
                note=c.note,
                actuals_until=c.actuals_until,
                partial=c.partial,
            )
            for c in grid.comparisons
        ],
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
                spread=r.spread,
                reference_version=r.reference_version,
                profile=r.profile,
                chosen_spread=(
                    None
                    if r.chosen_spread is None
                    else LineSpreadIO(
                        basis=r.chosen_spread.basis, profile_code=r.chosen_spread.profile_code
                    )
                ),
                months_visible=r.months_visible,
                months_editable=r.months_editable,
                total_editable=r.total_editable,
                capacity=r.capacity,
                personnel=r.personnel,
                forwarded=None
                if r.forwarded is None
                else ForwardedOut.model_validate(r.forwarded, from_attributes=True),
                credit=r.credit,
                not_planned=r.not_planned,
                notes=notes_marker(r.notes),
                item_list=None
                if r.item_list is None
                else ListMarkerOut(
                    list_id=r.item_list.list_id,
                    total=f"{r.item_list.total:.2f}",
                    visibility=r.item_list.visibility,  # type: ignore[arg-type]
                ),
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
        case LockedError():
            return HTTPException(423, str(e))
        case EditError() | WorkflowError() | rounds.RoundError():
            return HTTPException(422, str(e))
    raise e


HANDLED = (
    rounds.NotFoundError,
    access.AccessError,
    LockedError,
    EditError,
    WorkflowError,
    rounds.RoundError,
)


@router.get("/versions")
def versions(request: Request, user: CurrentUser) -> list[Version]:
    return [version_out(r) for r in rounds.rounds(_engine(request))]


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
        return version_out(rounds.get_round(conn, version_code))


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
    """Apply edits atomically; returns the updated grid with the change batch it wrote, or 409
    with the conflicting cells."""
    engine = _engine(request)
    edits = [
        planning.Edit(e.account_code, e.month, e.value, e.expected, e.quarter) for e in body.edits
    ]
    try:
        batch = planning.apply_edits(engine, user, version_code, cc_code, edits)
    except ConflictError as e:
        return _conflict(e)  # type: ignore[return-value]
    except HANDLED as e:
        raise _errors(e) from None
    return _grid_out(planning.read_grid(engine, user, version_code, cc_code), batch)


@router.post(
    "/versions/{version_code}/cost-centers/{cc_code}/undo",
    responses={404: {}, 409: {"model": list[ConflictOut]}, 423: {}},
)
def undo(
    request: Request, user: CurrentUser, version_code: str, cc_code: str, body: UndoIn
) -> GridOut:
    """Revert one of the user's change batches on the sheet; returns the updated grid with the
    undo's own batch (undoing it redoes the change), or 409 with the cells changed since."""
    engine = _engine(request)
    try:
        batch = planning.undo_batch(engine, user, version_code, cc_code, body.batch_id)
    except ConflictError as e:
        return _conflict(e)  # type: ignore[return-value]
    except HANDLED as e:
        raise _errors(e) from None
    return _grid_out(planning.read_grid(engine, user, version_code, cc_code), batch)


def _conflict(e: ConflictError) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content=[{"account_code": c.account_code, "month": c.month} for c in e.conflicts],
    )


@router.get("/versions/{version_code}/cost-centers/{cc_code}/accounts", responses={404: {}})
def accounts(
    request: Request, user: CurrentUser, version_code: str, cc_code: str
) -> list[AccountOut]:
    """Accounts a planner may add to the cost center's sheet: those it plans (ADR 0014)."""
    try:
        found = planning.addable_accounts(_engine(request), user, version_code, cc_code)
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


@router.put(
    "/versions/{version_code}/cost-centers/{cc_code}/lines/{account_code}/spread",
    responses={404: {}, 422: {}, 423: {}},
)
def set_line_spread(
    request: Request,
    user: CurrentUser,
    version_code: str,
    cc_code: str,
    account_code: str,
    body: SpreadIn,
) -> GridOut:
    """How one line spreads the year totals typed from now on; stored months stay."""
    engine = _engine(request)
    chosen = (
        None
        if body.spread is None
        else planning.LineSpread(body.spread.basis, body.spread.profile_code)
    )
    try:
        planning.set_line_spread(engine, user, version_code, cc_code, account_code, chosen)
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
        round=version_out(o.round),
        rows=[StatusRowOut.model_validate(r, from_attributes=True) for r in o.rows],
        not_planned=[NotPlannedOut.model_validate(n, from_attributes=True) for n in o.not_planned],
    )
