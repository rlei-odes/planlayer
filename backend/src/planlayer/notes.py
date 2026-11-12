"""Line notes (ADR 0017): threads of notes on a line, with mentions, tasks and resolving; one
notepad per line; and the notes a user should look at.

A line is a cost center x leaf account (module ``cost_center``) or a cost center x work center x
activity type (module ``capacity``). Threads live outside rounds: each remembers the round it was
last written in, shows there, and while open in every later round. Writing to a thread moves it
to the round it is written from.

Row-level security lets everyone who reads a line read and write its threads; authors change
their own notes; whoever writes the line changes its notepad.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Literal

from sqlalchemy import (
    ColumnElement,
    Connection,
    Engine,
    Table,
    and_,
    delete,
    func,
    insert,
    or_,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert

from planlayer.access import AccessError, User, capacity_rights, cost_center_rights
from planlayer.rounds import NotFoundError, RoundRef, get_round
from planlayer.store.schema import (
    ACCOUNT,
    ACTIVITY_TYPE,
    COST_CENTER,
    MODULE_CAPACITY,
    MODULE_COST_CENTER,
    WORK_CENTER,
    app_user,
    capacity_access,
    cost_center_access,
    member,
    note,
    note_grid,
    note_mention,
    note_thread,
    planning_round,
    round_member,
    version,
)
from planlayer.store.uow import unit_of_work

Module = Literal["cost_center", "capacity"]
MAX_BODY = 10_000
MAX_GRID_ROWS = 200


class NoteError(ValueError):
    """A note that can't be written as sent."""


@dataclass(frozen=True)
class LineRef:
    """A line, by codes: an account on the cost sheet, or a work center and activity type on the
    capacity sheet."""

    module: Module
    cost_center: str
    account: str | None = None
    work_center: str | None = None
    activity_type: str | None = None


@dataclass(frozen=True)
class _Line:
    module: Module
    cost_center_id: int
    account_id: int | None
    work_center_id: int | None
    activity_type_id: int | None
    can_write: bool  # may write the line itself (its notepad), not only its notes

    def where(self, t: Table) -> list[ColumnElement[bool]]:
        return [
            t.c.cost_center_id == self.cost_center_id,
            _is(t.c.account_id, self.account_id),
            _is(t.c.work_center_id, self.work_center_id),
            _is(t.c.activity_type_id, self.activity_type_id),
        ]

    def values(self) -> dict[str, object]:
        return {
            "module": self.module,
            "cost_center_id": self.cost_center_id,
            "account_id": self.account_id,
            "work_center_id": self.work_center_id,
            "activity_type_id": self.activity_type_id,
        }


def _is(column: ColumnElement[int], value: int | None) -> ColumnElement[bool]:
    return column.is_(None) if value is None else column == value


def _leaf(conn: Connection, dim: int, code: str | None, what: str) -> int:
    if code is None:
        raise NoteError(f"the line needs its {what}")
    found = conn.execute(
        select(member.c.member_id).where(
            member.c.dim_id == dim, member.c.code == code, member.c.is_group.is_(False)
        )
    ).scalar()
    if found is None:
        raise NotFoundError(f"{what} {code} not found (notes are on single {what}s, not groups)")
    return int(found)


def _line(conn: Connection, user: User, ref: LineRef) -> _Line:
    """The line's ids, if the user may read it; a line they may not read is not found."""
    cc = _leaf(conn, COST_CENTER, ref.cost_center, "cost center")
    rights = (cost_center_rights if ref.module == MODULE_COST_CENTER else capacity_rights)(
        conn, user
    )
    if cc not in rights:
        raise NotFoundError(f"cost center {ref.cost_center} not found")
    if ref.module == MODULE_COST_CENTER:
        return _Line(ref.module, cc, _leaf(conn, ACCOUNT, ref.account, "account"), None, None,
                     rights[cc])  # fmt: skip
    return _Line(
        ref.module,
        cc,
        None,
        _leaf(conn, WORK_CENTER, ref.work_center, "work center"),
        _leaf(conn, ACTIVITY_TYPE, ref.activity_type, "activity type"),
        rights[cc],
    )


def _shown_in(r: RoundRef) -> ColumnElement[bool]:
    """Threads a round shows: those last written in it, and the open ones of earlier rounds.
    Rounds are ordered by creation, which their version ids follow."""
    t = note_thread
    return or_(
        t.c.round_id == r.version_id,
        and_(t.c.resolved_at.is_(None), t.c.round_id < r.version_id),
    )


# --- Reading -----------------------------------------------------------------------------------


@dataclass
class Person:
    login: str
    name: str


@dataclass
class Note:
    note_id: int
    author: Person
    body: str
    created_at: datetime
    edited_at: datetime | None
    mentions: list[Person]
    mine: bool  # written by the user: they may edit and delete it


@dataclass
class Thread:
    thread_id: int
    notes: list[Note]
    assignee: Person | None
    resolved_at: datetime | None
    resolved_by: Person | None
    # The earlier round it was last written in; None when written in the round shown.
    carried_from: str | None
    created_at: datetime


@dataclass
class GridRow:
    text: str
    amount: Decimal | None


@dataclass
class Notepad:
    rows: list[GridRow]
    total: Decimal
    changed_at: datetime
    changed_by: Person
    carried_from: str | None


@dataclass
class LineNotes:
    threads: list[Thread]  # open ones first, then resolved, each oldest first
    notepad: Notepad | None
    can_write: bool  # may change the notepad and insert its total


def _people(conn: Connection, ids: set[int]) -> dict[int, Person]:
    if not ids:
        return {}
    return {
        uid: Person(login, name)
        for uid, login, name in conn.execute(
            select(app_user.c.user_id, app_user.c.login, app_user.c.display_name).where(
                app_user.c.user_id.in_(ids)
            )
        )
    }


def _round_names(conn: Connection) -> dict[int, str]:
    return dict(
        conn.execute(
            select(version.c.version_id, version.c.name).join(
                planning_round, planning_round.c.version_id == version.c.version_id
            )
        ).all()
    )


def line_notes(engine: Engine, user: User, version_code: str, ref: LineRef) -> LineNotes:
    with unit_of_work(engine, user) as conn:
        r = get_round(conn, version_code)
        line = _line(conn, user, ref)
        return _read(conn, user, r, line)


def _read(conn: Connection, user: User, r: RoundRef, line: _Line) -> LineNotes:
    t = note_thread
    threads = conn.execute(
        select(t).where(*line.where(t), _shown_in(r)).order_by(t.c.created_at, t.c.thread_id)
    ).all()
    ids = [th.thread_id for th in threads]
    notes = conn.execute(
        select(note).where(note.c.thread_id.in_(ids)).order_by(note.c.created_at, note.c.note_id)
    ).all()
    mentions: dict[int, list[int]] = {}
    for note_id, uid in conn.execute(
        select(note_mention.c.note_id, note_mention.c.user_id).where(
            note_mention.c.note_id.in_([n.note_id for n in notes])
        )
    ):
        mentions.setdefault(note_id, []).append(uid)
    grid = conn.execute(select(note_grid).where(*line.where(note_grid))).first()
    people = _people(
        conn,
        {n.author_id for n in notes}
        | {u for us in mentions.values() for u in us}
        | {th.assignee_id for th in threads if th.assignee_id}
        | {th.resolved_by for th in threads if th.resolved_by}
        | ({grid.changed_by} if grid else set()),
    )
    rounds = _round_names(conn)
    by_thread: dict[int, list[Note]] = {i: [] for i in ids}
    for n in notes:
        by_thread[n.thread_id].append(
            Note(
                n.note_id,
                people[n.author_id],
                n.body,
                n.created_at,
                n.edited_at,
                [people[u] for u in mentions.get(n.note_id, [])],
                n.author_id == user.user_id,
            )
        )
    out = [
        Thread(
            th.thread_id,
            by_thread[th.thread_id],
            people.get(th.assignee_id) if th.assignee_id else None,
            th.resolved_at,
            people.get(th.resolved_by) if th.resolved_by else None,
            None if th.round_id == r.version_id else rounds.get(th.round_id),
            th.created_at,
        )
        for th in threads
    ]
    out.sort(key=lambda th: th.resolved_at is not None)  # stable: open first
    notepad = None
    if grid is not None:
        rows = [GridRow(row["text"], _amount(row.get("amount"))) for row in grid.rows]
        notepad = Notepad(
            rows,
            sum((row.amount for row in rows if row.amount is not None), Decimal(0)),
            grid.changed_at,
            people[grid.changed_by],
            None if grid.round_id == r.version_id else rounds.get(grid.round_id),
        )
    return LineNotes(out, notepad, line.can_write)


def _amount(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        raise NoteError(f"not a number: {value!r}") from None
    if not amount.is_finite():
        raise NoteError(f"not a number: {value!r}")
    exponent = amount.as_tuple().exponent
    if isinstance(exponent, int) and exponent < -2:
        raise NoteError(f"{value} has more than 2 decimal places")
    return amount


# --- Markers on the sheets ---------------------------------------------------------------------


@dataclass
class Marker:
    """What a line shows on its sheet: its open threads, whether one is a task for the user, and
    whether it has a notepad."""

    open: int = 0
    resolved: int = 0
    task_for_me: bool = False
    notepad: bool = False


def markers(
    conn: Connection, user: User, r: RoundRef, module: Module, cost_center_id: int
) -> dict[tuple[int | None, int | None, int | None], Marker]:
    """Per line of the cost center's sheet, keyed (account, work center, activity type)."""
    t = note_thread
    out: dict[tuple[int | None, int | None, int | None], Marker] = {}
    for account, wc, at, resolved, assignee in conn.execute(
        select(t.c.account_id, t.c.work_center_id, t.c.activity_type_id, t.c.resolved_at,
               t.c.assignee_id)
        .where(t.c.module == module, t.c.cost_center_id == cost_center_id, _shown_in(r))
    ):  # fmt: skip
        m = out.setdefault((account, wc, at), Marker())
        if resolved is None:
            m.open += 1
            m.task_for_me |= assignee == user.user_id
        else:
            m.resolved += 1
    g = note_grid
    for key in conn.execute(
        select(g.c.account_id, g.c.work_center_id, g.c.activity_type_id).where(
            g.c.module == module, g.c.cost_center_id == cost_center_id
        )
    ):
        out.setdefault((key[0], key[1], key[2]), Marker()).notepad = True
    return out


# --- Writing -----------------------------------------------------------------------------------


def _body(text: str) -> str:
    body = text.strip()
    if not body:
        raise NoteError("a note needs some text")
    if len(body) > MAX_BODY:
        raise NoteError(f"a note has at most {MAX_BODY} characters")
    return body


def _mentioned(conn: Connection, line: _Line, logins: list[str]) -> dict[str, int]:
    """Mentioned users by login: each must be able to read the line."""
    wanted = {login.strip().lower() for login in logins if login.strip()}
    if not wanted:
        return {}
    found = dict(
        conn.execute(
            select(app_user.c.login, app_user.c.user_id).where(
                app_user.c.login.in_(wanted), app_user.c.disabled.is_(False)
            )
        ).all()
    )
    missing = wanted - set(found)
    if missing:
        raise NoteError(f"unknown user(s): {', '.join(sorted(missing))}")
    view = cost_center_access if line.module == MODULE_COST_CENTER else capacity_access
    readers = set(
        conn.execute(
            select(view.c.user_id).where(
                view.c.cost_center_id == line.cost_center_id, view.c.user_id.in_(found.values())
            )
        ).scalars()
    )
    blind = sorted(login for login, uid in found.items() if uid not in readers)
    if blind:
        raise NoteError(f"{', '.join(blind)} can't read this line")
    return found


def _add_note(
    conn: Connection, user: User, thread_id: int, line: _Line, body: str, mentions: list[str],
    assign: str | None,
) -> int:  # fmt: skip
    found = _mentioned(conn, line, mentions)
    note_id = int(
        conn.execute(
            insert(note)
            .values(thread_id=thread_id, author_id=user.user_id, body=_body(body))
            .returning(note.c.note_id)
        ).scalar_one()
    )
    if found:
        conn.execute(
            insert(note_mention), [{"note_id": note_id, "user_id": u} for u in found.values()]
        )
    if assign is not None:
        login = assign.strip().lower()
        if login not in found:
            raise NoteError("a task is assigned to someone mentioned in the note")
        conn.execute(
            update(note_thread)
            .where(note_thread.c.thread_id == thread_id)
            .values(assignee_id=found[login], resolved_at=None, resolved_by=None)
        )
    return note_id


def start_thread(
    engine: Engine, user: User, version_code: str, ref: LineRef, body: str,
    mentions: list[str], assign: str | None = None,
) -> int:  # fmt: skip
    """A new thread on the line, written in the round; returns its id."""
    with unit_of_work(engine, user, uuid.uuid4().hex) as conn:
        r = get_round(conn, version_code)
        line = _line(conn, user, ref)
        thread_id = int(
            conn.execute(
                insert(note_thread)
                .values(**line.values(), round_id=r.version_id, created_by=user.user_id)
                .returning(note_thread.c.thread_id)
            ).scalar_one()
        )
        _add_note(conn, user, thread_id, line, body, mentions, assign)
        return thread_id


def _thread(conn: Connection, user: User, thread_id: int) -> tuple[object, _Line]:
    th = conn.execute(select(note_thread).where(note_thread.c.thread_id == thread_id)).first()
    if th is None:
        raise NotFoundError(f"note thread {thread_id} not found")
    rights = (cost_center_rights if th.module == MODULE_COST_CENTER else capacity_rights)(
        conn, user
    )
    line = _Line(
        th.module,
        th.cost_center_id,
        th.account_id,
        th.work_center_id,
        th.activity_type_id,
        rights.get(th.cost_center_id, False),
    )
    return th, line


def _touch(conn: Connection, thread_id: int, r: RoundRef) -> None:
    """Written to in the round: the thread is the round's now (no longer carried)."""
    conn.execute(
        update(note_thread)
        .where(note_thread.c.thread_id == thread_id)
        .values(round_id=r.version_id)
    )


def reply(
    engine: Engine, user: User, version_code: str, thread_id: int, body: str,
    mentions: list[str], assign: str | None = None,
) -> None:  # fmt: skip
    with unit_of_work(engine, user, uuid.uuid4().hex) as conn:
        r = get_round(conn, version_code)
        _, line = _thread(conn, user, thread_id)
        _add_note(conn, user, thread_id, line, body, mentions, assign)
        _touch(conn, thread_id, r)


def edit_note(
    engine: Engine, user: User, version_code: str, note_id: int, body: str, mentions: list[str]
) -> None:
    """The author changes their note's text and mentions; a task stays assigned."""
    with unit_of_work(engine, user, uuid.uuid4().hex) as conn:
        r = get_round(conn, version_code)
        n = conn.execute(select(note).where(note.c.note_id == note_id)).first()
        if n is None:
            raise NotFoundError(f"note {note_id} not found")
        if n.author_id != user.user_id:
            raise AccessError("only its author edits a note")
        _, line = _thread(conn, user, n.thread_id)
        found = _mentioned(conn, line, mentions)
        conn.execute(
            update(note)
            .where(note.c.note_id == note_id)
            .values(body=_body(body), edited_at=func.now())
        )
        conn.execute(delete(note_mention).where(note_mention.c.note_id == note_id))
        if found:
            conn.execute(
                insert(note_mention), [{"note_id": note_id, "user_id": u} for u in found.values()]
            )
        _touch(conn, n.thread_id, r)


def delete_note(engine: Engine, user: User, note_id: int) -> None:
    """The author deletes their note. Deleting a thread's first note deletes the thread with its
    replies, as in a document's comment threads."""
    with unit_of_work(engine, user, uuid.uuid4().hex) as conn:
        n = conn.execute(select(note).where(note.c.note_id == note_id)).first()
        if n is None:
            raise NotFoundError(f"note {note_id} not found")
        if n.author_id != user.user_id:
            raise AccessError("only its author deletes a note")
        first = conn.execute(
            select(note.c.note_id)
            .where(note.c.thread_id == n.thread_id)
            .order_by(note.c.created_at, note.c.note_id)
            .limit(1)
        ).scalar()
        if first == note_id:
            conn.execute(delete(note_thread).where(note_thread.c.thread_id == n.thread_id))
        else:
            conn.execute(delete(note).where(note.c.note_id == note_id))


def resolve(engine: Engine, user: User, version_code: str, thread_id: int, resolved: bool) -> None:
    """Resolve a thread, or reopen it; either is written in the round."""
    with unit_of_work(engine, user, uuid.uuid4().hex) as conn:
        r = get_round(conn, version_code)
        _thread(conn, user, thread_id)
        conn.execute(
            update(note_thread)
            .where(note_thread.c.thread_id == thread_id)
            .values(
                resolved_at=func.now() if resolved else None,
                resolved_by=user.user_id if resolved else None,
                round_id=r.version_id,
            )
        )


def set_notepad(
    engine: Engine, user: User, version_code: str, ref: LineRef, rows: list[GridRow]
) -> None:
    """Replace the line's notepad; without rows it is removed."""
    if len(rows) > MAX_GRID_ROWS:
        raise NoteError(f"a notepad has at most {MAX_GRID_ROWS} rows")
    kept = [
        {"text": row.text.strip(), "amount": None if row.amount is None else f"{row.amount:.2f}"}
        for row in rows
        if row.text.strip() or row.amount is not None
    ]
    with unit_of_work(engine, user, uuid.uuid4().hex) as conn:
        r = get_round(conn, version_code)
        line = _line(conn, user, ref)
        if not line.can_write:
            raise AccessError("only those who may write the line change its notepad")
        if not kept:
            conn.execute(delete(note_grid).where(*line.where(note_grid)))
            return
        values = {"rows": kept, "round_id": r.version_id, "changed_by": user.user_id}
        existing = conn.execute(select(note_grid.c.grid_id).where(*line.where(note_grid))).scalar()
        if existing is None:
            conn.execute(pg_insert(note_grid).values(**line.values(), **values))
        else:
            conn.execute(
                update(note_grid)
                .where(note_grid.c.grid_id == existing)
                .values(**values, changed_at=func.now())
            )


def parse_rows(rows: list[tuple[str, str | None]]) -> list[GridRow]:
    return [GridRow(text, _amount(amount)) for text, amount in rows]


# --- People and one's own notes --------------------------------------------------------------


def people(engine: Engine, user: User, module: Module, cost_center: str) -> list[Person]:
    """Users who may read the cost center's lines: those a note there can mention."""
    with unit_of_work(engine, user) as conn:
        cc = _leaf(conn, COST_CENTER, cost_center, "cost center")
        rights = (cost_center_rights if module == MODULE_COST_CENTER else capacity_rights)(
            conn, user
        )
        if cc not in rights:
            raise NotFoundError(f"cost center {cost_center} not found")
        view = cost_center_access if module == MODULE_COST_CENTER else capacity_access
        return [
            Person(login, name)
            for login, name in conn.execute(
                select(app_user.c.login, app_user.c.display_name)
                .distinct()
                .join(view, view.c.user_id == app_user.c.user_id)
                .where(
                    view.c.cost_center_id == cc,
                    app_user.c.disabled.is_(False),
                    app_user.c.is_system.is_(False),
                )
                .order_by(app_user.c.display_name)
            )
        ]


@dataclass
class MyThread:
    thread_id: int
    reason: Literal["assigned", "mentioned", "started"]
    module: Module
    cost_center_code: str
    cost_center_name: str
    line_code: str  # the account, or "work center / activity type"
    line_name: str
    round_code: str  # the round to open it in: the latest that shows it and plans the line
    first: str  # the thread's first note
    last_author: str
    last_at: datetime
    assignee: str | None
    notes: int


@dataclass
class MyNotes:
    threads: list[MyThread] = field(default_factory=list)

    @property
    def tasks(self) -> int:
        return sum(1 for t in self.threads if t.reason == "assigned")


def my_notes(engine: Engine, user: User) -> MyNotes:
    """Open threads assigned to the user, mentioning them, or started by them; most recent first.
    Each opens in the latest round that shows it and plans its cost center."""
    t = note_thread
    with unit_of_work(engine, user) as conn:
        mentioned = set(
            conn.execute(
                select(note.c.thread_id)
                .join(note_mention, note_mention.c.note_id == note.c.note_id)
                .where(note_mention.c.user_id == user.user_id)
            ).scalars()
        )
        threads = conn.execute(
            select(t).where(
                t.c.resolved_at.is_(None),
                or_(
                    t.c.assignee_id == user.user_id,
                    t.c.created_by == user.user_id,
                    t.c.thread_id.in_(mentioned),
                ),
            )
        ).all()
        if not threads:
            return MyNotes()
        ids = [th.thread_id for th in threads]
        notes = conn.execute(
            select(note.c.thread_id, note.c.body, note.c.author_id, note.c.created_at)
            .where(note.c.thread_id.in_(ids))
            .order_by(note.c.created_at, note.c.note_id)
        ).all()
        members = {
            m.member_id: m
            for m in conn.execute(
                select(member.c.member_id, member.c.code, member.c.name).where(
                    member.c.member_id.in_(
                        {
                            x
                            for th in threads
                            for x in (
                                th.cost_center_id,
                                th.account_id,
                                th.work_center_id,
                                th.activity_type_id,
                            )
                            if x is not None
                        }
                    )
                )
            )
        }
        people = _people(
            conn,
            {n.author_id for n in notes} | {th.assignee_id for th in threads if th.assignee_id},
        )
        # Rounds, newest first, with the cost centers they plan per module.
        rounds = conn.execute(
            select(version.c.version_id, version.c.code)
            .join(planning_round, planning_round.c.version_id == version.c.version_id)
            .order_by(version.c.version_id.desc())
        ).all()
        planned = {
            (v, module, m)
            for v, module, m in conn.execute(
                select(round_member.c.version_id, round_member.c.module, round_member.c.member_id)
                .where(round_member.c.enabled.is_(True))
            )
        }  # fmt: skip
    by_thread: dict[int, list[object]] = {}
    for n in notes:
        by_thread.setdefault(n.thread_id, []).append(n)
    out = MyNotes()
    for th in threads:
        ns = by_thread.get(th.thread_id, [])
        if not ns:
            continue
        cc = members[th.cost_center_id]
        if th.module == MODULE_COST_CENTER:
            acc = members[th.account_id]
            code, name = acc.code, acc.name
        else:
            wc, at = members[th.work_center_id], members[th.activity_type_id]
            code, name = f"{wc.code} / {at.code}", f"{wc.name} · {at.name}"
        module_key = MODULE_COST_CENTER if th.module == MODULE_COST_CENTER else MODULE_CAPACITY
        target = next(
            (c for v, c in rounds if v >= th.round_id and (v, module_key, cc.member_id) in planned),
            next(c for v, c in rounds if v == th.round_id),
        )
        last = ns[-1]
        out.threads.append(
            MyThread(
                th.thread_id,
                "assigned"
                if th.assignee_id == user.user_id
                else "mentioned"
                if th.thread_id in mentioned
                else "started",
                th.module,
                cc.code,
                cc.name,
                code,
                name,
                target,
                ns[0].body,  # type: ignore[attr-defined]
                people[last.author_id].name,  # type: ignore[attr-defined]
                last.created_at,  # type: ignore[attr-defined]
                people[th.assignee_id].name if th.assignee_id else None,
                len(ns),
            )
        )
    order = {"assigned": 0, "mentioned": 1, "started": 2}
    out.threads.sort(key=lambda m: (order[m.reason], -m.last_at.timestamp()))
    return out
