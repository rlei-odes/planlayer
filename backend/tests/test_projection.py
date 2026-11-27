"""Projection comparison columns: actuals to a month, extrapolated to the full year.

The ``client`` fixture syncs ``conftest.SOURCE``: 2025 actuals for CC1 (6000 = 100.00 every month,
6010 = 50.00 in January, 7000 = -10.00 in February and 30.00 in March); B2026 is open and the
controller is logged in.
"""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response

from planlayer.domain.projection import project

from .conftest import URL, rows

D = Decimal


def test_flows_continue_at_their_monthly_average() -> None:
    months: list[Decimal | None] = [D("10.00"), None, D("30.00")] + [D("999")] * 9
    # 40.00 over three months: 160.00 for the year, months after the third ignored.
    projected = project(months, 3)
    assert projected[:3] == [D("10.00"), None, D("30.00")]
    assert projected[3:] == [D("13.34")] * 3 + [D("13.33")] * 6
    assert sum(v for v in projected if v is not None) == D("160.00")
    # 20.00 over three months, 60.00 more in nine: cents tie to the remainder.
    assert sum(project([None, D("-10"), D("30")] + [None] * 9, 3)[3:], D(0)) == D("60.00")
    assert project([D(0)] * 12, 6) == [D(0)] * 12  # zero stays zero
    assert project([None] * 12, 6) == [None] * 12  # nothing booked: blank
    assert project([D(1)] * 12, 0) == [None] * 12
    assert project([D(1)] * 12, 12) == [D(1)] * 12


def test_stocks_carry_the_last_month() -> None:
    months: list[Decimal | None] = [D("2.00")] * 5 + [D("3.00")] + [None] * 6
    assert project(months, 6, "last") == [D("2.00")] * 5 + [D("3.00")] * 7
    # A blank last month: nobody left.
    assert project([D(1), None] + [None] * 10, 2, "last") == [D(1)] + [None] * 11
    with pytest.raises(ValueError):
        project([None] * 12, 13)


def setup_of(client: TestClient, code: str) -> dict:  # type: ignore[type-arg]
    return client.get(f"/api/rounds/{code}/setup").json()["setup"]  # type: ignore[no-any-return]


def save(client: TestClient, code: str, **changes: object) -> Response:
    return client.put(f"/api/rounds/{code}/setup", json={**setup_of(client, code), **changes})


def test_a_budget_projects_the_prior_year(client: TestClient) -> None:
    projection = {"kind": "projection", "through": "2025-03-01"}
    assert save(client, "B2026", comparisons=[projection]).status_code == 200
    assert setup_of(client, "B2026")["comparisons"] == [
        {**projection, "version_code": None, "note": None}
    ]
    g = client.get(f"{URL}/grid").json()
    [column] = g["comparisons"]
    assert (column["kind"], column["label"]) == ("projection", "Projection 2025")
    assert (column["actuals_until"], column["partial"]) == ("2025-03-01", False)
    r = rows(g)
    assert r["6000"]["comparisons"][0] == ["100.00"] * 12
    assert r["6010"]["comparisons"][0] == ["50.00", None, None] + ["16.67"] * 6 + ["16.66"] * 3
    assert r["7000"]["comparisons"][0] == [None, "-10.00", "30.00"] + ["6.67"] * 6 + ["6.66"] * 3
    # Without a month, it counts through the latest actuals: the whole of 2025 here.
    assert save(client, "B2026", comparisons=[{"kind": "projection"}]).status_code == 200
    g = client.get(f"{URL}/grid").json()
    assert g["comparisons"][0]["actuals_until"] == "2025-12-01"
    assert rows(g)["6010"]["comparisons"][0] == ["50.00"] + [None] * 11


def test_the_month_is_one_of_the_prior_year(client: TestClient) -> None:
    for wrong in (
        {"kind": "projection", "through": "2026-03-01"},  # the round's own year
        {"kind": "projection", "through": "2025-03-15"},  # not a month
        {"kind": "actuals", "through": "2025-03-01"},  # not a projection
    ):
        assert save(client, "B2026", comparisons=[wrong]).status_code == 422, wrong


def test_a_forecast_projects_its_own_year(client: TestClient) -> None:
    body = {
        "code": "F2025",
        "name": "Forecast 2025",
        "kind": "forecast",
        "fiscal_year": 2025,
        "actuals_through": "2025-03-01",
    }
    assert client.post("/api/rounds", json=body).status_code == 200
    assert save(client, "F2025", comparisons=[{"kind": "projection"}]).status_code == 200
    g = client.get("/api/versions/F2025/cost-centers/CC1/grid").json()
    assert g["comparisons"][0]["label"] == "Projection 2025"
    assert g["comparisons"][0]["actuals_until"] == "2025-03-01"
    assert (
        rows(g)["6010"]["comparisons"][0] == ["50.00", None, None] + ["16.67"] * 6 + ["16.66"] * 3
    )


def test_a_round_set_up_like_another_moves_the_month(client: TestClient) -> None:
    projection = {"kind": "projection", "through": "2025-09-01"}
    assert save(client, "B2026", comparisons=[projection]).status_code == 200
    body = {"code": "B2027", "name": "Budget 2027", "kind": "budget", "fiscal_year": 2027}
    made = client.post("/api/rounds", json={**body, "copy_from": "B2026"})
    assert made.status_code == 200, made.text
    [column, _] = setup_of(client, "B2027")["comparisons"]
    assert column["through"] == "2026-09-01"


def test_workforce_carries_the_last_month(client: TestClient) -> None:
    setup = {"workforce_mode": "both", "comparisons": [{"kind": "projection"}]}
    assert save(client, "B2026", **setup).status_code == 200
    sheet = client.get("/api/versions/B2026/workforce/CC1").json()
    assert sheet["comparisons"][0]["label"] == "Projection 2025"
    tech = [
        r
        for r in sheet["rows"]
        if r["kind"] == "line"
        and r["measure"] == "headcount"
        and (r["staff_type_code"], r["staff_category_code"]) == ("EMP", "TECH")
    ]
    assert tech[0]["comparisons"][0] == ["2.00"] * 12
