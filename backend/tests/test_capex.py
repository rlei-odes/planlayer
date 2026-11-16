"""Capex planning (ADR 0028).

The slice has CC1 (owned by anna) and CC2 (no owner) under DEP, where appr approves. Investments
are written by those who plan the cost center, confirmed by approvers above it and controllers.
"""

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.exc import DBAPIError

from planlayer import access, capex
from planlayer.config import Settings
from planlayer.domain import capex as rules
from planlayer.store.db import create_app_engine
from planlayer.store.schema import capex_item, capex_log
from planlayer.store.uow import unit_of_work

from .conftest import APPROVER, CONTROLLER, NOBODY, OWNER, VIEWER, login

R = "B2026"
BASE = f"/api/rounds/{R}/capex"


# --- The rules ----------------------------------------------------------------------------------


def test_an_entry_is_capped_at_what_is_left() -> None:
    jan, feb = date(2026, 1, 1), date(2026, 2, 1)
    schedule = {jan: Decimal(700)}
    assert rules.capped(Decimal(1000), schedule, feb, Decimal(500)) == Decimal(300)
    assert rules.capped(Decimal(1000), schedule, jan, Decimal(900)) == Decimal(900)
    assert rules.rest(None, {}, jan) == 0


def test_a_lower_amount_trims_the_schedule_from_the_end() -> None:
    jan, mar, nxt = date(2026, 1, 1), date(2026, 3, 1), date(2027, 1, 1)
    schedule = {jan: Decimal(400), mar: Decimal(400), nxt: Decimal(200)}
    assert rules.trimmed(schedule, Decimal(500)) == {jan: Decimal(400), mar: Decimal(100)}
    assert rules.trimmed(schedule, None) == {}


def test_quarters_schedule_by_their_first_month() -> None:
    months = [date(2026, m, 1) for m in range(1, 13)]
    assert rules.cash_out_periods(months, "quarter") == [date(2026, m, 1) for m in (1, 4, 7, 10)]


# --- Through the API -----------------------------------------------------------------------------


def _on(client: TestClient, threshold: str | None = "100000", cash_out: bool = True) -> None:
    setup = client.get(f"/api/rounds/{R}/setup").json()["setup"]
    setup["capex"] = {"project_threshold": threshold, "cash_out": cash_out}
    response = client.put(f"/api/rounds/{R}/setup", json=setup)
    assert response.status_code == 200, response.text


def _types(client: TestClient) -> None:
    response = client.put(
        "/api/capex-types",
        json=[
            {"code": "MACH", "name": "Machinery", "asset_account": "0400", "useful_life": 10},
            {"code": "IT", "name": "IT hardware", "useful_life": 3},
        ],
    )
    assert response.status_code == 200, response.text


@pytest.fixture
def on(client: TestClient) -> TestClient:
    _on(client)
    _types(client)
    return client


def _add(client: TestClient, **item: Any) -> dict[str, Any]:
    response = client.post(f"{BASE}/changes", json={"create": [item]})
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return next(i for i in body["items"] if i["item_id"] == body["created"][0])


def _item(client: TestClient, item_id: int) -> dict[str, Any]:
    items: list[dict[str, Any]] = client.get(BASE).json()["items"]
    return next(i for i in items if i["item_id"] == item_id)


def _change(client: TestClient, item_id: int, **values: Any) -> Any:
    return client.post(f"{BASE}/changes", json={"update": [{"item_id": item_id, "values": values}]})


def test_capex_is_off_until_the_setup_turns_it_on(client: TestClient) -> None:
    assert client.get(BASE).json()["on"] is False
    response = client.post(
        f"{BASE}/changes", json={"create": [{"name": "Lathe", "cost_center": "CC1"}]}
    )
    assert response.status_code == 422
    _on(client)
    assert client.get(BASE).json()["settings"] == {
        "project_threshold": "100000.00",
        "cash_out": True,
    }
    assert client.get(f"/api/rounds/{R}/setup").json()["setup"]["capex"]["cash_out"] is True


def test_planners_write_their_cost_centers_only(on: TestClient) -> None:
    login(on, OWNER)
    item = _add(
        on, name="Lathe", cost_center="CC1", type="MACH", amount="50000", start_month="2026-03-01"
    )
    assert (item["priority"], item["status"], item["can_write"], item["can_confirm"]) == (
        2,
        "open",
        True,
        False,
    )
    response = on.post(
        f"{BASE}/changes", json={"create": [{"name": "Press", "cost_center": "CC2"}]}
    )
    assert response.status_code in (403, 422)
    login(on, NOBODY)
    assert on.get(BASE).json()["items"] == []
    # Types are the controller's.
    login(on, OWNER)
    assert on.put("/api/capex-types", json=[]).status_code == 403


def test_approvers_confirm_and_planners_dont(on: TestClient) -> None:
    login(on, OWNER)
    item = _add(on, name="Lathe", cost_center="CC1", amount="50000")
    response = on.post(
        f"{BASE}/status", json={"item_ids": [item["item_id"]], "status": "confirmed"}
    )
    assert response.status_code == 403
    login(on, APPROVER)
    seen = _item(on, item["item_id"])
    assert (seen["can_write"], seen["can_confirm"]) == (False, True)
    assert _change(on, item["item_id"], amount="1").status_code == 403
    response = on.post(f"{BASE}/status", json={"item_ids": [item["item_id"]], "status": "declined"})
    assert response.status_code == 422  # a reason is needed
    response = on.post(
        f"{BASE}/status", json={"item_ids": [item["item_id"]], "status": "confirmed"}
    )
    assert response.status_code == 200, response.text
    confirmed = _item(on, item["item_id"])
    assert (confirmed["status"], confirmed["status_by"]) == ("confirmed", APPROVER)
    # A viewer reads and comments, but doesn't confirm.
    login(on, VIEWER)
    assert _item(on, item["item_id"])["can_confirm"] is False
    response = on.post(f"{BASE}/items/{item['item_id']}/comments", json={"body": "Why now?"})
    assert response.status_code == 200
    assert response.json()[0]["author"] == "View"


def test_the_database_refuses_a_planners_confirmation(migrated: Settings, on: TestClient) -> None:
    login(on, OWNER)
    item = _add(on, name="Lathe", cost_center="CC1", amount="50000")
    engine = create_app_engine(migrated)
    try:
        with engine.connect() as conn:
            owner = access.user_by_login(conn, OWNER)
        with (
            pytest.raises(DBAPIError, match="confirms investments"),
            unit_of_work(engine, owner) as conn,
        ):
            conn.execute(
                update(capex_item)
                .where(capex_item.c.item_id == item["item_id"])
                .values(status="confirmed")
            )
    finally:
        engine.dispose()


def test_a_change_of_what_was_confirmed_opens_it_again(on: TestClient) -> None:
    login(on, OWNER)
    item = _add(on, name="Lathe", cost_center="CC1", amount="50000")
    login(on, APPROVER)
    on.post(f"{BASE}/status", json={"item_ids": [item["item_id"]], "status": "confirmed"})
    login(on, OWNER)
    assert _change(on, item["item_id"], name="CNC lathe").status_code == 200
    assert _item(on, item["item_id"])["status"] == "confirmed"
    assert _change(on, item["item_id"], amount="60000").status_code == 200
    again = _item(on, item["item_id"])
    assert (again["status"], again["status_by"]) == ("open", None)


def test_a_change_read_before_someone_elses_saves_nothing(on: TestClient) -> None:
    login(on, OWNER)
    item = _add(on, name="Lathe", cost_center="CC1", amount="50000")
    assert _change(on, item["item_id"], amount="1").status_code == 200
    response = on.post(
        f"{BASE}/changes",
        json={
            "update": [
                {
                    "item_id": item["item_id"],
                    "expected": item["row_version"],
                    "values": {"amount": "2"},
                }
            ]
        },
    )
    assert response.status_code == 409
    assert _item(on, item["item_id"])["amount"] == "1.00"


def test_large_projects_sum_their_lines_and_share_one_confirmation(on: TestClient) -> None:
    login(on, OWNER)
    single = _add(on, name="New hall", cost_center="CC1", amount="250000", priority=1)
    assert single["above_threshold"] is True
    response = on.post(f"{BASE}/items/{single['item_id']}/project")
    project_id = response.json()["created"][0]
    _add(on, project_id=project_id, name="Crane", amount="50000", start_month="2026-02-01")
    project = _item(on, project_id)
    assert (project["kind"], project["amount"], project["lines"], project["priority"]) == (
        "project",
        "300000.00",
        2,
        1,
    )
    assert project["start_month"] == "2026-02-01"
    line = _item(on, single["item_id"])
    assert (line["project_id"], line["above_threshold"], line["priority"]) == (project_id, False, 1)
    # The project's cost center and priority are its lines'.
    assert _change(on, line["item_id"], priority=3).status_code == 422
    login(on, APPROVER)
    assert (
        on.post(
            f"{BASE}/status", json={"item_ids": [line["item_id"]], "status": "confirmed"}
        ).status_code
        == 422
    )
    on.post(f"{BASE}/status", json={"item_ids": [project_id], "status": "confirmed"})
    assert _item(on, line["item_id"])["status"] == "confirmed"
    # A line's new amount opens the project again.
    login(on, OWNER)
    _change(on, line["item_id"], amount="200000")
    assert _item(on, project_id)["status"] == "open"
    # The controller moves the project, its lines go along.
    login(on, CONTROLLER)
    assert _change(on, project_id, cost_center="CC2").status_code == 200
    assert {i["cost_center_code"] for i in on.get(BASE).json()["items"]} == {"CC2"}
    # Deleting the project deletes its lines.
    on.post(f"{BASE}/delete", json={"item_ids": [project_id]})
    assert on.get(BASE).json()["items"] == []


def test_cash_out_never_exceeds_the_amount(migrated: Settings, on: TestClient) -> None:
    login(on, OWNER)
    item = _add(on, name="Lathe", cost_center="CC1", amount="1000")
    url = f"{BASE}/items/{item['item_id']}/cash-out"
    over = [{"period": "2026-03-01", "amount": "800"}, {"period": "2027-01-01", "amount": "300"}]
    assert on.put(url, json={"periods": over}).status_code == 422
    fits = [{"period": "2026-03-01", "amount": "800"}, {"period": "2027-01-01", "amount": "200"}]
    assert on.put(url, json={"periods": fits}).status_code == 200
    assert (
        on.put(url, json={"periods": [{"period": "2028-01-01", "amount": "1"}]}).status_code == 422
    )
    # A lower amount cuts from the end.
    _change(on, item["item_id"], amount="900")
    after = _item(on, item["item_id"])
    assert after["cash_out"] == [
        {"period": "2026-03-01", "amount": "800.00"},
        {"period": "2027-01-01", "amount": "100.00"},
    ]
    assert after["scheduled"] == "900.00"
    # Also at the database: the schedule is checked at commit.
    engine = create_app_engine(migrated)
    try:
        with engine.connect() as conn:
            owner = access.user_by_login(conn, OWNER)
        with (
            pytest.raises(DBAPIError, match="more than the investment"),
            unit_of_work(engine, owner) as conn,
        ):
            conn.execute(
                update(capex_item).where(capex_item.c.item_id == item["item_id"]).values(amount=10)
            )
        with unit_of_work(engine, owner) as conn:
            assert conn.execute(
                select(capex_log.c.op).where(capex_log.c.item_id == item["item_id"])
            ).first()
    finally:
        engine.dispose()


def test_a_closed_round_takes_no_investments(on: TestClient) -> None:
    login(on, OWNER)
    item = _add(on, name="Lathe", cost_center="CC1", amount="1000")
    login(on, CONTROLLER)
    assert on.post(f"/api/versions/{R}/status", json={"status": "closed"}).status_code == 200
    assert on.get(BASE).json()["lock_reason"] == "the round is closed"
    assert _change(on, item["item_id"], amount="5").status_code == 422
    login(on, APPROVER)
    response = on.post(
        f"{BASE}/status", json={"item_ids": [item["item_id"]], "status": "confirmed"}
    )
    assert response.status_code == 422


def test_the_overview_sums_by_area_priority_and_confirmation(on: TestClient) -> None:
    login(on, OWNER)
    a = _add(on, name="Lathe", cost_center="CC1", type="MACH", amount="1000", priority=1)
    _add(on, name="Laptop", cost_center="CC1", type="IT", amount="200", priority=3)
    login(on, CONTROLLER)
    _add(on, name="Press", cost_center="CC2", amount="500", priority=1)
    on.post(f"{BASE}/status", json={"item_ids": [a["item_id"]], "status": "confirmed"})
    o = on.get(f"{BASE}/overview").json()
    areas = {r["code"]: r["sums"] for r in o["areas"]}
    assert areas["DEP"]["by_priority"][0] == {
        "open": "500.00",
        "confirmed": "1000.00",
        "declined": "0.00",
    }
    assert areas["CC1"]["count"] == 2
    assert {t["code"]: t["sums"]["count"] for t in o["types"]} == {"IT": 1, "MACH": 1, None: 1}
    assert o["schedule"][-1] == {
        "period": None,
        "open": "700.00",
        "confirmed": "1000.00",
        "declined": "0.00",
    }
    login(on, OWNER)
    assert [r["code"] for r in on.get(f"{BASE}/overview").json()["areas"]] == ["NW", "DEP", "CC1"]


def test_capex_goes_off_only_without_investments(on: TestClient) -> None:
    login(on, OWNER)
    item = _add(on, name="Lathe", cost_center="CC1", type="MACH", amount="1000")
    login(on, CONTROLLER)
    setup = on.get(f"/api/rounds/{R}/setup").json()["setup"]
    setup["capex"] = None
    assert on.put(f"/api/rounds/{R}/setup", json=setup).status_code == 422
    # A type in use stays.
    assert on.put("/api/capex-types", json=[]).status_code == 422
    on.post(f"{BASE}/delete", json={"item_ids": [item["item_id"]]})
    assert on.put(f"/api/rounds/{R}/setup", json=setup).status_code == 200
    assert on.get(BASE).json()["on"] is False


def test_comments_are_deleted_by_their_author_only(on: TestClient) -> None:
    login(on, OWNER)
    item = _add(on, name="Lathe", cost_center="CC1", amount="1000")
    url = f"{BASE}/items/{item['item_id']}/comments"
    comment = on.post(url, json={"body": "Quote from supplier A"}).json()[0]
    login(on, APPROVER)
    assert on.delete(f"{url}/{comment['comment_id']}").status_code == 422
    assert on.get(url).json()[0]["mine"] is False
    login(on, OWNER)
    assert on.delete(f"{url}/{comment['comment_id']}").json() == []
    assert _item(on, item["item_id"])["comments"] == 0


def test_view_lists_planned_cost_centers_the_user_reads(on: TestClient) -> None:
    login(on, OWNER)
    view = on.get(BASE).json()
    assert [(c["code"], c["can_write"]) for c in view["cost_centers"]] == [("CC1", True)]
    assert view["periods"][0] == "2026-01-01" and view["next_year"] == "2027-01-01"
    assert view["confirms"] is False
    login(on, APPROVER)
    assert on.get(BASE).json()["confirms"] is True
    assert capex.CapexError is rules.CapexError


def test_home_counts_what_waits_for_the_confirmer(on: TestClient) -> None:
    login(on, OWNER)
    _add(on, name="Lathe", cost_center="CC1", amount="1000")
    assert on.get("/api/home").json()["rounds"][0]["to_confirm"] == 0
    login(on, APPROVER)
    home = on.get("/api/home").json()
    assert home["rounds"][0]["to_confirm"] == 1
    assert home["tasks"] >= 1
