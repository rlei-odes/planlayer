"""Item lists move, are judged against a round, and change hands (ADR 0036).

The ``client`` fixture syncs ``conftest.SOURCE`` (CC1 owned by anna, CC2 without an owner;
accounts 6000, 6010, 7000); B2026 is open and the controller is logged in.
"""

from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from planlayer import access
from planlayer.config import Settings
from planlayer.store.db import create_app_engine

from .conftest import CONTROLLER, NOBODY, OWNER, login

LISTS = "/api/item-lists"


@pytest.fixture
def engine(migrated: Settings, client: TestClient) -> Iterator[Engine]:
    e = create_app_engine(migrated)
    yield e
    e.dispose()


def create(client: TestClient, cc: str, account: str, visibility: str = "private") -> int:
    body = {
        "cost_center": cc,
        "account": account,
        "visibility": visibility,
        "items": [{"kind": "item", "name": "Licence", "quantity": "2", "unit_price": "50"}],
    }
    response = client.post(LISTS, json=body)
    assert response.status_code == 200, response.text
    return int(response.json()["list_id"])


def move(client: TestClient, list_id: int, cc: str, account: str) -> Any:
    return client.post(f"{LISTS}/{list_id}/move", json={"cost_center": cc, "account": account})


def summary(client: TestClient, list_id: int, **params: str) -> dict[str, Any]:
    found: list[dict[str, Any]] = client.get(LISTS, params=params).json()
    return next(s for s in found if s["list_id"] == list_id)


def test_the_owner_moves_a_list_to_a_line_they_plan(client: TestClient) -> None:
    login(client, OWNER)
    lid = create(client, "CC1", "6000", "cost_center")
    moved = move(client, lid, "CC1", "6010")
    assert moved.status_code == 200, moved.text
    body = moved.json()
    assert (body["account_code"], body["total"], body["visibility"]) == (
        "6010",
        "100.00",
        "cost_center",
    )
    assert move(client, lid, "CC2", "6000").status_code == 403  # anna doesn't plan CC2
    other = create(client, "CC1", "7000")
    taken = move(client, other, "CC1", "6010")
    assert taken.status_code == 422 and "has an item list already" in taken.text
    # Only the owner moves it, even a controller who edits the shared list.
    login(client, CONTROLLER)
    assert move(client, lid, "CC1", "6000").status_code == 403


def test_a_list_says_why_its_line_isnt_planned_in_a_round(client: TestClient) -> None:
    lid = create(client, "CC1", "7000")
    on_cc2 = create(client, "CC2", "6000")
    assert summary(client, lid, round="B2026")["unplaced"] is None
    setup = client.get("/api/rounds/B2026/setup").json()["setup"]
    saved = client.put("/api/rounds/B2026/setup", json={**setup, "not_planned_accounts": ["7000"]})
    assert saved.status_code == 200, saved.text
    assert (
        client.post(
            "/api/versions/B2026/cost-centers/CC2/enabled", json={"enabled": False}
        ).status_code
        == 200
    )
    assert summary(client, lid, round="B2026")["unplaced"] == "account_not_planned"
    assert summary(client, on_cc2, round="B2026")["unplaced"] == "cost_center_not_planned"
    assert summary(client, lid)["unplaced"] is None  # without a round, nothing to judge against
    # Moved to a line the round plans, it is placed again.
    assert move(client, lid, "CC1", "6010").status_code == 200
    assert summary(client, lid, round="B2026")["unplaced"] is None


def test_a_controller_takes_over_a_disabled_owners_list(engine: Engine, client: TestClient) -> None:
    login(client, OWNER)
    lid = create(client, "CC1", "6000")
    login(client, CONTROLLER)
    assert all(s["list_id"] != lid for s in client.get(LISTS).json())  # private to anna
    assert client.get(f"{LISTS}/{lid}/owners").status_code == 404
    admin = replace(access.system_user(engine), is_admin=True)
    access.update_user(engine, admin, OWNER, disabled=True)
    found = summary(client, lid)
    assert (found["owner_disabled"], found["can_take_over"]) == (True, True)
    candidates = [c["login"] for c in client.get(f"{LISTS}/{lid}/owners").json()]
    assert CONTROLLER in candidates and OWNER not in candidates
    # Not to someone who doesn't plan the cost center.
    refused = client.post(f"{LISTS}/{lid}/owner", json={"login": NOBODY})
    assert refused.status_code == 403
    assert client.post(f"{LISTS}/{lid}/owner", json={"login": CONTROLLER}).status_code == 200
    taken = summary(client, lid)
    assert (taken["mine"], taken["owner_disabled"], taken["can_take_over"]) == (True, False, False)
    # The owner is active now: the list is theirs to hand on, not a controller's to take.
    access.update_user(engine, admin, OWNER, disabled=False)
    login(client, OWNER)
    assert client.post(f"{LISTS}/{lid}/owner", json={"login": OWNER}).status_code == 403
