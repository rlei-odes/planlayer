"""Users, login and who may do what (ADR 0009), through the API and in the database.

The ``client`` fixture's users: the controller, ``anna`` (owner of CC1, matched by e-mail), the
approver at DEP, a viewer and bob (no rights). CC2 has no owner.
"""

from collections.abc import Iterator
from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, insert, select, text
from sqlalchemy.exc import DBAPIError

from planlayer import access
from planlayer.api.app import create_app
from planlayer.config import Settings
from planlayer.store.db import create_app_engine
from planlayer.store.schema import fact_cost_center, member, version
from planlayer.store.uow import system_rights, unit_of_work

from .conftest import APPROVER, BLANK, CONTROLLER, NOBODY, OWNER, OWNER_EMAIL, VIEWER, login

V = "/api/versions/B2026"


@pytest.fixture
def engine(migrated: Settings, client: TestClient) -> Iterator[Engine]:
    e = create_app_engine(migrated)
    yield e
    e.dispose()


def units(client: TestClient) -> list[tuple[str, bool]]:
    return [(u["code"], u["can_write"]) for u in client.get(f"{V}/cost-centers").json()]


def edit(client: TestClient, cc: str, value: str = "1") -> int:
    body = {"edits": [{"account_code": "6000", "month": 0, "value": value, "expected": BLANK}]}
    return client.post(f"{V}/cost-centers/{cc}/edits", json=body).status_code


def act(client: TestClient, action: str, cc: str, comment: str | None = None) -> int:
    body = {"action": action, "cost_centers": [cc], "comment": comment}
    return client.post(f"{V}/workflow", json=body).status_code


def user(engine: Engine, login_: str) -> access.User:
    with engine.connect() as conn:
        return access.user_by_login(conn, login_)


# --- Login ----------------------------------------------------------------------------------------


def test_requests_need_a_login(client: TestClient) -> None:
    client.post("/api/auth/logout")
    assert client.get("/api/versions").status_code == 401
    assert client.get("/api/me").status_code == 401
    assert client.get("/api/health").status_code == 200
    login(client, OWNER)
    assert client.get("/api/me").json()["email"] == OWNER_EMAIL
    assert client.post("/api/auth/demo-login", json={"login": "system"}).status_code == 422


def test_me_says_whether_any_cost_center_is_readable(client: TestClient, engine: Engine) -> None:
    # The admin role alone reads no cost center (ADR 0009); roles combine.
    admin = replace(access.system_user(engine), is_admin=True)
    access.create_user(engine, admin, "it@x.example", "IT", is_admin=True)
    for who, reads in [
        (OWNER, True),
        (APPROVER, True),
        (VIEWER, True),
        (NOBODY, False),
        ("it@x.example", False),
    ]:
        login(client, who)
        assert client.get("/api/me").json()["reads_cost_centers"] is reads, who


def test_demo_login_only_in_a_demo(migrated: Settings, client: TestClient) -> None:
    with TestClient(create_app(migrated.model_copy(update={"demo": False}))) as c:
        assert c.get("/api/auth/options").json()["demo"] is False
        assert c.post("/api/auth/demo-login", json={"login": CONTROLLER}).status_code == 404
        assert c.get("/api/auth/demo-users").status_code == 404


def test_a_failure_under_system_rights_keeps_its_error(client: TestClient, engine: Engine) -> None:
    # The rights aren't restored in a failed transaction: that would hide the error behind
    # "current transaction is aborted".
    controller = user(engine, CONTROLLER)
    with (
        pytest.raises(DBAPIError) as raised,
        unit_of_work(engine, controller) as conn,
        system_rights(conn),
    ):
        conn.execute(text("SELECT 1 / 0"))
    assert "division by zero" in str(raised.value)


def test_demo_session_works_over_plain_http(migrated: Settings, client: TestClient) -> None:
    # Browsers drop Secure cookies on plain HTTP other than localhost: the demo on a LAN host.
    demo = migrated.model_copy(update={"demo": True, "environment": "production"})
    with TestClient(create_app(demo), base_url="http://primeb:8000") as c:
        response = c.post("/api/auth/demo-login", json={"login": CONTROLLER})
        assert response.status_code == 200, response.text
        assert "secure" not in response.headers["set-cookie"].lower()
        assert c.get("/api/me").json()["login"] == CONTROLLER


# --- Scopes ---------------------------------------------------------------------------------------


def test_owner_plans_only_their_cost_center(client: TestClient) -> None:
    login(client, OWNER)
    assert units(client) == [("CC1", True)]
    assert client.get(f"{V}/cost-centers/CC2/grid").status_code == 404
    assert edit(client, "CC2") == 404
    assert edit(client, "CC1") == 200
    assert act(client, "submit", "CC1") == 200
    assert act(client, "approve", "CC1") == 403  # not their own
    assert client.get("/api/responsibilities").status_code == 403


def test_approver_reads_below_their_node_and_approves(client: TestClient) -> None:
    login(client, OWNER)
    edit(client, "CC1")
    act(client, "submit", "CC1")
    login(client, APPROVER)
    assert units(client) == [("CC1", False), ("CC2", False)]
    grid = client.get(f"{V}/cost-centers/CC1/grid").json()
    assert grid["rows"][1]["plan"][0]["value"] == "1.00"
    assert grid["lock_reason"] == "the cost center is submitted"
    rows = {r["code"]: r for r in client.get(f"{V}/overview").json()["rows"]}
    assert rows["CC1"]["actions"] == ["approve", "reject"]
    assert rows["CC2"]["actions"] == []  # not submitted; and the approver doesn't plan it
    assert act(client, "submit", "CC2") == 403
    assert act(client, "approve", "CC1") == 200
    assert act(client, "reopen", "CC1", "why") == 403
    login(client, CONTROLLER)
    assert act(client, "reopen", "CC1", "why") == 200


def test_viewer_reads_all_writes_nothing(client: TestClient) -> None:
    login(client, VIEWER)
    assert units(client) == [("CC1", False), ("CC2", False)]
    grid = client.get(f"{V}/cost-centers/CC2/grid").json()
    assert grid["lock_reason"] == "you may only read this cost center"
    assert edit(client, "CC2") == 423
    rows = client.get(f"{V}/overview").json()["rows"]
    assert all(r["actions"] == [] for r in rows)


def test_user_without_rights_sees_nothing(client: TestClient) -> None:
    login(client, NOBODY)
    assert units(client) == []
    assert client.get(f"{V}/cost-centers/CC1/grid").status_code == 404
    assert client.get(f"{V}/overview").json()["rows"] == []


def test_controller_plans_every_cost_center(client: TestClient) -> None:
    assert units(client) == [("CC1", True), ("CC2", True)]
    assert edit(client, "CC2") == 200
    assert (
        client.post(f"{V}/status", json={"status": "closed", "unapproved_ok": True}).status_code
        == 200
    )
    login(client, OWNER)
    assert client.post(f"{V}/status", json={"status": "open"}).status_code == 403


# --- Row-level security ---------------------------------------------------------------------------


def _count(engine: Engine, actor: access.User | None) -> int:
    query = select(func.count()).select_from(fact_cost_center)
    if actor is None:
        with engine.connect() as conn:
            return int(conn.execute(query).scalar_one())
    with unit_of_work(engine, actor) as conn:
        return int(conn.execute(query).scalar_one())


def test_rls_filters_reads_and_refuses_writes(engine: Engine, client: TestClient) -> None:
    # Synced actuals: 15 cells for CC1, 1 for CC2.
    assert _count(engine, None) == 0  # no user in the context
    assert _count(engine, user(engine, NOBODY)) == 0
    assert _count(engine, user(engine, OWNER)) == 15
    assert _count(engine, user(engine, VIEWER)) == 16
    with unit_of_work(engine, user(engine, OWNER)) as conn:
        ids = dict(conn.execute(select(member.c.code, member.c.member_id)).all())
        vid = conn.execute(select(version.c.version_id).where(version.c.code == "B2026")).scalar()
    row = {
        "version_id": vid,
        "layer_id": 1,
        "measure_id": 1,
        "period": date(2026, 1, 1),
        "dim_entity": ids["DE"],
        "dim_account": ids["6000"],
        "amount": Decimal(1),
    }
    owner, viewer = user(engine, OWNER), user(engine, VIEWER)
    with (
        pytest.raises(DBAPIError, match="row-level security"),
        unit_of_work(engine, owner) as conn,
    ):
        conn.execute(insert(fact_cost_center).values(**row, dim_cost_center=ids["CC2"]))
    with (
        pytest.raises(DBAPIError, match="row-level security"),
        unit_of_work(engine, viewer) as conn,
    ):
        conn.execute(insert(fact_cost_center).values(**row, dim_cost_center=ids["CC1"]))


# --- Responsibilities -----------------------------------------------------------------------------


def test_owner_override_and_deputy(client: TestClient) -> None:
    r = client.get("/api/responsibilities").json()
    cc1 = next(row for row in r["rows"] if row["code"] == "CC1")
    assert (cc1["owner"], cc1["matched"], cc1["planner"]) == (OWNER_EMAIL, OWNER, OWNER)
    assert "bob@x.example" in r["users"] and "system" not in r["users"]

    client.put("/api/responsibilities/CC1/owner", json={"login": NOBODY})
    login(client, OWNER)
    assert units(client) == []
    login(client, NOBODY)
    assert units(client) == [("CC1", True)]

    login(client, CONTROLLER)
    client.delete("/api/responsibilities/CC1/owner")
    client.post("/api/responsibilities/DEP/deputies", json={"login": NOBODY})
    login(client, NOBODY)
    assert units(client) == [("CC1", True), ("CC2", True)]  # deputy for the whole node
    login(client, OWNER)
    assert units(client) == [("CC1", True)]  # back to the matched owner

    login(client, CONTROLLER)
    client.put("/api/responsibilities/CC1/owner", json={"login": None})
    login(client, OWNER)
    assert units(client) == []


def test_approver_only_on_nodes(client: TestClient) -> None:
    assert client.post(
        "/api/responsibilities/CC1/approvers", json={"login": VIEWER}
    ).status_code == (422)
    assert client.post(
        "/api/responsibilities/NW/approvers", json={"login": VIEWER}
    ).status_code == (200)


# --- Users ----------------------------------------------------------------------------------------


def test_only_admins_manage_users(engine: Engine, client: TestClient) -> None:
    assert client.get("/api/users").status_code == 403
    admin = replace(access.system_user(engine), is_admin=True)
    access.create_user(engine, admin, "root@x.example", "Root", is_admin=True)
    login(client, "root@x.example")
    access.add_deputy(engine, access.system_user(engine), "DEP", NOBODY)
    listed = {u["login"]: u for u in client.get("/api/users").json()}
    assert "system" not in listed and OWNER in listed
    # Planners have no role: what they plan comes from owning and deputising.
    assert (listed[OWNER]["owns"], listed[OWNER]["deputy"]) == (1, 0)
    assert (listed[NOBODY]["owns"], listed[NOBODY]["deputy"]) == (0, 1)
    new = {"login": "New@X.example", "display_name": "New", "is_viewer": True}
    assert client.post("/api/users", json=new).status_code == 200
    assert client.post("/api/users", json=new).status_code == 422  # exists (logins are lowercase)
    assert client.patch("/api/users/root@x.example", json={"disabled": True}).status_code == 422

    # Disabling a user ends their sessions, and an owner who is disabled no longer matches.
    other = TestClient(client.app)
    login(other, OWNER)
    assert client.patch("/api/users/anna", json={"disabled": True}).status_code == 200
    assert other.get("/api/me").status_code == 401
    login(client, CONTROLLER)
    assert client.get("/api/responsibilities").json()["unmatched_owners"] == [OWNER_EMAIL]
