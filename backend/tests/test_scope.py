"""Preparing a round (ADR 0014): account sets, carry-over into a new round, and the scope view.

The ``client`` fixture syncs ``conftest.SOURCE`` (CC1 and CC2 under DEP; accounts 6000 and 6010
under A, 7000 under B) and opens B2026; the controller is logged in.
"""

from collections.abc import Iterator
from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, insert, select
from sqlalchemy.exc import DBAPIError

from planlayer import access, masterdata
from planlayer.config import Settings
from planlayer.store.db import create_app_engine
from planlayer.store.schema import change_log, fact_cost_center, member, version
from planlayer.store.uow import unit_of_work

from .conftest import BLANK, CONTROLLER, OWNER, URL, login, plan, rows

R = "B2026"


@pytest.fixture
def engine(migrated: Settings, client: TestClient) -> Iterator[Engine]:
    e = create_app_engine(migrated)
    yield e
    e.dispose()


def system(engine: Engine) -> access.User:
    return access.system_user(engine)


def setup_of(client: TestClient, code: str = R) -> dict:  # type: ignore[type-arg]
    return client.get(f"/api/rounds/{code}/setup").json()["setup"]  # type: ignore[no-any-return]


def save(client: TestClient, code: str = R, **changes: object) -> int:
    return client.put(
        f"/api/rounds/{code}/setup", json={**setup_of(client, code), **changes}
    ).status_code


def edit(code: str, value: str | None, m: int = 0) -> dict:  # type: ignore[type-arg]
    return {"account_code": code, "month": m, "value": value, "expected": BLANK}


# --- Account sets ---------------------------------------------------------------------------------


def test_account_set_shapes_the_sheet(engine: Engine, client: TestClient) -> None:
    personnel = {"name": "Personnel only", "accounts": ["A"], "assigned_to": ["DEP"]}
    assert save(client, account_sets=[personnel]) == 200
    assert setup_of(client)["account_sets"] == [personnel]
    cc2 = "/api/versions/B2026/cost-centers/CC2"
    grid = client.get(f"{cc2}/grid").json()
    # The set's accounts are listed without values: 6010 has none on CC2.
    assert [r["code"] for r in grid["rows"]] == ["A", "6000", "6010"]
    grid = client.get(f"{URL}/grid").json()
    # 7000 has prior-year actuals on CC1 but is outside the set: read only, still in totals.
    assert rows(grid)["7000"]["not_planned"] == "set"
    assert not rows(grid)["7000"]["total_editable"]
    assert rows(grid)["B"]["comparisons"][0][1] == "-10.00"
    refused = client.post(f"{URL}/edits", json={"edits": [edit("7000", "5")]})
    assert refused.status_code == 422 and "not in this cost center's accounts" in refused.text
    assert [a["code"] for a in client.get(f"{URL}/accounts").json()] == ["6000", "6010"]
    with pytest.raises(DBAPIError, match="not in the cost center's account set"):
        _raw(engine, "7000")
    # A set on the cost center itself wins over the node's.
    rent = {"name": "Rent", "accounts": ["7000"], "assigned_to": ["CC1"]}
    assert save(client, account_sets=[personnel, rent]) == 200
    grid = client.get(f"{URL}/grid").json()
    assert rows(grid)["7000"]["not_planned"] is None
    assert rows(grid)["6000"]["not_planned"] == "set"  # has actuals, outside "Rent"


def test_account_set_rules(client: TestClient) -> None:
    one = {"name": "One", "accounts": ["A"], "assigned_to": ["DEP"]}
    assert save(client, account_sets=[one, {**one, "assigned_to": []}]) == 422  # same name
    assert save(client, account_sets=[one, {**one, "name": "Two"}]) == 422  # DEP twice
    assert save(client, account_sets=[{**one, "accounts": ["nope"]}]) == 404


def _raw(engine: Engine, account: str, cc: str = "CC1", code: str = R) -> None:
    with unit_of_work(engine, system(engine)) as conn:
        ids = dict(conn.execute(select(member.c.code, member.c.member_id)).all())
        vid = conn.execute(select(version.c.version_id).where(version.c.code == code)).scalar_one()
        conn.execute(
            insert(fact_cost_center).values(
                version_id=vid,
                layer_id=1,
                measure_id=1,
                period=date(2026, 5, 1),
                dim_entity=ids["DE"],
                dim_cost_center=ids[cc],
                dim_account=ids[account],
                amount=Decimal(1),
            )
        )


# --- Carry-over -----------------------------------------------------------------------------------


def test_new_round_carries_over_with_uplift(engine: Engine, client: TestClient) -> None:
    year = {"account_code": "6000", "month": None, "value": "1200", "expected": BLANK}
    assert (
        client.post(f"{URL}/edits", json={"edits": [year, edit("7000", "33.33")]}).status_code
        == 200
    )
    personnel = {"name": "Personnel", "accounts": ["A"], "assigned_to": ["CC2"]}
    planned = ["CC1"]  # CC2 not planned in B2026
    assert save(client, account_sets=[personnel], planned_cost_centers=planned) == 200
    new = {
        "code": "B2027",
        "name": "Budget 2027",
        "kind": "budget",
        "fiscal_year": 2027,
        "copy_from": R,
        "carry": {"values": True, "uplift": "10"},
    }
    assert client.post("/api/rounds", json=new).status_code == 200
    s = setup_of(client, "B2027")
    assert s["account_sets"] == [personnel] and s["planned_cost_centers"] == ["CC1"]
    assert s["comparisons"][-1] == {"kind": "version", "version_code": R, "note": None}
    # The round keeps how it was created: what it carried, and how many plan values.
    created = client.get("/api/rounds/B2027/setup").json()["created"]
    assert created["carried"]["values"] is True and created["carried"]["uplift"] == "10"
    assert created["cells"] == 13 and created["by"] == CONTROLLER
    grid = client.get("/api/versions/B2027/cost-centers/CC1/grid").json()
    assert grid["status"] == "not_started"  # prepared by the controller, not started
    # B2026's plan in 2026's months, plus 10 %, in 2027's months.
    assert plan(grid, "6000") == ["110.00"] * 12
    assert plan(grid, "7000")[0] == "36.66"
    assert [c["label"] for c in grid["comparisons"]] == ["Actual 2026", "Budget 2026"]
    assert rows(grid)["6000"]["comparisons"][1] == ["100.00"] * 12
    with engine.connect() as conn:
        vid = conn.execute(select(version.c.version_id).where(version.c.code == "B2027")).scalar()
    with unit_of_work(engine, system(engine)) as conn:
        batches = conn.execute(
            select(func.count(func.distinct(change_log.c.batch_id))).where(
                change_log.c.version_id == vid
            )
        ).scalar()
    assert batches == 1  # one audited change batch


def test_new_round_without_values_or_settings(client: TestClient) -> None:
    assert client.post(f"{URL}/edits", json={"edits": [edit("6000", "5")]}).status_code == 200
    assert save(client, not_planned_accounts=["7000"]) == 200
    carry = {"setup": False, "compare_with_source": False, "accounts": False}
    new = {"code": "B2027", "name": "B", "kind": "budget", "fiscal_year": 2027}
    assert (
        client.post("/api/rounds", json={**new, "copy_from": R, "carry": carry}).status_code == 200
    )
    s = setup_of(client, "B2027")
    assert s["not_planned_accounts"] == [] and s["comparisons"] == [
        {"kind": "prior_year_actuals", "version_code": None, "note": None}
    ]
    grid = client.get("/api/versions/B2027/cost-centers/CC1/grid").json()
    assert "6000" not in rows(grid)  # no values carried, no 2026 actuals
    created = client.get("/api/rounds/B2027/setup").json()["created"]
    assert created["carried"]["setup"] is False and created["cells"] is None


def test_not_planned_in_setup_clears_carried_values(client: TestClient) -> None:
    assert client.post(f"{URL}/edits", json={"edits": [edit("7000", "30")]}).status_code == 200
    assert save(client, not_planned_accounts=["7000"]) == 422  # B2026 is open: refused
    new = {"code": "B2027", "name": "B", "kind": "budget", "fiscal_year": 2027, "copy_from": R}
    carried = {**new, "carry": {"values": True}}
    assert client.post("/api/rounds", json=carried).status_code == 200
    url = "/api/versions/B2027/cost-centers/CC1/grid"
    assert plan(client.get(url).json(), "7000")[0] == "30.00"
    assert save(client, "B2027", not_planned_accounts=["7000"]) == 200  # in setup: cleared
    assert rows(client.get(url).json())["7000"]["not_planned"] == "round"
    assert plan(client.get(url).json(), "7000") == BLANK


def test_a_set_clears_carried_values_outside_it_in_setup(client: TestClient) -> None:
    assert client.post(f"{URL}/edits", json={"edits": [edit("7000", "30")]}).status_code == 200
    personnel = {"name": "Personnel", "accounts": ["A"], "assigned_to": ["CC1"]}
    assert save(client, account_sets=[personnel]) == 422  # B2026 is open: 7000 has values
    new = {"code": "B2027", "name": "B", "kind": "budget", "fiscal_year": 2027, "copy_from": R}
    assert client.post("/api/rounds", json={**new, "carry": {"values": True}}).status_code == 200
    assert save(client, "B2027", account_sets=[personnel]) == 200
    grid = client.get("/api/versions/B2027/cost-centers/CC1/grid").json()
    assert rows(grid)["7000"]["not_planned"] == "set" and plan(grid, "7000") == BLANK


def test_planners_wait_for_the_round_to_open(engine: Engine, client: TestClient) -> None:
    new = {"code": "B2027", "name": "B", "kind": "budget", "fiscal_year": 2027, "copy_from": R}
    assert client.post("/api/rounds", json=new).status_code == 200
    login(client, OWNER)
    url = "/api/versions/B2027/cost-centers/CC1/edits"
    assert client.post(url, json={"edits": [edit("6000", "1")]}).status_code == 423


# --- Scope ----------------------------------------------------------------------------------------


def test_scope_marks_new_and_missing_cost_centers(engine: Engine, client: TestClient) -> None:
    masterdata.add_member(engine, system(engine), "cost_centers", "CC3", "Paint shop", parent="DEP")
    new = {"code": "B2027", "name": "B", "kind": "budget", "fiscal_year": 2027, "copy_from": R}
    assert client.post("/api/rounds", json=new).status_code == 200
    sc = client.get("/api/rounds/B2027/scope").json()
    assert sc["source_round"] == R
    cc = {r["code"]: r for r in sc["cost_centers"]}
    assert [c for c, r in cc.items() if r["new"]] == ["CC3"]
    assert cc["CC3"]["planned"] is True and cc["DEP"]["planned"] is None
    assert cc["CC1"]["prior_actuals"] is None  # no 2026 actuals in the source
    b26 = {r["code"]: r for r in client.get(f"/api/rounds/{R}/scope").json()["cost_centers"]}
    assert Decimal(b26["CC1"]["prior_actuals"]) == Decimal("1270.00")
    assert Decimal(b26["DEP"]["prior_actuals"]) == Decimal("2269.00")  # rolled up
    accounts = {r["code"]: r for r in client.get(f"/api/rounds/{R}/scope").json()["accounts"]}
    assert accounts["7000"]["planned"] is True and Decimal(accounts["B"]["prior_actuals"]) == 20
    login(client, OWNER)
    assert client.get(f"/api/rounds/{R}/scope").status_code == 403


def test_admin_without_cost_centers_cant_prepare(engine: Engine, client: TestClient) -> None:
    admin = replace(system(engine), is_admin=True)
    access.create_user(engine, admin, "root@x.example", "Root", is_admin=True)
    login(client, "root@x.example")
    assert client.get(f"/api/rounds/{R}/scope").status_code == 403
