"""The start page (ADR 0021): tasks and the cost centers a user plans."""

from fastapi.testclient import TestClient

from .conftest import APPROVER, NOBODY, OWNER, VIEWER, login
from .test_workforce import mode

R = "B2026"


def round_of(home: dict) -> dict:  # type: ignore[type-arg]
    return next(w for w in home["rounds"] if w["round"]["code"] == R)


def test_planners_see_their_cost_centers_and_sheets(client: TestClient) -> None:
    mode(client, "headcount")
    login(client, OWNER)
    work = round_of(client.get("/api/home").json())
    assert [c["code"] for c in work["cost_centers"]] == ["CC1"]
    assert work["cost_centers"][0]["sheets"][-2:] == ["workforce", "planning"]
    assert work["counts"] is None
    client.post(f"/api/versions/{R}/workflow", json={"action": "submit", "cost_centers": ["CC1"]})
    login(client, APPROVER)
    work = round_of(client.get("/api/home").json())
    home = client.get("/api/home").json()
    work = round_of(home)
    assert work["cost_centers"] == [] and work["to_approve"] == 1 and home["tasks"] == 1
    login(client, VIEWER)
    counts = round_of(client.get("/api/home").json())["counts"]
    assert counts["submitted"] == 1 and counts["not_started"] == 1


def test_nobody_has_no_rounds(client: TestClient) -> None:
    login(client, NOBODY)
    home = client.get("/api/home").json()
    assert home == {"tasks": 0, "transfers": [], "rounds": []}
