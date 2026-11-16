"""A round's investments (ADR 0028): one list over every cost center the user reads, written by
those who plan them, confirmed by approvers above them and controllers.

Every write runs in one transaction, all or nothing. The database enforces who writes and who
confirms, the round's lock, that a change of what was confirmed opens it again, and that cash
out never exceeds the investment; the checks here give clear errors first."""

from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Literal, cast

from sqlalchemy import Connection, Engine, delete, func, insert, select, update
from sqlalchemy.exc import DBAPIError

from planlayer.access import AccessError, User, cost_center_rights
from planlayer.capex.common import (
    CapexSettings,
    CapexType,
    _types,
    periods_of,
    refusal,
    settings_of,
)
from planlayer.domain.capex import (
    PRIORITIES,
    CapexError,
    Status,
    above_threshold,
    check_schedule,
    trimmed,
)
from planlayer.rounds import RoundRef, get_round, not_open
from planlayer.sheet import Conflict, ConflictError
from planlayer.store.schema import (
    COST_CENTER,
    MODULE_COST_CENTER,
    app_user,
    capex_cash_out,
    capex_comment,
    capex_confirmer,
    capex_item,
    capex_type,
    member,
    round_member,
)
from planlayer.store.uow import unit_of_work

Kind = Literal["line", "project"]
FIELDS = ("name", "cost_center", "type", "priority", "start_month", "amount")
DEFAULT_PRIORITY = 2


@dataclass
class CostCenterRef:
    code: str
    name: str
    can_write: bool


@dataclass
class Item:
    item_id: int
    kind: Kind
    project_id: int | None
    cost_center_code: str
    cost_center_name: str
    name: str
    type_code: str | None
    # A project's line shows its project's priority and confirmation; a project shows its lines'
    # earliest start and their sum.
    priority: int
    start_month: date | None
    amount: Decimal | None
    status: Status
    status_by: str | None
    status_at: datetime | None
    row_version: int
    can_write: bool
    can_confirm: bool
    comments: int
    cash_out: dict[date, Decimal] = field(default_factory=dict)
    scheduled: Decimal = Decimal(0)
    above_threshold: bool = False
    lines: int = 0
    changed_by: str | None = None
    changed_at: datetime | None = None


@dataclass
class CapexView:
    round: RoundRef
    settings: CapexSettings | None  # None: the round doesn't plan capex
    lock_reason: str | None
    periods: list[date]  # cash-out periods of the fiscal year
    next_year: date  # the one period after them
    types: list[CapexType]
    cost_centers: list[CostCenterRef]  # planned in the round, readable by the user
    items: list[Item]
    confirms: bool  # the user confirms investments of some cost center


def _lock_reason(r: RoundRef, user: User) -> str | None:
    if r.status == "open" or (r.status == "setup" and user.is_controller):
        return None
    return not_open(r.status)


def _confirms(conn: Connection, user: User) -> set[int]:
    return set(
        conn.execute(
            select(capex_confirmer.c.cost_center_id).where(
                capex_confirmer.c.user_id == user.user_id
            )
        ).scalars()
    )


def _planned(conn: Connection, version_id: int) -> set[int]:
    return set(
        conn.execute(
            select(round_member.c.member_id).where(
                round_member.c.version_id == version_id,
                round_member.c.module == MODULE_COST_CENTER,
                round_member.c.enabled.is_(True),
            )
        ).scalars()
    )


def waiting_for(conn: Connection, user: User, version_id: int) -> int:
    """Open investments and large projects of the round that the user confirms (Home)."""
    return int(
        conn.execute(
            select(func.count())
            .select_from(capex_item)
            .where(
                capex_item.c.version_id == version_id,
                capex_item.c.project_id.is_(None),
                capex_item.c.status == "open",
                capex_item.c.cost_center_id.in_(
                    select(capex_confirmer.c.cost_center_id).where(
                        capex_confirmer.c.user_id == user.user_id
                    )
                ),
            )
        ).scalar_one()
    )


def view_of(engine: Engine, user: User, code: str) -> CapexView:
    with unit_of_work(engine, user) as conn:
        r = get_round(conn, code)
        settings = settings_of(conn, r.version_id)
        periods, next_year = periods_of(conn, r)
        rights = cost_center_rights(conn, user)
        planned = _planned(conn, r.version_id)
        names = {
            m.member_id: (m.code, m.name)
            for m in conn.execute(
                select(member.c.member_id, member.c.code, member.c.name).where(
                    member.c.dim_id == COST_CENTER, member.c.member_id.in_(set(rights))
                )
            )
        }
        ccs = sorted(
            (CostCenterRef(*names[cc], rights[cc]) for cc in planned if cc in names),
            key=lambda c: c.code,
        )
        confirmable = _confirms(conn, user)
        items = _items(conn, r.version_id, rights, confirmable, settings) if settings else []
        return CapexView(
            r,
            settings,
            _lock_reason(r, user),
            periods,
            next_year,
            _types(conn),
            ccs,
            items,
            bool(confirmable & planned),
        )


def _items(
    conn: Connection,
    version_id: int,
    rights: dict[int, bool],
    confirmable: set[int],
    settings: CapexSettings,
) -> list[Item]:
    i, cc = capex_item, member.alias("cc")
    comments = (
        select(capex_comment.c.item_id, func.count().label("n"))
        .group_by(capex_comment.c.item_id)
        .subquery()
    )
    rows = conn.execute(
        select(
            i,
            cc.c.code.label("cc_code"),
            cc.c.name.label("cc_name"),
            capex_type.c.code.label("type_code"),
            func.coalesce(comments.c.n, 0).label("comments"),
        )
        .join(cc, cc.c.member_id == i.c.cost_center_id)
        .outerjoin(capex_type, capex_type.c.type_id == i.c.type_id)
        .outerjoin(comments, comments.c.item_id == i.c.item_id)
        .where(i.c.version_id == version_id)
        .order_by(i.c.item_id)
    ).all()
    schedule: dict[int, dict[date, Decimal]] = defaultdict(dict)
    if settings.cash_out:
        for item_id, period, amount in conn.execute(
            select(capex_cash_out.c.item_id, capex_cash_out.c.period, capex_cash_out.c.amount)
            .join(i, i.c.item_id == capex_cash_out.c.item_id)
            .where(i.c.version_id == version_id)
            .order_by(capex_cash_out.c.period)
        ):
            schedule[item_id][period] = amount
    by_id = {row.item_id: row for row in rows}
    lines: dict[int, list[int]] = defaultdict(list)
    for row in rows:
        if row.project_id is not None:
            lines[row.project_id].append(row.item_id)

    def item(row: object) -> Item:
        r = row._mapping  # type: ignore[attr-defined]
        owner = by_id.get(r["project_id"]) if r["project_id"] is not None else None
        head = owner._mapping if owner is not None else r
        plan = schedule.get(r["item_id"], {})
        out = Item(
            item_id=r["item_id"],
            kind=r["kind"],
            project_id=r["project_id"],
            cost_center_code=r["cc_code"],
            cost_center_name=r["cc_name"],
            name=r["name"],
            type_code=r["type_code"],
            priority=head["priority"],
            start_month=r["start_month"],
            amount=r["amount"],
            status=head["status"],
            status_by=head["status_by"],
            status_at=head["status_at"],
            row_version=r["row_version"],
            can_write=rights.get(r["cost_center_id"], False),
            can_confirm=r["cost_center_id"] in confirmable,
            comments=r["comments"],
            cash_out=plan,
            scheduled=sum(plan.values(), Decimal(0)),
            above_threshold=r["kind"] == "line"
            and r["project_id"] is None
            and above_threshold(r["amount"], settings.threshold),
            changed_by=r["changed_by"],
            changed_at=r["changed_at"],
        )
        if out.kind == "project":
            mine = [by_id[x] for x in lines.get(out.item_id, [])]
            amounts = [m.amount for m in mine if m.amount is not None]
            starts = [m.start_month for m in mine if m.start_month is not None]
            out.amount = sum(amounts, Decimal(0)) if amounts else None
            out.start_month = min(starts) if starts else None
            out.lines = len(mine)
            out.scheduled = sum(
                (sum(schedule.get(m.item_id, {}).values(), Decimal(0)) for m in mine), Decimal(0)
            )
        return out

    ordered: list[Item] = []
    for row in rows:
        if row.project_id is None:
            ordered.append(item(row))
            ordered += [item(by_id[x]) for x in lines.get(row.item_id, [])]
    return ordered


# --- Writes --------------------------------------------------------------------------------------


@dataclass
class NewItem:
    kind: Kind = "line"
    project_id: int | None = None  # a line of this large project
    cost_center: str | None = None  # a project's line takes its project's
    name: str = ""
    type: str | None = None
    priority: int | None = None
    start_month: date | None = None
    amount: Decimal | None = None


@dataclass
class ItemChange:
    item_id: int
    expected: int | None  # the row version read; None: don't check
    values: dict[str, object]  # among FIELDS


@contextmanager
def _writing(engine: Engine, user: User, code: str) -> Iterator[tuple[Connection, RoundRef]]:
    """A write to a round's investments: the round shared-locked, capex on, refusals of the
    database as clear errors."""
    try:
        with unit_of_work(engine, user) as conn:
            r = get_round(conn, code, lock="share")
            if settings_of(conn, r.version_id) is None:
                raise CapexError("this round doesn't plan capex")
            if (reason := _lock_reason(r, user)) is not None:
                raise CapexError(f"investments can't be changed: {reason}")
            yield conn, r
    except DBAPIError as e:
        raise refusal(e) from None


def _cost_center(conn: Connection, r: RoundRef, code: str, rights: dict[int, bool]) -> int:
    found = conn.execute(
        select(member.c.member_id).where(
            member.c.dim_id == COST_CENTER, member.c.code == code.strip(), ~member.c.is_group
        )
    ).scalar()
    if found is None or found not in rights:
        raise CapexError(f"no cost center {code}")
    if not rights[found]:
        raise AccessError(f"you don't plan {code}")
    if found not in _planned(conn, r.version_id):
        raise CapexError(f"{code} is not planned in this round")
    return int(found)


def _type(conn: Connection, code: str | None, current: int | None = None) -> int | None:
    if code is None or not code.strip():
        return None
    row = conn.execute(
        select(capex_type.c.type_id, capex_type.c.active).where(capex_type.c.code == code.strip())
    ).first()
    if row is None:
        raise CapexError(f"no investment type {code}")
    if not row.active and row.type_id != current:
        raise CapexError(f"the investment type {code} is no longer used")
    return int(row.type_id)


def _priority(value: object) -> int:
    if value is None:
        return DEFAULT_PRIORITY
    if isinstance(value, int) and value in PRIORITIES:
        return value
    raise CapexError(f"priority {value} is not 1, 2 or 3")


def _name(value: object) -> str:
    name = str(value or "").strip()
    if not name:
        raise CapexError("an investment needs a name")
    return name


def _amount(value: object) -> Decimal | None:
    if value is None:
        return None
    if not isinstance(value, Decimal) or value < 0:
        raise CapexError(f"the amount {value} is not a positive number")
    if value != value.quantize(Decimal("0.01")):
        raise CapexError(f"{value} has more than 2 decimal places")
    return value


def _month(value: object) -> date | None:
    if value is None:
        return None
    if not isinstance(value, date):
        raise CapexError(f"{value} is not a month")
    return value.replace(day=1)


def apply(
    engine: Engine,
    user: User,
    code: str,
    creates: list[NewItem],
    changes: list[ItemChange],
) -> list[int]:
    """Add and change investments in one transaction; returns the new items' ids. A change of a
    row someone else changed since it was read saves nothing (ADR 0010)."""
    with _writing(engine, user, code) as (conn, r):
        rights = cost_center_rights(conn, user)
        _change(conn, r, user, rights, changes)
        return [_create(conn, r, user, rights, n) for n in creates]


def _create(conn: Connection, r: RoundRef, user: User, rights: dict[int, bool], n: NewItem) -> int:
    values: dict[str, object] = {
        "version_id": r.version_id,
        "kind": n.kind,
        "name": _name(n.name),
        "created_by": user.login,
        "changed_by": user.login,
    }
    if n.project_id is not None:
        if n.kind != "line":
            raise CapexError("a large project isn't part of another")
        project = conn.execute(
            select(capex_item.c.cost_center_id, capex_item.c.kind).where(
                capex_item.c.item_id == n.project_id, capex_item.c.version_id == r.version_id
            )
        ).first()
        if project is None or project.kind != "project":
            raise CapexError(f"no large project {n.project_id}")
        if not rights.get(project.cost_center_id):
            raise AccessError("you don't plan this large project's cost center")
        values |= {"project_id": n.project_id, "cost_center_id": project.cost_center_id}
    else:
        if not n.cost_center:
            raise CapexError(f"{values['name']}: choose a cost center")
        values |= {
            "cost_center_id": _cost_center(conn, r, n.cost_center, rights),
            "priority": _priority(n.priority),
            "status": "open",
        }
    if n.kind == "line":
        values |= {
            "type_id": _type(conn, n.type),
            "start_month": _month(n.start_month),
            "amount": _amount(n.amount),
        }
    elif n.type or n.start_month or n.amount is not None:
        raise CapexError("a large project's type, start and amount are its lines'")
    return int(
        conn.execute(
            insert(capex_item).values(**values).returning(capex_item.c.item_id)
        ).scalar_one()
    )


def _rows(conn: Connection, r: RoundRef, ids: list[int]) -> dict[int, object]:
    """The items, locked for update; an item the user doesn't read isn't found."""
    found: dict[int, object] = {
        row.item_id: row
        for row in conn.execute(
            select(capex_item)
            .where(capex_item.c.item_id.in_(ids), capex_item.c.version_id == r.version_id)
            .with_for_update()
        )
    }
    if missing := [i for i in ids if i not in found]:
        raise CapexError(f"investment {missing[0]} not found")
    return found


def _change(
    conn: Connection,
    r: RoundRef,
    user: User,
    rights: dict[int, bool],
    changes: list[ItemChange],
) -> None:
    if not changes:
        return
    rows = _rows(conn, r, [c.item_id for c in changes])
    conflicts = [
        Conflict(str(c.item_id), 0)
        for c in changes
        if c.expected is not None and rows[c.item_id].row_version != c.expected  # type: ignore[attr-defined]
    ]
    if conflicts:
        raise ConflictError(conflicts)
    for c in changes:
        row = rows[c.item_id]._mapping  # type: ignore[attr-defined]
        if not rights.get(row["cost_center_id"]):
            raise AccessError("you don't plan this investment's cost center")
        values: dict[str, object] = {}
        for key, value in c.values.items():
            match key:
                case "name":
                    values["name"] = _name(value)
                case "cost_center" | "priority" if row["project_id"] is not None:
                    raise CapexError(f"a large project's lines take its {key.replace('_', ' ')}")
                case "type" | "start_month" | "amount" if row["kind"] == "project":
                    raise CapexError(f"a large project's {key.replace('_', ' ')} is its lines'")
                case "cost_center":
                    values["cost_center_id"] = _cost_center(conn, r, str(value or ""), rights)
                case "priority":
                    values["priority"] = _priority(value)
                case "type":
                    values["type_id"] = _type(conn, cast(str | None, value), row["type_id"])
                case "start_month":
                    values["start_month"] = _month(value)
                case "amount":
                    values["amount"] = _amount(value)
                case _:
                    raise CapexError(f"{key} can't be changed")
        if "amount" in values:
            _trim_cash_out(conn, c.item_id, cast(Decimal | None, values["amount"]))
        if values:
            conn.execute(
                update(capex_item)
                .where(capex_item.c.item_id == c.item_id)
                .values(**values, changed_by=user.login, changed_at=func.now())
            )


def _schedule(conn: Connection, item_id: int) -> dict[date, Decimal]:
    return dict(
        conn.execute(
            select(capex_cash_out.c.period, capex_cash_out.c.amount).where(
                capex_cash_out.c.item_id == item_id
            )
        ).all()
    )


def _trim_cash_out(conn: Connection, item_id: int, amount: Decimal | None) -> None:
    """A lower amount cuts the cash-out schedule from its latest period back."""
    before = _schedule(conn, item_id)
    after = trimmed(before, amount)
    if after != before:
        _write_schedule(conn, item_id, after)


def _write_schedule(conn: Connection, item_id: int, schedule: dict[date, Decimal]) -> None:
    conn.execute(delete(capex_cash_out).where(capex_cash_out.c.item_id == item_id))
    if schedule:
        conn.execute(
            insert(capex_cash_out),
            [{"item_id": item_id, "period": p, "amount": v} for p, v in sorted(schedule.items())],
        )


def delete_items(engine: Engine, user: User, code: str, item_ids: list[int]) -> None:
    """Delete investments; a large project goes with its lines."""
    with _writing(engine, user, code) as (conn, r):
        rights = cost_center_rights(conn, user)
        rows = _rows(conn, r, item_ids)
        for row in rows.values():
            if not rights.get(row.cost_center_id):  # type: ignore[attr-defined]
                raise AccessError("you don't plan this investment's cost center")
        conn.execute(delete(capex_item).where(capex_item.c.item_id.in_(item_ids)))


def make_project(engine: Engine, user: User, code: str, item_id: int) -> int:
    """Turn a single investment into a large project with the investment as its first line;
    returns the project's id."""
    with _writing(engine, user, code) as (conn, r):
        rights = cost_center_rights(conn, user)
        row = _rows(conn, r, [item_id])[item_id]._mapping  # type: ignore[attr-defined]
        if row["kind"] != "line" or row["project_id"] is not None:
            raise CapexError("only a single investment becomes a large project")
        if not rights.get(row["cost_center_id"]):
            raise AccessError("you don't plan this investment's cost center")
        project = conn.execute(
            insert(capex_item)
            .values(
                version_id=r.version_id,
                kind="project",
                cost_center_id=row["cost_center_id"],
                name=row["name"],
                priority=row["priority"],
                status="open",
                created_by=user.login,
                changed_by=user.login,
            )
            .returning(capex_item.c.item_id)
        ).scalar_one()
        _join(conn, user, item_id, project, row["cost_center_id"])
        return int(project)


def _join(conn: Connection, user: User, item_id: int, project: int, cost_center: int) -> None:
    conn.execute(
        update(capex_item)
        .where(capex_item.c.item_id == item_id)
        .values(
            project_id=project,
            cost_center_id=cost_center,
            priority=None,
            status=None,
            status_by=None,
            status_at=None,
            changed_by=user.login,
            changed_at=func.now(),
        )
    )


def move(engine: Engine, user: User, code: str, item_id: int, project_id: int | None) -> None:
    """Move an investment into a large project (onto its cost center), or out of its project
    (keeping the project's priority; open)."""
    with _writing(engine, user, code) as (conn, r):
        rights = cost_center_rights(conn, user)
        ids = [item_id] if project_id is None else [item_id, project_id]
        rows = {k: v._mapping for k, v in _rows(conn, r, ids).items()}  # type: ignore[attr-defined]
        row = rows[item_id]
        if row["kind"] != "line":
            raise CapexError("a large project isn't part of another")
        if not rights.get(row["cost_center_id"]):
            raise AccessError("you don't plan this investment's cost center")
        if project_id is None:
            if row["project_id"] is None:
                return
            head = _rows(conn, r, [row["project_id"]])[row["project_id"]]
            conn.execute(
                update(capex_item)
                .where(capex_item.c.item_id == item_id)
                .values(
                    project_id=None,
                    priority=head.priority,  # type: ignore[attr-defined]
                    status="open",
                    changed_by=user.login,
                    changed_at=func.now(),
                )
            )
            return
        project = rows[project_id]
        if project["kind"] != "project":
            raise CapexError(f"no large project {project_id}")
        if not rights.get(project["cost_center_id"]):
            raise AccessError("you don't plan the large project's cost center")
        if project["cost_center_id"] not in _planned(conn, r.version_id):
            raise CapexError("the large project's cost center is not planned in this round")
        _join(conn, user, item_id, project_id, project["cost_center_id"])


def set_status(
    engine: Engine,
    user: User,
    code: str,
    item_ids: list[int],
    status: Status,
    comment: str | None = None,
) -> None:
    """Confirm, decline or reopen investments and large projects (a project's lines go with
    it). A comment goes to each one's comments."""
    text = (comment or "").strip()
    if status == "declined" and not text:
        raise CapexError("say why the investment is declined")
    with _writing(engine, user, code) as (conn, r):
        rows = _rows(conn, r, item_ids)
        confirmable = _confirms(conn, user)
        for row in rows.values():
            m = row._mapping  # type: ignore[attr-defined]
            if m["project_id"] is not None:
                raise CapexError("a line of a large project is confirmed with its project")
            if m["cost_center_id"] not in confirmable:
                raise AccessError(
                    "only an approver above the cost center or a controller confirms investments"
                )
        conn.execute(
            update(capex_item)
            .where(capex_item.c.item_id.in_(item_ids))
            .values(
                status=status,
                status_by=None if status == "open" else user.login,
                status_at=None if status == "open" else func.now(),
            )
        )
        if text:
            label = {"open": "Reopened", "confirmed": "Confirmed", "declined": "Declined"}[status]
            conn.execute(
                insert(capex_comment),
                [
                    {"item_id": i, "author_id": user.user_id, "body": f"{label}: {text}"}
                    for i in item_ids
                ],
            )


def set_cash_out(
    engine: Engine, user: User, code: str, item_id: int, schedule: dict[date, Decimal]
) -> None:
    """Replace an investment's cash-out schedule: the round's periods and the next fiscal year,
    never more than its amount."""
    with _writing(engine, user, code) as (conn, r):
        settings = settings_of(conn, r.version_id)
        if settings is None or not settings.cash_out:
            raise CapexError("this round doesn't plan cash out")
        rights = cost_center_rights(conn, user)
        row = _rows(conn, r, [item_id])[item_id]._mapping  # type: ignore[attr-defined]
        if row["kind"] != "line":
            raise CapexError("a large project's cash out is planned on its lines")
        if not rights.get(row["cost_center_id"]):
            raise AccessError("you don't plan this investment's cost center")
        periods, next_year = periods_of(conn, r)
        check_schedule(row["amount"], schedule, [*periods, next_year])
        _write_schedule(conn, item_id, schedule)


# --- Comments ------------------------------------------------------------------------------------


@dataclass
class Comment:
    comment_id: int
    author: str
    body: str
    created_at: datetime
    mine: bool


def comments(engine: Engine, user: User, code: str, item_id: int) -> list[Comment]:
    with unit_of_work(engine, user) as conn:
        r = get_round(conn, code)
        _visible(conn, r, item_id)
        return [
            Comment(c.comment_id, c.display_name, c.body, c.created_at, c.author_id == user.user_id)
            for c in conn.execute(
                select(
                    capex_comment.c.comment_id,
                    capex_comment.c.author_id,
                    capex_comment.c.body,
                    capex_comment.c.created_at,
                    app_user.c.display_name,
                )
                .join(app_user, app_user.c.user_id == capex_comment.c.author_id)
                .where(capex_comment.c.item_id == item_id)
                .order_by(capex_comment.c.created_at, capex_comment.c.comment_id)
            )
        ]


def _visible(conn: Connection, r: RoundRef, item_id: int) -> None:
    found = conn.execute(
        select(capex_item.c.item_id).where(
            capex_item.c.item_id == item_id, capex_item.c.version_id == r.version_id
        )
    ).scalar()
    if found is None:
        raise CapexError(f"investment {item_id} not found")


def add_comment(engine: Engine, user: User, code: str, item_id: int, body: str) -> None:
    """Everyone who reads an investment comments on it, in any round status."""
    text = body.strip()
    if not text:
        raise CapexError("a comment needs text")
    try:
        with unit_of_work(engine, user) as conn:
            r = get_round(conn, code)
            _visible(conn, r, item_id)
            conn.execute(
                insert(capex_comment).values(item_id=item_id, author_id=user.user_id, body=text)
            )
    except DBAPIError as e:
        raise refusal(e) from None


def delete_comment(engine: Engine, user: User, code: str, item_id: int, comment_id: int) -> None:
    """Authors delete their own comments."""
    with unit_of_work(engine, user) as conn:
        r = get_round(conn, code)
        _visible(conn, r, item_id)
        gone = conn.execute(
            delete(capex_comment).where(
                capex_comment.c.comment_id == comment_id,
                capex_comment.c.item_id == item_id,
                capex_comment.c.author_id == user.user_id,
            )
        ).rowcount
        if not gone:
            raise CapexError("only a comment's author deletes it")
