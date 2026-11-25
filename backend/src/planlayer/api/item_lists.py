"""Item list endpoints (ADR 0017): the lists a user can see, one list with its items, creating,
saving and deleting lists, and the lists as an Excel workbook. Amounts are decimal strings; the
server computes them."""

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from planlayer import item_list_files, item_lists
from planlayer.access import AccessError
from planlayer.api.auth import CurrentUser, engine_of
from planlayer.api.limits import Code, Number
from planlayer.rounds import NotFoundError

router = APIRouter(prefix="/api/item-lists")

Visibility = Literal["private", "cost_center", "public"]
Kind = Literal["item", "heading"]
ExportScope = Literal["mine", "visible"]
HANDLED = (AccessError, NotFoundError, item_lists.ListError)


def _http(e: Exception) -> HTTPException:
    match e:
        case AccessError():
            return HTTPException(403, str(e))
        case NotFoundError():
            return HTTPException(404, str(e))
        case item_lists.ListError():
            return HTTPException(422, str(e))
    raise e


class ItemIO(BaseModel):
    # A category heading has a name only and groups the items below it, up to the next heading.
    kind: Kind = "item"
    name: Annotated[str, Field(max_length=300)]
    supplier: Annotated[str | None, Field(max_length=300)] = None
    quantity: Number = "1"
    unit_price: Number | None = None  # required for an item, none for a heading
    amount: Number | None = None  # computed by the server; ignored on input
    comment: Annotated[str | None, Field(max_length=2000)] = None


class ListSummaryOut(BaseModel):
    list_id: int
    cost_center_code: str
    cost_center_name: str
    account_code: str
    account_name: str
    owner_login: str
    owner_name: str
    visibility: Visibility
    total: str
    items: int
    changed_at: datetime
    changed_by: str
    mine: bool  # owned by the user: they change visibility and delete it
    can_edit: bool


class ItemListOut(ListSummaryOut):
    entries: list[ItemIO]


class NewListIn(BaseModel):
    cost_center: Code
    account: Code
    visibility: Visibility = "private"
    items: Annotated[list[ItemIO], Field(max_length=item_lists.MAX_ITEMS)] = []


class SaveListIn(BaseModel):
    visibility: Visibility
    items: Annotated[list[ItemIO], Field(max_length=item_lists.MAX_ITEMS)]


def _summary(s: item_lists.ItemList) -> ListSummaryOut:
    return ListSummaryOut(
        list_id=s.list_id,
        cost_center_code=s.cost_center_code,
        cost_center_name=s.cost_center_name,
        account_code=s.account_code,
        account_name=s.account_name,
        owner_login=s.owner_login,
        owner_name=s.owner_name,
        visibility=s.visibility,
        total=f"{s.total:.2f}",
        items=s.items,
        changed_at=s.changed_at,
        changed_by=s.changed_by,
        mine=s.mine,
        can_edit=s.can_edit,
    )


def _full(s: item_lists.ItemList) -> ItemListOut:
    return ItemListOut(
        **_summary(s).model_dump(),
        entries=[
            ItemIO(
                kind=i.kind,
                name=i.name,
                supplier=i.supplier,
                quantity=f"{i.quantity.normalize():f}",
                unit_price=None if i.unit_price is None else f"{i.unit_price:.2f}",
                amount=None if i.amount is None else f"{i.amount:.2f}",
                comment=i.comment,
            )
            for i in s.entries
        ],
    )


def _items(items: list[ItemIO]) -> list[item_lists.ItemIn]:
    return [
        item_lists.ItemIn(i.name, i.unit_price, i.quantity, i.supplier, i.comment, i.kind)
        for i in items
    ]


@router.get("")
def lists(request: Request, user: CurrentUser) -> list[ListSummaryOut]:
    """Every list the user can see, most recently changed first."""
    return [_summary(s) for s in item_lists.lists(engine_of(request), user)]


@router.get("/line", responses={404: {}})
def list_of_line(
    request: Request, user: CurrentUser, cost_center: Code, account: Code
) -> ItemListOut | None:
    """The line's list, if there is one the user can see."""
    try:
        found = item_lists.list_of_line(engine_of(request), user, cost_center, account)
    except HANDLED as e:
        raise _http(e) from None
    return None if found is None else _full(found)


@router.get("/export.xlsx")
def export(request: Request, user: CurrentUser, scope: ExportScope = "visible") -> Response:
    """The user's own lists (``mine``) or every list they can see (``visible``) as an Excel
    workbook: an overview, then one sheet per list, by cost center and account."""
    found = item_lists.lists(engine_of(request), user)
    chosen = sorted(
        (s for s in found if scope == "visible" or s.mine),
        key=lambda s: (s.cost_center_code, s.account_code),
    )
    return Response(
        item_list_files.workbook(chosen),
        media_type=item_list_files.MEDIA,
        headers={"Content-Disposition": f'attachment; filename="item-lists-{scope}.xlsx"'},
    )


@router.get("/{list_id}", responses={404: {}})
def get_list(request: Request, user: CurrentUser, list_id: int) -> ItemListOut:
    try:
        return _full(item_lists.get_list(engine_of(request), user, list_id))
    except HANDLED as e:
        raise _http(e) from None


@router.post("", responses={403: {}, 404: {}, 422: {}})
def create_list(request: Request, user: CurrentUser, body: NewListIn) -> ItemListOut:
    engine = engine_of(request)
    try:
        list_id = item_lists.create_list(
            engine, user, body.cost_center, body.account, body.visibility, _items(body.items)
        )
        return _full(item_lists.get_list(engine, user, list_id))
    except HANDLED as e:
        raise _http(e) from None


@router.put("/{list_id}", responses={403: {}, 404: {}, 422: {}})
def save_list(request: Request, user: CurrentUser, list_id: int, body: SaveListIn) -> ItemListOut:
    """Replace the list's items (all or nothing); only the owner changes visibility."""
    engine = engine_of(request)
    try:
        item_lists.save_list(engine, user, list_id, body.visibility, _items(body.items))
        return _full(item_lists.get_list(engine, user, list_id))
    except HANDLED as e:
        raise _http(e) from None


@router.delete("/{list_id}", responses={403: {}, 404: {}})
def delete_list(request: Request, user: CurrentUser, list_id: int) -> None:
    try:
        item_lists.delete_list(engine_of(request), user, list_id)
    except HANDLED as e:
        raise _http(e) from None
