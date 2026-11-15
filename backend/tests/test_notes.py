"""Line notes and item lists (ADR 0017).

The ``client`` fixture syncs ``conftest.SOURCE`` (CC1 owned by anna, CC2 without owner, both under
DEP whose approver is appr@), opens B2026 and logs the controller in.
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import Engine, func, select

from planlayer import access
from planlayer.config import Settings
from planlayer.store.db import create_app_engine
from planlayer.store.schema import annotation_log, note_thread
from planlayer.store.uow import unit_of_work

from .conftest import APPROVER, CONTROLLER, NOBODY, OWNER, URL, VIEWER, login, rows

R = "B2026"
LINE = {"module": "cost_center", "cost_center": "CC1", "account": "6000"}


@pytest.fixture
def engine(migrated: Settings, client: TestClient) -> Iterator[Engine]:
    e = create_app_engine(migrated)
    yield e
    e.dispose()


def start(client: TestClient, body: str, version: str = R, **extra: object) -> Response:
    return client.post("/api/notes/threads", json={"version": version, "line": LINE,
                                                   "body": body, **extra})  # fmt: skip


def read(client: TestClient, version: str = R, line: dict | None = None) -> dict:  # type: ignore[type-arg]
    response = client.get("/api/notes/line", params={"version": version, **(line or LINE)})
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


def test_threads_mentions_and_tasks(client: TestClient) -> None:
    login(client, OWNER)
    # Mentions are limited to those who may read the line.
    refused = start(client, "Bob?", mentions=[NOBODY])
    assert refused.status_code == 422 and "can't read this line" in refused.text
    made = start(client, "Why 20 % more? @Appr", mentions=[APPROVER], assign=APPROVER)
    assert made.status_code == 200, made.text
    (thread,) = made.json()["threads"]
    assert thread["assignee"]["login"] == APPROVER and thread["carried_from"] is None
    assert thread["notes"][0]["mentions"] == [{"login": APPROVER, "name": "Appr"}]

    # The approver reads the line, so they find the task and may answer and resolve it.
    login(client, APPROVER)
    mine = client.get("/api/notes/mine").json()
    assert mine["tasks"] == 1 and mine["threads"][0]["reason"] == "assigned"
    assert mine["threads"][0]["round_code"] == R
    tid = thread["thread_id"]
    answer = {"version": R, "body": "New machine from July."}
    assert client.post(f"/api/notes/threads/{tid}/replies", json=answer).status_code == 200
    resolved = {"version": R, "resolved": True}
    assert client.post(f"/api/notes/threads/{tid}/resolved", json=resolved).status_code == 200
    assert client.get("/api/notes/mine").json() == {"tasks": 0, "threads": []}
    thread = read(client)["threads"][0]
    assert [n["body"] for n in thread["notes"]] == [
        "Why 20 % more? @Appr",
        "New machine from July.",
    ]
    assert thread["resolved_by"]["login"] == APPROVER

    # Viewers read every cost center and may write notes too; outsiders see nothing.
    login(client, VIEWER)
    assert start(client, "Noted.").status_code == 200
    login(client, NOBODY)
    assert client.get("/api/notes/line", params={"version": R, **LINE}).status_code == 404
    assert start(client, "Hello").status_code == 404


def test_authors_change_their_own_notes(client: TestClient, engine: Engine) -> None:
    login(client, OWNER)
    note_id = start(client, "Draft").json()["threads"][0]["notes"][0]["note_id"]
    login(client, CONTROLLER)
    assert client.put(f"/api/notes/{note_id}", json={"version": R, "body": "x"}).status_code == 403
    assert client.delete(f"/api/notes/{note_id}").status_code == 403
    login(client, OWNER)
    assert (
        client.put(f"/api/notes/{note_id}", json={"version": R, "body": "Final"}).status_code == 200
    )
    note = read(client)["threads"][0]["notes"][0]
    assert note["body"] == "Final" and note["edited_at"] is not None and note["mine"]
    assert client.delete(f"/api/notes/{note_id}").status_code == 200
    assert read(client)["threads"] == []  # the thread went with its only note
    # Row-level security: someone without the cost center reads no threads in the store.
    with engine.connect() as conn:
        nobody = access.user_by_login(conn, NOBODY)
    with unit_of_work(engine, nobody) as conn:
        start_count = conn.execute(select(func.count()).select_from(note_thread)).scalar()
    assert start_count == 0
    with unit_of_work(engine, access.system_user(engine)) as conn:
        logged = conn.execute(select(func.count()).select_from(annotation_log)).scalar_one()
    assert logged >= 4  # thread, note, edit, deletes: audited


def test_open_threads_carry_forward(client: TestClient) -> None:
    login(client, OWNER)
    open_id = start(client, "Renegotiate the lease").json()["threads"][0]["thread_id"]
    done = start(client, "Check the bonus").json()["threads"]
    done_id = next(t["thread_id"] for t in done if t["thread_id"] != open_id)
    resolved = {"version": R, "resolved": True}
    assert client.post(f"/api/notes/threads/{done_id}/resolved", json=resolved).status_code == 200
    login(client, CONTROLLER)
    new = {"code": "B2027", "name": "Budget 2027", "kind": "budget", "fiscal_year": 2027,
           "copy_from": R}  # fmt: skip
    assert client.post("/api/rounds", json=new).status_code == 200
    login(client, OWNER)
    (carried,) = read(client, "B2027")["threads"]  # the open one only
    assert carried["thread_id"] == open_id and carried["carried_from"] == "Budget 2026"
    # Written to in the new round, it is fresh: no longer carried, and the new round's.
    answer = {"version": "B2027", "body": "Still open in 2027."}
    assert client.post(f"/api/notes/threads/{open_id}/replies", json=answer).status_code == 200
    assert read(client, "B2027")["threads"][0]["carried_from"] is None
    # The earlier round no longer shows it: it belongs to B2027 now. The resolved one stays.
    assert [t["thread_id"] for t in read(client)["threads"]] == [done_id]


def test_notepad(client: TestClient) -> None:
    pad = {"version": R, "line": LINE, "rows": [{"text": "Licence A", "amount": "1200.50"},
           {"text": "Licence B", "amount": "300"}, {"text": "", "amount": None}]}  # fmt: skip
    login(client, APPROVER)  # reads CC1, doesn't plan it
    assert client.put("/api/notes/notepad", json=pad).status_code == 403
    login(client, OWNER)
    saved = client.put("/api/notes/notepad", json=pad)
    assert saved.status_code == 200, saved.text
    notepad = saved.json()["notepad"]
    assert notepad["total"] == "1500.50" and len(notepad["rows"]) == 2
    bad = {**pad, "rows": [{"text": "x", "amount": "1.234"}]}
    assert client.put("/api/notes/notepad", json=bad).status_code == 422
    start(client, "See the notepad")
    marker = rows(client.get(f"{URL}/grid").json())["6000"]["notes"]
    assert marker == {"open": 1, "resolved": 0, "task_for_me": False, "notepad": True}
    cleared = client.put("/api/notes/notepad", json={**pad, "rows": []})
    assert cleared.json()["notepad"] is None


def test_capacity_lines_take_notes(client: TestClient) -> None:
    setup = client.get(f"/api/rounds/{R}/setup").json()["setup"]
    productive = {**setup, "capacity_work_centers": ["CC1"]}
    assert client.put(f"/api/rounds/{R}/setup", json=productive).status_code == 200
    line = {"module": "capacity", "cost_center": "CC1", "work_center": "WC1",
            "activity_type": "MH"}  # fmt: skip
    body = {"version": R, "line": line, "body": "Second shift from May?"}
    assert client.post("/api/notes/threads", json=body).status_code == 200
    sheet = client.get(f"/api/versions/{R}/capacity/CC1").json()
    lathe = next(r for r in sheet["rows"] if r["kind"] == "line" and r["work_center_code"] == "WC1")
    assert lathe["notes"]["open"] == 1
    assert read(client, line=line)["threads"][0]["notes"][0]["body"] == "Second shift from May?"


# --- Item lists -----------------------------------------------------------------------------------


def make_list(client: TestClient, visibility: str = "private") -> dict:  # type: ignore[type-arg]
    body = {
        "cost_center": "CC1",
        "account": "6000",
        "visibility": visibility,
        "items": [
            {"name": "Claude Code", "supplier": "Anthropic", "quantity": "12", "unit_price": "25"},
            {"name": "Cloud", "unit_price": "1000.10", "comment": "yearly"},
        ],
    }
    response = client.post("/api/item-lists", json=body)
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


def test_item_lists_compute_and_mark_the_line(client: TestClient) -> None:
    login(client, OWNER)
    made = make_list(client)
    assert [i["amount"] for i in made["entries"]] == ["300.00", "1000.10"]
    assert made["total"] == "1300.10" and made["mine"] and made["can_edit"]
    marker = rows(client.get(f"{URL}/grid").json())["6000"]["item_list"]
    assert marker == {"list_id": made["list_id"], "total": "1300.10", "visibility": "private"}
    # One list per line.
    again = client.post("/api/item-lists", json={"cost_center": "CC1", "account": "6000"})
    assert again.status_code == 422
    # Groups take no lists; nor do readers who don't plan the cost center.
    group = client.post("/api/item-lists", json={"cost_center": "CC1", "account": "A"})
    assert group.status_code == 404
    login(client, APPROVER)
    other = client.post("/api/item-lists", json={"cost_center": "CC1", "account": "7000"})
    assert other.status_code == 403


def test_item_list_visibility(client: TestClient) -> None:
    login(client, OWNER)
    made = make_list(client)
    lid = made["list_id"]
    line = {"cost_center": "CC1", "account": "6000"}
    login(client, CONTROLLER)  # plans every cost center, but the list is private
    assert client.get("/api/item-lists/line", params=line).json() is None
    assert "item_list" not in rows(client.get(f"{URL}/grid").json())["6000"] or (
        rows(client.get(f"{URL}/grid").json())["6000"]["item_list"] is None
    )

    login(client, OWNER)
    shared = {"visibility": "cost_center", "items": made["entries"]}
    assert client.put(f"/api/item-lists/{lid}", json=shared).status_code == 200
    login(client, CONTROLLER)  # plans CC1: sees and edits it, but doesn't own it
    seen = client.get(f"/api/item-lists/{lid}").json()
    assert seen["can_edit"] and not seen["mine"]
    more = {
        "visibility": "cost_center",
        "items": [*seen["entries"], {"name": "x", "unit_price": "1"}],
    }
    assert client.put(f"/api/item-lists/{lid}", json=more).json()["total"] == "1301.10"
    assert (
        client.put(f"/api/item-lists/{lid}", json={**more, "visibility": "public"}).status_code
        == 403
    )
    assert client.delete(f"/api/item-lists/{lid}").status_code == 403
    login(client, APPROVER)  # reads CC1, doesn't plan it: no cost center lists
    assert client.get(f"/api/item-lists/{lid}").status_code == 404

    login(client, OWNER)
    public = {"visibility": "public", "items": more["items"]}
    assert client.put(f"/api/item-lists/{lid}", json=public).status_code == 200
    login(client, APPROVER)  # public: read, not written
    assert client.get(f"/api/item-lists/{lid}").json()["can_edit"] is False
    assert client.put(f"/api/item-lists/{lid}", json=public).status_code == 403
    assert [s["list_id"] for s in client.get("/api/item-lists").json()] == [lid]
    login(client, NOBODY)
    assert client.get("/api/item-lists").json() == []
    login(client, OWNER)
    assert client.delete(f"/api/item-lists/{lid}").status_code == 200
    assert client.get("/api/item-lists").json() == []


def test_deleting_the_first_note_deletes_the_thread(client: TestClient) -> None:
    login(client, OWNER)
    thread = start(client, "Question").json()["threads"][0]
    tid = thread["thread_id"]
    login(client, APPROVER)
    for body in ("Answer 1", "Answer 2"):
        reply = {"version": R, "body": body}
        assert client.post(f"/api/notes/threads/{tid}/replies", json=reply).status_code == 200
    notes = read(client)["threads"][0]["notes"]
    assert client.delete(f"/api/notes/{notes[1]['note_id']}").status_code == 200  # a reply
    assert [n["body"] for n in read(client)["threads"][0]["notes"]] == ["Question", "Answer 2"]
    login(client, OWNER)
    assert client.delete(f"/api/notes/{thread['notes'][0]['note_id']}").status_code == 200
    assert read(client)["threads"] == []  # with the approver's reply
