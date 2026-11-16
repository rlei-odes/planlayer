"""Capex planning endpoints (ADR 0028): a round's investments as one list, changed all or
nothing; large projects, confirmations, cash out, comments, the overview and investment types.
Amounts are decimal strings; every write returns the whole list."""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from planlayer import capex
from planlayer.api.auth import CurrentUser, engine_of
from planlayer.api.planning import HANDLED, Version, _errors, _s, version_out
from planlayer.capex import CapexError
from planlayer.capex.items import CapexView
from planlayer.capex.overview import Sums
from planlayer.sheet import ConflictError, EditError, parse_amount

router = APIRouter(prefix="/api")

Status = Literal["open", "confirmed", "declined"]
Kind = Literal["line", "project"]
ERRORS: dict[int | str, dict[str, Any]] = {
    401: {},
    403: {},
    404: {},
    409: {"description": "changed by someone else since it was read"},
    422: {},
    423: {},
}


def _http(e: Exception) -> HTTPException:
    match e:
        case CapexError():
            return HTTPException(422, str(e))
        case ConflictError():
            return HTTPException(
                409, "an investment was changed by someone else since; the list is reloaded"
            )
    return _errors(e)


CAUGHT = (*HANDLED, CapexError, ConflictError)


def _amount(value: str | None) -> Decimal | None:
    try:
        return parse_amount(value)
    except EditError as e:
        raise CapexError(str(e)) from None


class CapexTypeIO(BaseModel):
    code: Annotated[str, Field(min_length=1, max_length=20)]
    name: Annotated[str, Field(min_length=1, max_length=100)]
    asset_account: Annotated[str | None, Field(max_length=40)] = None  # balance sheet account
    useful_life: Annotated[int | None, Field(ge=1, le=100)] = None  # years
    active: bool = True
    used: bool = False  # read only: investments have it


class CapexSettingsIO(BaseModel):
    """A round's capex planning (setup page)."""

    project_threshold: str | None = None  # above it, plan an investment as a large project
    cash_out: bool = False  # planners schedule when the cash goes out


class CostCenterRefOut(BaseModel):
    code: str
    name: str
    can_write: bool


class CashOutIO(BaseModel):
    period: date
    amount: str


class ItemOut(BaseModel):
    item_id: int
    kind: Kind
    project_id: int | None
    cost_center_code: str
    cost_center_name: str
    name: str
    type_code: str | None
    priority: int  # a project's line: its project's
    start_month: date | None  # a project: its lines' earliest
    amount: str | None  # a project: its lines' sum
    status: Status  # a project's line: its project's
    status_by: str | None
    status_at: datetime | None
    row_version: int
    can_write: bool
    can_confirm: bool
    comments: int
    cash_out: list[CashOutIO]
    scheduled: str  # cash out scheduled; a project: its lines'
    above_threshold: bool  # a single investment better planned as a large project
    lines: int  # a project's lines
    changed_by: str | None
    changed_at: datetime | None


class CapexOut(BaseModel):
    round: Version
    on: bool  # the round plans capex
    settings: CapexSettingsIO
    lock_reason: str | None
    periods: list[date]  # cash-out periods of the fiscal year
    next_year: date  # the period after them
    types: list[CapexTypeIO]
    cost_centers: list[CostCenterRefOut]
    items: list[ItemOut]
    confirms: bool
    created: list[int] = []  # ids of the investments the write added


class NewItemIn(BaseModel):
    kind: Kind = "line"
    project_id: int | None = None
    cost_center: str | None = None
    name: str = ""
    type: str | None = None
    priority: int | None = None
    start_month: date | None = None
    amount: str | None = None


class ItemValuesIn(BaseModel):
    """The fields to change; a field left out stays, null clears it."""

    name: str | None = None
    cost_center: str | None = None
    type: str | None = None
    priority: int | None = None
    start_month: date | None = None
    amount: str | None = None


class ItemChangeIn(BaseModel):
    item_id: int
    expected: int | None = None  # the row version read
    values: ItemValuesIn


class ChangesIn(BaseModel):
    create: list[NewItemIn] = []
    update: list[ItemChangeIn] = []


class IdsIn(BaseModel):
    item_ids: Annotated[list[int], Field(min_length=1)]


class StatusIn(BaseModel):
    item_ids: Annotated[list[int], Field(min_length=1)]
    status: Status
    comment: Annotated[str | None, Field(max_length=2000)] = None


class MoveIn(BaseModel):
    project_id: int | None  # null: out of its project


class CashOutIn(BaseModel):
    periods: list[CashOutIO]


class CommentOut(BaseModel):
    comment_id: int
    author: str
    body: str
    created_at: datetime
    mine: bool


class CommentIn(BaseModel):
    body: Annotated[str, Field(min_length=1, max_length=4000)]


class StatusSumsOut(BaseModel):
    open: str
    confirmed: str
    declined: str


class SumsOut(BaseModel):
    by_priority: list[StatusSumsOut]  # priorities 1, 2, 3
    count: int


class AreaRowOut(BaseModel):
    kind: Literal["node", "cost_center"]
    code: str
    name: str
    depth: int
    sums: SumsOut


class TypeRowOut(BaseModel):
    code: str | None
    name: str
    sums: SumsOut


class CashOutRowOut(BaseModel):
    period: date | None  # null: not scheduled yet
    open: str
    confirmed: str
    declined: str


class CapexOverviewOut(BaseModel):
    round: Version
    on: bool
    cash_out: bool
    areas: list[AreaRowOut]
    types: list[TypeRowOut]
    total: SumsOut
    schedule: list[CashOutRowOut]
    next_year: date
    waiting: int  # open investments the user confirms


def _money(value: Decimal | None) -> str:
    return f"{value or Decimal(0):.2f}"


def _out(v: CapexView, created: list[int] | None = None) -> CapexOut:
    s = v.settings
    return CapexOut(
        round=version_out(v.round),
        on=s is not None,
        settings=CapexSettingsIO(
            project_threshold=_s(s.threshold) if s else None, cash_out=bool(s and s.cash_out)
        ),
        lock_reason=v.lock_reason,
        periods=v.periods,
        next_year=v.next_year,
        types=[CapexTypeIO.model_validate(t, from_attributes=True) for t in v.types],
        cost_centers=[
            CostCenterRefOut.model_validate(c, from_attributes=True) for c in v.cost_centers
        ],
        items=[
            ItemOut(
                **{
                    k: getattr(i, k)
                    for k in ItemOut.model_fields
                    if k not in ("amount", "cash_out", "scheduled")
                },
                amount=_s(i.amount),
                cash_out=[
                    CashOutIO(period=p, amount=_money(a)) for p, a in sorted(i.cash_out.items())
                ],
                scheduled=_money(i.scheduled),
            )
            for i in v.items
        ],
        confirms=v.confirms,
        created=created or [],
    )


def _view(
    request: Request, user: CurrentUser, code: str, created: list[int] | None = None
) -> CapexOut:
    return _out(capex.view_of(engine_of(request), user, code), created)


@router.get("/rounds/{code}/capex", responses=ERRORS)
def capex_list(request: Request, user: CurrentUser, code: str) -> CapexOut:
    try:
        return _view(request, user, code)
    except CAUGHT as e:
        raise _http(e) from None


@router.post("/rounds/{code}/capex/changes", responses=ERRORS)
def capex_changes(request: Request, user: CurrentUser, code: str, body: ChangesIn) -> CapexOut:
    """Add and change investments, all or nothing (a paste is one request)."""
    try:
        creates = [
            capex.NewItem(
                kind=n.kind,
                project_id=n.project_id,
                cost_center=n.cost_center,
                name=n.name,
                type=n.type,
                priority=n.priority,
                start_month=n.start_month,
                amount=_amount(n.amount),
            )
            for n in body.create
        ]
        changes = []
        for c in body.update:
            values: dict[str, object] = {}
            for key in c.values.model_fields_set:
                value = getattr(c.values, key)
                values[key] = _amount(value) if key == "amount" else value
            changes.append(capex.ItemChange(c.item_id, c.expected, values))
        created = capex.apply(engine_of(request), user, code, creates, changes)
        return _view(request, user, code, created)
    except CAUGHT as e:
        raise _http(e) from None


@router.post("/rounds/{code}/capex/delete", responses=ERRORS)
def capex_delete(request: Request, user: CurrentUser, code: str, body: IdsIn) -> CapexOut:
    """Delete investments; a large project goes with its lines."""
    try:
        capex.delete_items(engine_of(request), user, code, body.item_ids)
        return _view(request, user, code)
    except CAUGHT as e:
        raise _http(e) from None


@router.post("/rounds/{code}/capex/items/{item_id}/project", responses=ERRORS)
def capex_make_project(request: Request, user: CurrentUser, code: str, item_id: int) -> CapexOut:
    """Turn a single investment into a large project, the investment its first line."""
    try:
        project = capex.make_project(engine_of(request), user, code, item_id)
        return _view(request, user, code, [project])
    except CAUGHT as e:
        raise _http(e) from None


@router.put("/rounds/{code}/capex/items/{item_id}/project", responses=ERRORS)
def capex_move(
    request: Request, user: CurrentUser, code: str, item_id: int, body: MoveIn
) -> CapexOut:
    """Move an investment into a large project, or out of its project."""
    try:
        capex.move(engine_of(request), user, code, item_id, body.project_id)
        return _view(request, user, code)
    except CAUGHT as e:
        raise _http(e) from None


@router.post("/rounds/{code}/capex/status", responses=ERRORS)
def capex_status(request: Request, user: CurrentUser, code: str, body: StatusIn) -> CapexOut:
    """Confirm, decline or reopen investments and large projects; declining needs a comment."""
    try:
        capex.set_status(engine_of(request), user, code, body.item_ids, body.status, body.comment)
        return _view(request, user, code)
    except CAUGHT as e:
        raise _http(e) from None


@router.put("/rounds/{code}/capex/items/{item_id}/cash-out", responses=ERRORS)
def capex_cash_out(
    request: Request, user: CurrentUser, code: str, item_id: int, body: CashOutIn
) -> CapexOut:
    """Replace an investment's cash-out schedule; it never exceeds the amount."""
    try:
        schedule: dict[date, Decimal] = {}
        for p in body.periods:
            amount = _amount(p.amount)
            if amount is None or p.period in schedule:
                raise CapexError(f"one amount per period: {p.period:%Y-%m}")
            schedule[p.period] = amount
        capex.set_cash_out(engine_of(request), user, code, item_id, schedule)
        return _view(request, user, code)
    except CAUGHT as e:
        raise _http(e) from None


@router.get("/rounds/{code}/capex/items/{item_id}/comments", responses=ERRORS)
def capex_comments(
    request: Request, user: CurrentUser, code: str, item_id: int
) -> list[CommentOut]:
    try:
        return [
            CommentOut.model_validate(c, from_attributes=True)
            for c in capex.comments(engine_of(request), user, code, item_id)
        ]
    except CAUGHT as e:
        raise _http(e) from None


@router.post("/rounds/{code}/capex/items/{item_id}/comments", responses=ERRORS)
def capex_add_comment(
    request: Request, user: CurrentUser, code: str, item_id: int, body: CommentIn
) -> list[CommentOut]:
    try:
        capex.add_comment(engine_of(request), user, code, item_id, body.body)
        return capex_comments(request, user, code, item_id)
    except CAUGHT as e:
        raise _http(e) from None


@router.delete("/rounds/{code}/capex/items/{item_id}/comments/{comment_id}", responses=ERRORS)
def capex_delete_comment(
    request: Request, user: CurrentUser, code: str, item_id: int, comment_id: int
) -> list[CommentOut]:
    try:
        capex.delete_comment(engine_of(request), user, code, item_id, comment_id)
        return capex_comments(request, user, code, item_id)
    except CAUGHT as e:
        raise _http(e) from None


def _sums(s: Sums) -> SumsOut:
    return SumsOut(
        by_priority=[
            StatusSumsOut(
                open=_money(s.get(p, "open")),
                confirmed=_money(s.get(p, "confirmed")),
                declined=_money(s.get(p, "declined")),
            )
            for p in (1, 2, 3)
        ],
        count=s.count,
    )


@router.get("/rounds/{code}/capex/overview", responses=ERRORS)
def capex_overview(request: Request, user: CurrentUser, code: str) -> CapexOverviewOut:
    """Amounts by priority and confirmation per area and per type; cash out per period."""
    try:
        o = capex.overview(engine_of(request), user, code)
    except CAUGHT as e:
        raise _http(e) from None
    return CapexOverviewOut(
        round=version_out(o.round),
        on=o.on,
        cash_out=o.cash_out,
        areas=[
            AreaRowOut(kind=a.kind, code=a.code, name=a.name, depth=a.depth, sums=_sums(a.sums))  # type: ignore[arg-type]
            for a in o.areas
        ],
        types=[TypeRowOut(code=t.code, name=t.name, sums=_sums(t.sums)) for t in o.types],
        total=_sums(o.total),
        schedule=[
            CashOutRowOut(
                period=c.period,
                open=_money(c.by_status["open"]),
                confirmed=_money(c.by_status["confirmed"]),
                declined=_money(c.by_status["declined"]),
            )
            for c in o.schedule
        ],
        next_year=o.next_year,
        waiting=o.waiting,
    )


@router.get("/capex-types", responses={401: {}})
def capex_types(request: Request, user: CurrentUser) -> list[CapexTypeIO]:
    return [
        CapexTypeIO.model_validate(t, from_attributes=True)
        for t in capex.types(engine_of(request), user)
    ]


@router.put("/capex-types", responses=ERRORS)
def save_capex_types(
    request: Request, user: CurrentUser, body: list[CapexTypeIO]
) -> list[CapexTypeIO]:
    """Replace the installation's investment types (controllers); a used one can't be removed."""
    try:
        saved = capex.save_types(
            engine_of(request),
            user,
            [
                capex.CapexType(t.code, t.name, t.asset_account, t.useful_life, t.active)
                for t in body
            ],
        )
    except CAUGHT as e:
        raise _http(e) from None
    return [CapexTypeIO.model_validate(t, from_attributes=True) for t in saved]
