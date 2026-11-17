"""Line notes endpoints (ADR 0017): a line's threads and notepad, writing and resolving notes,
the people a note can mention, and the user's own open threads. Amounts are decimal strings."""

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from planlayer import notes
from planlayer.access import AccessError
from planlayer.api.auth import CurrentUser, engine_of
from planlayer.api.planning import _s
from planlayer.rounds import NotFoundError

router = APIRouter(prefix="/api/notes")

Module = Literal["cost_center", "capacity", "capex"]
HANDLED = (AccessError, NotFoundError, notes.NoteError)


def _http(e: Exception) -> HTTPException:
    match e:
        case AccessError():
            return HTTPException(403, str(e))
        case NotFoundError():
            return HTTPException(404, str(e))
        case notes.NoteError():
            return HTTPException(422, str(e))
    raise e


class NoteLineIn(BaseModel):
    """A line: ``cost_center`` with ``account`` on the cost sheet, or with ``work_center`` and
    ``activity_type`` on the capacity sheet; ``investment`` (its id) for module ``capex``."""

    module: Module
    cost_center: str | None = None
    account: str | None = None
    work_center: str | None = None
    activity_type: str | None = None
    investment: int | None = None

    def ref(self) -> notes.LineRef:
        return notes.LineRef(
            self.module,
            self.cost_center,
            self.account,
            self.work_center,
            self.activity_type,
            self.investment,
        )


class PersonOut(BaseModel):
    login: str
    name: str


class NoteOut(BaseModel):
    note_id: int
    author: PersonOut
    body: str
    created_at: datetime
    edited_at: datetime | None
    mentions: list[PersonOut]
    mine: bool


class ThreadOut(BaseModel):
    thread_id: int
    notes: list[NoteOut]
    assignee: PersonOut | None
    resolved_at: datetime | None
    resolved_by: PersonOut | None
    carried_from: str | None  # the earlier round's name; null when written in this round


class GridRowIO(BaseModel):
    text: Annotated[str, Field(max_length=500)]
    amount: str | None


class NotepadOut(BaseModel):
    rows: list[GridRowIO]
    total: str
    changed_at: datetime
    changed_by: PersonOut
    carried_from: str | None


class LineNotesOut(BaseModel):
    threads: list[ThreadOut]
    notepad: NotepadOut | None
    can_write: bool  # may change the notepad and insert its total


class NoteIn(BaseModel):
    version: str  # the round written from
    body: Annotated[str, Field(min_length=1, max_length=notes.MAX_BODY)]
    mentions: list[str] = []  # logins
    assign: str | None = None  # a mentioned login: the thread becomes their task


class ThreadIn(NoteIn):
    line: NoteLineIn


class NoteEditIn(BaseModel):
    version: str
    body: Annotated[str, Field(min_length=1, max_length=notes.MAX_BODY)]
    mentions: list[str] = []


class ResolveIn(BaseModel):
    version: str
    resolved: bool


class NotepadIn(BaseModel):
    version: str
    line: NoteLineIn
    rows: Annotated[list[GridRowIO], Field(max_length=notes.MAX_GRID_ROWS)]


class MyThreadOut(BaseModel):
    thread_id: int
    reason: Literal["assigned", "mentioned", "started"]
    module: Module
    cost_center_code: str
    cost_center_name: str
    line_code: str
    line_name: str
    round_code: str
    first: str
    last_author: str
    last_at: datetime
    assignee: str | None
    notes: int


class MyNotesOut(BaseModel):
    tasks: int  # open tasks assigned to the user
    threads: list[MyThreadOut]


def _person(p: notes.Person | None) -> PersonOut | None:
    return None if p is None else PersonOut(login=p.login, name=p.name)


def _line_out(n: notes.LineNotes) -> LineNotesOut:
    pad = n.notepad
    return LineNotesOut(
        threads=[
            ThreadOut(
                thread_id=t.thread_id,
                notes=[
                    NoteOut(
                        note_id=x.note_id,
                        author=PersonOut(login=x.author.login, name=x.author.name),
                        body=x.body,
                        created_at=x.created_at,
                        edited_at=x.edited_at,
                        mentions=[PersonOut(login=m.login, name=m.name) for m in x.mentions],
                        mine=x.mine,
                    )
                    for x in t.notes
                ],
                assignee=_person(t.assignee),
                resolved_at=t.resolved_at,
                resolved_by=_person(t.resolved_by),
                carried_from=t.carried_from,
            )
            for t in n.threads
        ],
        notepad=None
        if pad is None
        else NotepadOut(
            rows=[GridRowIO(text=r.text, amount=_s(r.amount)) for r in pad.rows],
            total=f"{pad.total:.2f}",
            changed_at=pad.changed_at,
            changed_by=PersonOut(login=pad.changed_by.login, name=pad.changed_by.name),
            carried_from=pad.carried_from,
        ),
        can_write=n.can_write,
    )


def _read(request: Request, user: CurrentUser, version: str, line: NoteLineIn) -> LineNotesOut:
    return _line_out(notes.line_notes(engine_of(request), user, version, line.ref()))


@router.get("/line", responses={404: {}})
def line_notes(
    request: Request,
    user: CurrentUser,
    version: str,
    module: Module,
    cost_center: str | None = None,
    account: str | None = None,
    work_center: str | None = None,
    activity_type: str | None = None,
    investment: int | None = None,
) -> LineNotesOut:
    """The line's threads in the round (its own, and open ones carried from earlier rounds)
    and its notepad."""
    try:
        line = NoteLineIn(
            module=module,
            cost_center=cost_center,
            account=account,
            work_center=work_center,
            activity_type=activity_type,
            investment=investment,
        )
        return _read(request, user, version, line)
    except HANDLED as e:
        raise _http(e) from None


@router.post("/threads", responses={404: {}, 422: {}})
def start_thread(request: Request, user: CurrentUser, body: ThreadIn) -> LineNotesOut:
    try:
        notes.start_thread(
            engine_of(request), user, body.version, body.line.ref(), body.body, body.mentions,
            body.assign,
        )  # fmt: skip
        return _read(request, user, body.version, body.line)
    except HANDLED as e:
        raise _http(e) from None


@router.post("/threads/{thread_id}/replies", responses={404: {}, 422: {}})
def reply(request: Request, user: CurrentUser, thread_id: int, body: NoteIn) -> None:
    try:
        notes.reply(
            engine_of(request), user, body.version, thread_id, body.body, body.mentions,
            body.assign,
        )  # fmt: skip
    except HANDLED as e:
        raise _http(e) from None


@router.post("/threads/{thread_id}/resolved", responses={404: {}})
def resolve(request: Request, user: CurrentUser, thread_id: int, body: ResolveIn) -> None:
    try:
        notes.resolve(engine_of(request), user, body.version, thread_id, body.resolved)
    except HANDLED as e:
        raise _http(e) from None


@router.put("/notepad", responses={403: {}, 404: {}, 422: {}})
def set_notepad(request: Request, user: CurrentUser, body: NotepadIn) -> LineNotesOut:
    """Replace the line's notepad; no rows removes it."""
    try:
        rows = notes.parse_rows([(r.text, r.amount) for r in body.rows])
        notes.set_notepad(engine_of(request), user, body.version, body.line.ref(), rows)
        return _read(request, user, body.version, body.line)
    except HANDLED as e:
        raise _http(e) from None


@router.put("/{note_id}", responses={403: {}, 404: {}, 422: {}})
def edit_note(request: Request, user: CurrentUser, note_id: int, body: NoteEditIn) -> None:
    try:
        notes.edit_note(engine_of(request), user, body.version, note_id, body.body, body.mentions)
    except HANDLED as e:
        raise _http(e) from None


@router.delete("/{note_id}", responses={403: {}, 404: {}})
def delete_note(request: Request, user: CurrentUser, note_id: int) -> None:
    try:
        notes.delete_note(engine_of(request), user, note_id)
    except HANDLED as e:
        raise _http(e) from None


@router.get("/people", responses={404: {}})
def people(
    request: Request, user: CurrentUser, cost_center: str, module: Module = "cost_center"
) -> list[PersonOut]:
    """Who a note on the cost center's lines can mention: those who may read them."""
    try:
        return [
            PersonOut(login=p.login, name=p.name)
            for p in notes.people(engine_of(request), user, module, cost_center)
        ]
    except HANDLED as e:
        raise _http(e) from None


@router.get("/mine")
def mine(
    request: Request, user: CurrentUser, limit: Annotated[int, Query(ge=1, le=500)] = 200
) -> MyNotesOut:
    """Open threads assigned to the user, mentioning them, or started by them."""
    m = notes.my_notes(engine_of(request), user)
    return MyNotesOut(
        tasks=m.tasks,
        threads=[MyThreadOut.model_validate(t, from_attributes=True) for t in m.threads[:limit]],
    )
