"""Item lists (ADR 0017): the items behind a cost center x account's value, kept outside rounds.

A line has at most one list. Its owner chooses who sees it: only the owner (``private``),
everyone who may plan the cost center (``cost_center``), or everyone who may read it
(``public``, written by those who plan it). Row-level security enforces this; a trigger keeps
visibility the owner's to change. An item's amount is quantity x unit price, computed by the
database; the list total is the sum of the amounts. The total reaches the plan only when someone
applies it to the line, as a typed year total.

Items are kept in the order sent. A category heading has a name only and groups the items below
it, up to the next heading; items above the first heading belong to none.

Its owner moves a list to another line of a cost center they plan; a controller takes over a list
whose owner is disabled (ADR 0036). Against a round, a list's line may not be planned: its cost
center or account isn't planned there, or isn't delivered any more. The list stays as it is.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Literal

from sqlalchemy import Connection, Engine, delete, func, insert, select, update
from sqlalchemy.exc import DBAPIError, IntegrityError

from planlayer.access import AccessError, User, cost_center_rights
from planlayer.rounds import NotFoundError, RoundRef, account_scopes, get_round
from planlayer.sheet import fits
from planlayer.store.schema import (
    ACCOUNT,
    COST_CENTER,
    MODULE_COST_CENTER,
    app_user,
    cost_center_access,
    item,
    item_list,
    member,
    not_planned_account,
    round_member,
)
from planlayer.store.uow import system_rights, unit_of_work

Visibility = Literal["private", "cost_center", "public"]
VISIBILITIES: tuple[Visibility, ...] = ("private", "cost_center", "public")
Kind = Literal["item", "heading"]
GUARD_SQLSTATE = "PL002"  # raised by the list's guard trigger
# Why a list's line isn't planned in a round.
Unplaced = Literal[
    "cost_center_gone",  # the source no longer delivers the cost center
    "account_gone",
    "account_group",  # the account has become a group
    "cost_center_not_planned",
    "account_not_planned",
    "outside_account_set",
]
MAX_ITEMS = 2_000


class ListError(ValueError):
    """An item list change that can't be made as sent."""


@dataclass
class ItemIn:
    name: str
    unit_price: str | None  # None for a heading
    quantity: str = "1"
    supplier: str | None = None
    comment: str | None = None
    kind: Kind = "item"


@dataclass
class Item:
    name: str
    supplier: str | None
    quantity: Decimal
    unit_price: Decimal | None
    amount: Decimal | None
    comment: str | None
    kind: Kind


@dataclass
class ListSummary:
    list_id: int
    cost_center_code: str
    cost_center_name: str
    account_code: str
    account_name: str
    owner_login: str
    owner_name: str
    visibility: Visibility
    total: Decimal
    items: int
    changed_at: datetime
    changed_by: str
    mine: bool  # the user owns it: changes visibility, moves it, deletes it
    can_edit: bool
    unplaced: Unplaced | None = None  # against the round asked for; None: planned there
    owner_disabled: bool = False
    can_take_over: bool = False  # a controller, and the owner is disabled


@dataclass
class ItemList(ListSummary):
    entries: list[Item] = field(default_factory=list)


def _decimal(value: str, what: str, places: int) -> Decimal:
    try:
        amount = Decimal(value.strip().replace(",", "."))
    except InvalidOperation:
        raise ListError(f"not a {what}: {value!r}") from None
    if not amount.is_finite():
        raise ListError(f"not a {what}: {value!r}")
    exponent = amount.as_tuple().exponent
    if isinstance(exponent, int) and exponent < -places:
        raise ListError(f"{what} {value} has more than {places} decimal places")
    if not fits(amount, 18, places):
        raise ListError(f"{what} {value} is too large")
    return amount


def _rows(items: list[ItemIn]) -> list[dict[str, object]]:
    if len(items) > MAX_ITEMS:
        raise ListError(f"a list has at most {MAX_ITEMS} items")
    rows: list[dict[str, object]] = []
    for position, it in enumerate(items, start=1):
        name = it.name.strip()
        supplier = (it.supplier or "").strip() or None
        price = (it.unit_price or "").strip()
        comment = (it.comment or "").strip() or None
        quantity = _decimal(it.quantity or "1", "quantity", 4)
        match it.kind:
            case "heading":
                if not name:
                    raise ListError(f"category {position} needs a name")
                if price or supplier or comment or quantity != 1:
                    raise ListError(f"category {name!r} takes a name only")
                # Every column, as a multi-row insert takes its columns from the first row.
                rows.append(
                    {
                        "position": position,
                        "kind": "heading",
                        "name": name,
                        "supplier": None,
                        "quantity": Decimal(1),
                        "unit_price": None,
                        "comment": None,
                    }
                )
            case "item":
                if not name:
                    raise ListError(f"item {position} needs a name")
                if not price:
                    raise ListError(f"item {name!r} needs a unit price")
                rows.append(
                    {
                        "position": position,
                        "kind": "item",
                        "name": name,
                        "supplier": supplier,
                        "quantity": quantity,
                        "unit_price": _decimal(price, "unit price", 2),
                        "comment": comment,
                    }
                )
            case _:
                raise ListError(f"unknown kind {it.kind}")
    return rows


def _leaf(conn: Connection, dim: int, code: str, what: str) -> int:
    found = conn.execute(
        select(member.c.member_id).where(
            member.c.dim_id == dim, member.c.code == code, member.c.is_group.is_(False)
        )
    ).scalar()
    if found is None:
        raise NotFoundError(f"{what} {code} not found")
    return int(found)


def _unplaced(conn: Connection, r: RoundRef, lists: list[object]) -> dict[int, Unplaced]:
    """Why each list's line isn't planned in the round, for those it isn't."""
    rows = [lst._mapping for lst in lists]  # type: ignore[attr-defined]
    enabled = set(
        conn.execute(
            select(round_member.c.member_id).where(
                round_member.c.version_id == r.version_id,
                round_member.c.module == MODULE_COST_CENTER,
                round_member.c.enabled.is_(True),
            )
        ).scalars()
    )
    not_planned = set(
        conn.execute(
            select(not_planned_account.c.account_id).where(
                not_planned_account.c.version_id == r.version_id,
                not_planned_account.c.module == MODULE_COST_CENTER,
            )
        ).scalars()
    )
    sets = account_scopes(conn, r.version_id, {row["cost_center_id"] for row in rows})
    out: dict[int, Unplaced] = {}
    for row in rows:
        cc, acc = row["cost_center_id"], row["account_id"]
        reason: Unplaced | None = None
        if row["cc_gone"] is not None:
            reason = "cost_center_gone"
        elif row["acc_gone"] is not None:
            reason = "account_gone"
        elif row["acc_group"]:
            reason = "account_group"
        elif cc not in enabled:
            reason = "cost_center_not_planned"
        elif acc in not_planned:
            reason = "account_not_planned"
        elif (scope := sets.get(cc)) is not None and acc not in scope:
            reason = "outside_account_set"
        if reason is not None:
            out[row["list_id"]] = reason
    return out


def _summaries(
    conn: Connection, user: User, list_ids: list[int] | None, r: RoundRef | None = None
) -> list[ItemList]:
    """Visible lists (all, or the given ones) with their items, newest change first; with a
    round, whether each list's line is planned there."""
    cc, acc, owner, changer = (
        member.alias("cc"),
        member.alias("acc"),
        app_user.alias("owner"),
        app_user.alias("changer"),
    )
    query = (
        select(
            item_list,
            cc.c.code.label("cc_code"),
            cc.c.name.label("cc_name"),
            acc.c.code.label("acc_code"),
            acc.c.name.label("acc_name"),
            owner.c.login.label("owner_login"),
            owner.c.display_name.label("owner_name"),
            owner.c.disabled.label("owner_disabled"),
            changer.c.display_name.label("changer_name"),
            cc.c.missing_since.label("cc_gone"),
            acc.c.missing_since.label("acc_gone"),
            acc.c.is_group.label("acc_group"),
        )
        .join(cc, cc.c.member_id == item_list.c.cost_center_id)
        .join(acc, acc.c.member_id == item_list.c.account_id)
        .join(owner, owner.c.user_id == item_list.c.owner_id)
        .join(changer, changer.c.user_id == item_list.c.changed_by)
        .order_by(item_list.c.changed_at.desc())
    )
    if list_ids is not None:
        query = query.where(item_list.c.list_id.in_(list_ids))
    lists = conn.execute(query).all()
    entries: dict[int, list[Item]] = {}
    for row in conn.execute(
        select(item)
        .where(item.c.list_id.in_([lst.list_id for lst in lists]))
        .order_by(item.c.list_id, item.c.position)
    ):
        entries.setdefault(row.list_id, []).append(
            Item(
                row.name,
                row.supplier,
                row.quantity,
                row.unit_price,
                row.amount,
                row.comment,
                row.kind,
            )
        )
    plans = {c for c, w in cost_center_rights(conn, user).items() if w}
    unplaced = _unplaced(conn, r, list(lists)) if r is not None else {}
    out = []
    for lst in lists:
        mine = lst.owner_id == user.user_id
        items = entries.get(lst.list_id, [])
        out.append(
            ItemList(
                lst.list_id,
                lst.cc_code,
                lst.cc_name,
                lst.acc_code,
                lst.acc_name,
                lst.owner_login,
                lst.owner_name,
                lst.visibility,
                sum((i.amount for i in items if i.amount is not None), Decimal(0)),
                sum(1 for i in items if i.kind == "item"),
                lst.changed_at,
                lst.changer_name,
                mine,
                mine or (lst.visibility != "private" and lst.cost_center_id in plans),
                entries=items,
                unplaced=unplaced.get(lst.list_id),
                owner_disabled=lst.owner_disabled,
                can_take_over=user.is_controller and lst.owner_disabled,
            )
        )
    return out


def lists(engine: Engine, user: User, round_code: str | None = None) -> list[ItemList]:
    """Every list the user can see; with a round, whether each one's line is planned there."""
    with unit_of_work(engine, user) as conn:
        r = get_round(conn, round_code) if round_code is not None else None
        return _summaries(conn, user, None, r)


def get_list(engine: Engine, user: User, list_id: int) -> ItemList:
    with unit_of_work(engine, user) as conn:
        found = _summaries(conn, user, [list_id])
    if not found:
        raise NotFoundError(f"item list {list_id} not found")
    return found[0]


def list_of_line(engine: Engine, user: User, cc_code: str, account_code: str) -> ItemList | None:
    """The line's list, if there is one the user can see."""
    with unit_of_work(engine, user) as conn:
        cc = _leaf(conn, COST_CENTER, cc_code, "cost center")
        acc = _leaf(conn, ACCOUNT, account_code, "account")
        list_id = conn.execute(
            select(item_list.c.list_id).where(
                item_list.c.cost_center_id == cc, item_list.c.account_id == acc
            )
        ).scalar()
        return None if list_id is None else _summaries(conn, user, [list_id])[0]


def markers(conn: Connection, cost_center_id: int) -> dict[int, tuple[int, Decimal, str]]:
    """Per account of the cost center, the visible list: id, total and visibility."""
    totals = (
        select(item.c.list_id, func.coalesce(func.sum(item.c.amount), 0).label("total"))
        .group_by(item.c.list_id)
        .subquery()
    )
    return {
        account: (list_id, total, visibility)
        for account, list_id, total, visibility in conn.execute(
            select(
                item_list.c.account_id,
                item_list.c.list_id,
                func.coalesce(totals.c.total, 0),
                item_list.c.visibility,
            )
            .outerjoin(totals, totals.c.list_id == item_list.c.list_id)
            .where(item_list.c.cost_center_id == cost_center_id)
        )
    }


def create_list(
    engine: Engine, user: User, cc_code: str, account_code: str, visibility: Visibility,
    items: list[ItemIn],
) -> int:  # fmt: skip
    """A list on a line, owned by the user, who must be able to plan the cost center."""
    if visibility not in VISIBILITIES:
        raise ListError(f"unknown visibility {visibility}")
    rows = _rows(items)
    try:
        with unit_of_work(engine, user, uuid.uuid4().hex) as conn:
            cc = _leaf(conn, COST_CENTER, cc_code, "cost center")
            acc = _leaf(conn, ACCOUNT, account_code, "account")
            rights = cost_center_rights(conn, user)
            if cc not in rights:
                raise NotFoundError(f"cost center {cc_code} not found")
            if not rights[cc]:
                raise AccessError("only those who may plan the cost center keep item lists on it")
            list_id = int(
                conn.execute(
                    insert(item_list)
                    .values(
                        cost_center_id=cc,
                        account_id=acc,
                        owner_id=user.user_id,
                        visibility=visibility,
                        changed_by=user.user_id,
                    )
                    .returning(item_list.c.list_id)
                ).scalar_one()
            )
            if rows:
                conn.execute(insert(item), [{**r, "list_id": list_id} for r in rows])
            return list_id
    except IntegrityError as e:
        if "uq_item_list" in str(e.orig) or "item_list_cost_center_id_account_id" in str(e.orig):
            raise ListError(
                f"{cc_code} / {account_code} has an item list already, maybe one kept private "
                "by its owner"
            ) from None
        raise


def save_list(
    engine: Engine, user: User, list_id: int, visibility: Visibility, items: list[ItemIn]
) -> None:
    """Replace the list's items, all or nothing; only its owner changes visibility."""
    if visibility not in VISIBILITIES:
        raise ListError(f"unknown visibility {visibility}")
    rows = _rows(items)
    try:
        with unit_of_work(engine, user, uuid.uuid4().hex) as conn:
            changed = conn.execute(
                update(item_list)
                .where(item_list.c.list_id == list_id)
                .values(visibility=visibility, changed_by=user.user_id, changed_at=func.now())
                .returning(item_list.c.list_id)
            ).first()
            if changed is None:  # not visible, or visible but not writable
                visible = conn.execute(
                    select(item_list.c.list_id).where(item_list.c.list_id == list_id)
                ).first()
                if visible is None:
                    raise NotFoundError(f"item list {list_id} not found")
                raise AccessError("you may read this item list, not change it")
            conn.execute(delete(item).where(item.c.list_id == list_id))
            if rows:
                conn.execute(insert(item), [{**r, "list_id": list_id} for r in rows])
    except DBAPIError as e:
        if getattr(e.orig, "sqlstate", None) == GUARD_SQLSTATE:
            raise AccessError("only the list's owner changes its visibility") from None
        raise


def delete_list(engine: Engine, user: User, list_id: int) -> None:
    with unit_of_work(engine, user, uuid.uuid4().hex) as conn:
        lst = conn.execute(
            select(item_list.c.owner_id).where(item_list.c.list_id == list_id)
        ).first()
        if lst is None:
            raise NotFoundError(f"item list {list_id} not found")
        if lst.owner_id != user.user_id:
            raise AccessError("only the list's owner deletes it")
        conn.execute(delete(item_list).where(item_list.c.list_id == list_id))


def _guarded(e: DBAPIError) -> AccessError | None:
    if getattr(e.orig, "sqlstate", None) == GUARD_SQLSTATE:
        return AccessError(str(e.orig).splitlines()[0])
    return None


def move_list(engine: Engine, user: User, list_id: int, cc_code: str, account_code: str) -> None:
    """Its owner moves a list to another line of a cost center they plan; the line must have no
    list yet. Items, visibility and owner stay; the plan values of both lines stay as they are."""
    try:
        with unit_of_work(engine, user, uuid.uuid4().hex) as conn:
            lst = conn.execute(
                select(item_list.c.owner_id).where(item_list.c.list_id == list_id)
            ).first()
            if lst is None:
                raise NotFoundError(f"item list {list_id} not found")
            if lst.owner_id != user.user_id:
                raise AccessError("only the list's owner moves it")
            cc = _leaf(conn, COST_CENTER, cc_code, "cost center")
            acc = _leaf(conn, ACCOUNT, account_code, "account")
            if not cost_center_rights(conn, user).get(cc, False):
                raise AccessError("a list moves only to a cost center you plan")
            conn.execute(
                update(item_list)
                .where(item_list.c.list_id == list_id)
                .values(
                    cost_center_id=cc,
                    account_id=acc,
                    changed_by=user.user_id,
                    changed_at=func.now(),
                )
            )
    except IntegrityError as e:
        if "uq_item_list" in str(e.orig):
            raise ListError(
                f"{cc_code} / {account_code} has an item list already, maybe one kept private "
                "by its owner"
            ) from None
        raise
    except DBAPIError as e:
        guarded = _guarded(e)
        if guarded is None:
            raise
        raise guarded from None


@dataclass
class Candidate:
    login: str
    name: str


def owner_candidates(engine: Engine, user: User, list_id: int) -> list[Candidate]:
    """Who a controller may make the new owner of a disabled user's list: themself, or an active
    user who plans its cost center."""
    user.require_controller()
    with unit_of_work(engine, user) as conn:
        lst = conn.execute(
            select(item_list.c.cost_center_id, app_user.c.disabled)
            .join(app_user, app_user.c.user_id == item_list.c.owner_id)
            .where(item_list.c.list_id == list_id)
        ).first()
        if lst is None:
            raise NotFoundError(f"item list {list_id} not found")
        if not lst.disabled:
            raise ListError("its owner is active: only the owner hands a list on")
        # Every user's rights on the cost center: a question across users.
        with system_rights(conn, actor=user.login):
            found = conn.execute(
                select(app_user.c.login, app_user.c.display_name)
                .join(cost_center_access, cost_center_access.c.user_id == app_user.c.user_id)
                .where(
                    cost_center_access.c.cost_center_id == lst.cost_center_id,
                    cost_center_access.c.can_write.is_(True),
                    app_user.c.disabled.is_(False),
                    app_user.c.is_system.is_(False),
                )
                .distinct()
            ).all()
    people = {login: name for login, name in found}
    people[user.login] = user.display_name
    return sorted((Candidate(k, v) for k, v in people.items()), key=lambda c: c.name.lower())


def take_over(engine: Engine, user: User, list_id: int, login: str) -> None:
    """A controller makes themself, or someone who plans the list's cost center, the owner of a
    disabled user's list. The database refuses anything else."""
    user.require_controller()
    try:
        with unit_of_work(engine, user, uuid.uuid4().hex) as conn:
            new_owner = conn.execute(
                select(app_user.c.user_id).where(app_user.c.login == login)
            ).scalar()
            if new_owner is None:
                raise NotFoundError(f"user {login} not found")
            changed = conn.execute(
                update(item_list)
                .where(item_list.c.list_id == list_id)
                .values(owner_id=new_owner, changed_by=user.user_id, changed_at=func.now())
                .returning(item_list.c.list_id)
            ).first()
            if changed is None:
                raise NotFoundError(f"item list {list_id} not found")
    except DBAPIError as e:
        guarded = _guarded(e)
        if guarded is None:
            raise
        raise guarded from None
