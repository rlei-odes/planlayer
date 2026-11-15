"""Capacity from the workforce (ADR 0027): hours from positions, written to a line of the
cost center, and the credits that follow.

CC1's people from HR (see test_personnel): P001 (100 %), P002 (50 %), P003 (80 %, leaving end
of June) and P004 (a freelancer, no contract %). CC1's lines (see test_capacity): WC1 machine
hours at 80 (credited to 4900), WC2 machine hours at 100.50 and WC2 repair work at 60 (no
account). The round's hours per FTE: 1,800.
"""

from collections.abc import Iterator
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, insert, select
from sqlalchemy.exc import DBAPIError

from planlayer import access
from planlayer.config import Settings
from planlayer.store.db import create_app_engine
from planlayer.store.schema import COST_CENTER, capacity_position, member, version
from planlayer.store.uow import unit_of_work

from .conftest import BLANK, CONTROLLER, NOBODY, OWNER, VIEWER, login

R = "B2026"
CAP = f"/api/versions/{R}/capacity/CC1"
STAFF = f"{CAP}/workforce"
WORKFORCE = f"/api/versions/{R}/workforce/CC1"
PLANNER = "cap@x.example"
WC1 = {"work_center_code": "WC1", "activity_type_code": "MH"}
REPAIR = {"work_center_code": "WC2", "activity_type_code": "RW"}


@pytest.fixture
def engine(migrated: Settings, client: TestClient) -> Iterator[Engine]:
    e = create_app_engine(migrated)
    admin = replace(access.system_user(e), is_admin=True)
    access.create_user(e, admin, PLANNER, "Cap")
    yield e
    e.dispose()


def save(client: TestClient, code: str = R, **changes: object) -> object:
    setup = client.get(f"/api/rounds/{code}/setup").json()["setup"]
    return client.put(f"/api/rounds/{code}/setup", json={**setup, **changes})


def start(client: TestClient, **changes: object) -> dict:  # type: ignore[type-arg]
    """Positions with HR's staff on CC1; CC1's hours typed and from its positions."""
    body = {
        "workforce_mode": "positions",
        "capacity_work_centers": ["CC1"],
        "capacity_workforce": ["CC1"],
        "fte_hours": "1800",
        **changes,
    }
    assert save(client, **body).status_code == 200  # type: ignore[attr-defined]
    assert client.post(f"{WORKFORCE}/hr").status_code == 200
    return client.get(STAFF).json()  # type: ignore[no-any-return]


def by_code(view: dict) -> dict[str, dict]:  # type: ignore[type-arg]
    return {p["code"]: p for p in view["positions"]}


def line(sheet: dict, key: str) -> dict:  # type: ignore[type-arg]
    return next(
        r
        for r in sheet["rows"]
        if r["kind"] == "line" and f"{r['work_center_code']}/{r['activity_type_code']}" == key
    )


def months(sheet: dict, key: str) -> list[str | None]:  # type: ignore[type-arg]
    return [c["value"] for c in line(sheet, key)["plan"]]


def credit(sheet: dict, activity_type: str) -> list[str | None]:  # type: ignore[type-arg]
    row = next(
        r
        for r in sheet["rows"]
        if r["kind"] == "credit" and r["activity_type_code"] == activity_type
    )
    return [c["value"] for c in row["plan"]]


def test_hours_from_positions(engine: Engine, client: TestClient) -> None:
    view = start(client)
    assert view["fte_hours"] == "1800.00" and view["typed_hours"] is True
    assert sorted(by_code(view)) == ["P001", "P002", "P003", "P004"]
    assert {p["hours"] for p in view["positions"]} == {None}  # no line, no share yet
    units = client.get(f"/api/versions/{R}/capacity").json()
    assert [(u["code"], u["staff_hours"]) for u in units] == [("CC1", True)]

    # Every position to repair work at 80 %: 1,800 x contract % x 80 %, the planner until June.
    # The freelancer has no contract %: no share applies, his hours are typed.
    view = client.put(STAFF, json={"line": REPAIR, "percent": "80"}).json()
    hours = {c: p["hours"] for c, p in by_code(view).items()}
    assert hours == {"P001": "1440.00", "P002": "720.00", "P003": "576.00", "P004": None}
    repair = next(x for x in view["lines"] if x["activity_type_code"] == "RW")
    assert repair["hours"] == "2736.00" and repair["rate"] == "60.00"
    sheet = client.get(CAP).json()
    assert sheet["staff_hours"] is True
    assert line(sheet, "WC2/RW")["staffed"] is True
    assert months(sheet, "WC2/RW") == ["276.00"] * 6 + ["180.00"] * 6
    assert line(sheet, "WC1/MH")["staffed"] is False

    # A manager gives no hours; the freelancer runs the lathe, which credits 4900.
    ids = {c: p["position_id"] for c, p in by_code(view).items()}
    view = client.put(
        f"{STAFF}/positions/{ids['P001']}", json={"line": None, "percent": "0"}
    ).json()
    assert by_code(view)["P001"]["hours"] == "0.00"
    url = f"{STAFF}/positions/{ids['P004']}"
    assert client.put(url, json={"line": WC1, "percent": "80"}).status_code == 422
    url_p001 = f"{STAFF}/positions/{ids['P001']}"
    assert (
        client.put(url_p001, json={"line": None, "percent": None, "hours": "5"}).status_code == 422
    )
    view = client.put(url, json={"line": WC1, "percent": None, "hours": "1200"}).json()
    p004 = by_code(view)["P004"]
    assert p004["work_center_code"] == "WC1" and p004["typed_hours"] == "1200.00"
    assert p004["hours"] == "1200.00"
    sheet = client.get(CAP).json()
    assert months(sheet, "WC1/MH") == ["100.00"] * 12
    assert credit(sheet, "MH") == ["8000.00"] * 12
    # Hours from positions aren't typed; other lines still are.
    edit = {"work_center_code": "WC1", "activity_type_code": "MH", "month": 0, "value": "5",
            "expected": BLANK}  # fmt: skip
    assert client.post(f"{CAP}/edits", json={"edits": [edit]}).status_code == 422
    edit = {**edit, "work_center_code": "WC2"}
    assert client.post(f"{CAP}/edits", json={"edits": [edit]}).status_code == 200
    assert credit(client.get(CAP).json(), "MH")[:2] == ["8502.50", "8000.00"]

    # Back to the default line: the lathe takes no hours from positions any more.
    client.put(url, json={"line": None, "percent": None, "hours": "1200"})
    sheet = client.get(CAP).json()
    assert months(sheet, "WC1/MH") == BLANK and line(sheet, "WC1/MH")["staffed"] is False
    assert credit(sheet, "MH")[:2] == ["502.50", None]
    # A spread by profile weights the months, and the year stays: the planner works the
    # whole first half, so all of her hours count; the freelancer's 1,200 fall in it.
    profile = {"code": "H1", "name": "First half", "entered_as": "relative",
               "values": [1] * 6 + [0] * 6}  # fmt: skip
    assert client.post("/api/profiles", json=profile).status_code == 200
    spread = {"spread": {"basis": "profile", "profile_code": "H1"}}
    assert client.patch(f"{CAP}/lines/WC2/RW", json=spread).status_code == 200
    staffed = months(client.get(CAP).json(), "WC2/RW")
    assert staffed == ["512.00"] * 6 + ["0.00"] * 6  # (720 + 1152 + 1200) / 6


def test_position_changes_follow(engine: Engine, client: TestClient) -> None:
    start(client)
    client.put(STAFF, json={"line": REPAIR, "percent": "100"})
    sheet = client.get(WORKFORCE).json()
    p002 = next(p for p in sheet["positions"] if p["code"] == "P002")
    # Eva goes full time: 900 more hours.
    body = {k: p002[k] for k in ("title", "staff_type_code", "staff_category_code", "person_name",
                                 "entry_date", "leave_date", "description")}  # fmt: skip
    body["contract_percent"] = "100"
    url = f"{WORKFORCE}/positions/{p002['position_id']}"
    assert client.put(url, json=body).status_code == 200
    view = client.get(STAFF).json()
    assert by_code(view)["P002"]["hours"] == "1800.00"
    assert next(x for x in view["lines"] if x["activity_type_code"] == "RW")["hours"] == "4320.00"
    # A position removed takes its hours along.
    assert client.delete(url).status_code == 200
    view = client.get(STAFF).json()
    assert next(x for x in view["lines"] if x["activity_type_code"] == "RW")["hours"] == "2520.00"


def test_setup_rules(engine: Engine, client: TestClient) -> None:
    # Hours from positions need positions and the hours per FTE.
    assert save(client, capacity_workforce=["CC1"], fte_hours="1800").status_code == 422  # type: ignore[attr-defined]
    body = {"workforce_mode": "positions", "capacity_workforce": ["CC1"]}
    assert save(client, **body).status_code == 422  # type: ignore[attr-defined]
    assert save(client, **body, fte_hours="0").status_code == 422  # type: ignore[attr-defined]
    # From positions only: CC1 is productive, but hours aren't typed.
    start(client, capacity_work_centers=[])
    setup = client.get(f"/api/rounds/{R}/setup").json()["setup"]
    assert setup["capacity_work_centers"] == [] and setup["capacity_workforce"] == ["CC1"]
    assert setup["fte_hours"] == "1800.00"
    assert client.get(CAP).json()["typed_hours"] is False
    edit = {"work_center_code": "WC2", "activity_type_code": "MH", "month": 0, "value": "5",
            "expected": BLANK}  # fmt: skip
    assert client.post(f"{CAP}/edits", json={"edits": [edit]}).status_code == 422
    client.put(STAFF, json={"line": REPAIR, "percent": "100"})
    # Fewer hours per FTE: the hours follow.
    assert save(client, fte_hours="1700").status_code == 200  # type: ignore[attr-defined]
    view = client.get(STAFF).json()
    assert by_code(view)["P001"]["hours"] == "1700.00"
    # Typed again, and then no longer from positions: the hours from positions go.
    body = {"capacity_work_centers": ["CC1"], "capacity_workforce": []}
    assert save(client, **body).status_code == 200  # type: ignore[attr-defined]
    sheet = client.get(CAP).json()
    assert sheet["staff_hours"] is False and months(sheet, "WC2/RW") == BLANK
    assert line(sheet, "WC2/RW")["staffed"] is False
    assert client.get(STAFF).status_code == 404
    # Typed hours stay where hours are typed no longer: refused.
    assert client.post(f"{CAP}/edits", json={"edits": [edit]}).status_code == 200
    body = {"capacity_work_centers": [], "capacity_workforce": ["CC1"]}
    response = save(client, **body)
    assert response.status_code == 422 and "typed hours on CC1" in response.text  # type: ignore[attr-defined]


def test_who_plans_hours_from_positions(engine: Engine, client: TestClient) -> None:
    view = start(client)
    p001 = by_code(view)["P001"]["position_id"]
    body = {"line": REPAIR, "percent": "80"}
    url = f"{STAFF}/positions/{p001}"
    # A capacity planner plans them; a viewer reads them; others don't see them.
    grant = {"login": PLANNER}
    assert client.post("/api/responsibilities/CC1/capacity-planners", json=grant).status_code == 200
    login(client, PLANNER)
    assert client.put(STAFF, json=body).status_code == 200
    assert client.put(url, json={"line": None, "percent": "50"}).status_code == 200
    login(client, OWNER)
    assert by_code(client.get(STAFF).json())["P001"]["hours"] == "900.00"
    login(client, VIEWER)
    assert client.get(STAFF).status_code == 200
    assert client.put(url, json={"line": None, "percent": "60"}).status_code == 423
    login(client, NOBODY)
    assert client.get(STAFF).status_code == 404
    # A position of another cost center isn't one of CC1's.
    login(client, CONTROLLER)
    assert client.post(f"/api/versions/{R}/workforce/CC2/hr").status_code == 200
    p009 = next(
        p["position_id"]
        for p in client.get(f"/api/versions/{R}/workforce/CC2").json()["positions"]
        if p["code"] == "P009"
    )
    assert client.put(f"{STAFF}/positions/{p009}", json=body).status_code == 404
    # The store refuses settings for a cost center that isn't productive.
    with engine.connect() as c:
        controller = access.user_by_login(c, CONTROLLER)
        cc2 = c.execute(
            select(member.c.member_id).where(member.c.dim_id == COST_CENTER, member.c.code == "CC2")
        ).scalar_one()
        vid = c.execute(select(version.c.version_id).where(version.c.code == R)).scalar_one()
    with (
        pytest.raises(DBAPIError, match="not productive"),
        unit_of_work(engine, controller) as conn,
    ):
        conn.execute(
            insert(capacity_position).values(
                position_id=p009, version_id=vid, cost_center_id=cc2, productive_percent=50
            )
        )


def test_a_new_round_carries_hours_from_positions(engine: Engine, client: TestClient) -> None:
    view = start(client)
    client.put(STAFF, json={"line": REPAIR, "percent": "80"})
    p002 = by_code(view)["P002"]["position_id"]
    client.put(f"{STAFF}/positions/{p002}", json={"line": WC1, "percent": "100"})
    p004 = by_code(view)["P004"]["position_id"]
    client.put(f"{STAFF}/positions/{p004}", json={"line": None, "percent": None, "hours": "600"})
    new = {"code": "B2027", "name": "Budget 2027", "kind": "budget", "fiscal_year": 2027,
           "copy_from": R}  # fmt: skip
    assert client.post("/api/rounds", json=new).status_code == 200
    view = client.get("/api/versions/B2027/capacity/CC1/workforce").json()
    assert view["fte_hours"] == "1800.00" and view["default_percent"] == "80.00"
    # The planner who leaves in June 2026 stays behind; Eva keeps the lathe.
    hours = {c: p["hours"] for c, p in by_code(view).items()}
    assert hours == {"P001": "1440.00", "P002": "900.00", "P004": "600.00"}
    sheet = client.get("/api/versions/B2027/capacity/CC1").json()
    assert months(sheet, "WC1/MH") == ["75.00"] * 12
    assert credit(sheet, "MH") == ["6000.00"] * 12
