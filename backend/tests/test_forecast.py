"""Forecast rounds and quarter grain (ADR 0015).

The ``client`` fixture syncs ``conftest.SOURCE``: 2025 actuals for CC1 (6000 = 100.00 every month,
6010 = 50.00 in January, 7000 = -10.00 in February and 30.00 in March) and CC2 (6000 = 999.00 in
January); B2026 is open and the controller is logged in. Budget 2025 is prepared in setup here,
and Forecast 2025 set up like it.
"""

from collections.abc import Iterator
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import Engine, func, insert, select
from sqlalchemy.exc import DBAPIError

from planlayer import access
from planlayer.config import Settings
from planlayer.store.db import create_app_engine
from planlayer.store.output import create_output_engine, fact_plan_cost_center
from planlayer.store.schema import fact_cost_center, member, version
from planlayer.store.uow import unit_of_work

from .conftest import BLANK, URL, plan, rows, versions_of

B, F = "B2025", "F2025"


def grid_url(code: str, cc: str = "CC1") -> str:
    return f"/api/versions/{code}/cost-centers/{cc}"


def grid(client: TestClient, code: str = F, cc: str = "CC1") -> dict:  # type: ignore[type-arg]
    response = client.get(f"{grid_url(code, cc)}/grid")
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


def edits(client: TestClient, code: str, *edit: dict) -> Response:  # type: ignore[type-arg]
    return client.post(f"{grid_url(code)}/edits", json={"edits": list(edit)})


def year(account: str, value: str | None) -> dict:  # type: ignore[type-arg]
    return {"account_code": account, "month": None, "value": value, "expected": BLANK}


def setup_of(client: TestClient, code: str) -> dict:  # type: ignore[type-arg]
    return client.get(f"/api/rounds/{code}/setup").json()["setup"]  # type: ignore[no-any-return]


def save(client: TestClient, code: str, **changes: object) -> Response:
    return client.put(f"/api/rounds/{code}/setup", json={**setup_of(client, code), **changes})


def create(client: TestClient, **fields: object) -> Response:
    body = {"code": F, "name": "Forecast 2025", "kind": "forecast", "fiscal_year": 2025}
    return client.post("/api/rounds", json={**body, **fields})


@pytest.fixture
def budget(client: TestClient) -> TestClient:
    """Budget 2025 in setup with CC1 6000 = 200.00 a month (no 2024 actuals: spread evenly)."""
    body = {"code": B, "name": "Budget 2025", "kind": "budget", "fiscal_year": 2025}
    assert client.post("/api/rounds", json=body).status_code == 200
    assert edits(client, B, year("6000", "2400")).status_code == 200
    return client


@pytest.fixture
def forecast(budget: TestClient) -> TestClient:
    """Forecast 2025 as a 6+6, set up like Budget 2025 with its plan values."""
    made = create(budget, copy_from=B, carry={"values": True}, actuals_through="2025-06-01")
    assert made.status_code == 200, made.text
    return budget


@pytest.fixture
def engine(migrated: Settings, client: TestClient) -> Iterator[Engine]:
    e = create_app_engine(migrated)
    yield e
    e.dispose()


def test_closed_months_show_actuals_and_open_months_the_carried_plan(forecast: TestClient) -> None:
    g = grid(forecast)
    assert (g["version_kind"], g["grain"], g["closed_months"]) == ("forecast", "month", 6)
    assert g["actuals_through"] == "2025-06-01"
    assert plan(g, "6000") == ["100.00"] * 6 + ["200.00"] * 6
    assert versions_of(g, "6000")[:6] == [None] * 6  # actuals: no row version
    # Accounts with actuals in the closed months show, with them.
    assert plan(g, "7000")[:3] == [None, "-10.00", "30.00"]
    assert forecast.get(f"/api/rounds/{F}/setup").json()["created"]["cells"] == 6
    # The year's actuals stop at the closed month: the year to date.
    comparisons = [
        {"kind": "prior_year_actuals"},
        {"kind": "actuals"},
        {"kind": "version", "version_code": B},
    ]
    assert save(forecast, F, comparisons=comparisons).status_code == 200
    g = grid(forecast)
    assert [c["label"] for c in g["comparisons"]] == ["Actual 2024", "YTD Jun 2025", "Budget 2025"]
    assert rows(g)["6000"]["comparisons"][1] == ["100.00"] * 6 + [None] * 6
    assert g["comparisons"][1]["actuals_until"] == "2025-06-01"


def test_closed_months_take_no_plan_values(forecast: TestClient, engine: Engine) -> None:
    month = {"account_code": "6000", "month": 2, "value": "1", "expected": BLANK}
    refused = edits(forecast, F, month)
    assert refused.status_code == 422 and "closed" in refused.text
    with (
        pytest.raises(DBAPIError, match="the month is closed in this forecast"),
        unit_of_work(engine, access.system_user(engine)) as conn,
    ):
        ids = dict(conn.execute(select(member.c.code, member.c.member_id)).all())
        vid = conn.execute(select(version.c.version_id).where(version.c.code == F)).scalar()
        conn.execute(
            insert(fact_cost_center).values(
                version_id=vid,
                layer_id=1,
                measure_id=1,
                period=date(2025, 3, 1),
                dim_entity=ids["DE"],
                dim_cost_center=ids["CC1"],
                dim_account=ids["6000"],
                amount=Decimal(1),
            )
        )


def test_a_year_total_is_the_full_year(forecast: TestClient) -> None:
    g = grid(forecast)
    expected = versions_of(g, "6000")
    typed = {**year("6000", "1800"), "expected": expected}
    assert edits(forecast, F, typed).status_code == 200
    # 600.00 of actuals to date leave 1,200.00 for the six open months.
    assert plan(grid(forecast), "6000") == ["100.00"] * 6 + ["200.00"] * 6
    typed = {**year("6000", "1500"), "expected": versions_of(grid(forecast), "6000")}
    assert edits(forecast, F, typed).status_code == 200
    assert plan(grid(forecast), "6000") == ["100.00"] * 6 + ["150.00"] * 6
    # Clearing the year clears the open months; the closed months still show actuals.
    cleared = {**year("6000", None), "expected": versions_of(grid(forecast), "6000")}
    assert edits(forecast, F, cleared).status_code == 200
    assert plan(grid(forecast), "6000") == ["100.00"] * 6 + [None] * 6


def test_closed_months_follow_the_grain_and_the_kind(budget: TestClient) -> None:
    for wrong in (
        {"kind": "budget", "code": "B2025X", "actuals_through": "2025-06-01"},  # not a forecast
        {"actuals_through": "2025-12-01"},  # nothing left to forecast
        {"actuals_through": "2024-12-01"},  # not in the year
        {"actuals_through": "2025-06-15"},  # not a month
        {"grain": "quarter", "actuals_through": "2025-05-01"},  # not a quarter end
    ):
        made = create(budget, **wrong)
        assert made.status_code == 422, wrong
    made = create(budget, grain="quarter", actuals_through="2025-06-01")
    assert made.status_code == 200
    comparisons = [{"kind": "actuals"}]
    assert save(budget, F, comparisons=comparisons).status_code == 200
    assert [c["label"] for c in grid(budget)["comparisons"]] == ["YTD Q2 2025"]
    # Planned by quarter, it can't close a single month more.
    moved = save(budget, F, actuals_through="2025-07-01")
    assert moved.status_code == 422


def test_moving_actuals_through(forecast: TestClient) -> None:
    # In setup, months closed now lose their carried plan values in the same save.
    assert save(forecast, F, actuals_through="2025-09-01").status_code == 200
    assert plan(grid(forecast), "6000") == ["100.00"] * 9 + ["200.00"] * 3
    # Opening months again: they start blank.
    assert save(forecast, F, actuals_through="2025-06-01").status_code == 200
    assert plan(grid(forecast), "6000") == ["100.00"] * 6 + [None] * 3 + ["200.00"] * 3
    # In an open round, closing months that hold plan values is refused.
    status = forecast.post(f"/api/versions/{F}/status", json={"status": "open"})
    assert status.status_code == 200
    refused = save(forecast, F, actuals_through="2025-10-01")
    assert refused.status_code == 422 and "CC1" in refused.text
    assert save(forecast, F, actuals_through="2025-08-01").status_code == 200


def test_a_forecast_compares_as_its_full_year(forecast: TestClient) -> None:
    comparisons = [{"kind": "version", "version_code": F}]
    assert save(forecast, "B2026", comparisons=comparisons).status_code == 200
    g = forecast.get(f"{URL}/grid").json()
    assert rows(g)["6000"]["comparisons"][0] == ["100.00"] * 6 + ["200.00"] * 6
    assert rows(g)["6010"]["comparisons"][0][0] == "50.00"  # an actual, no plan value


def test_quarter_grain(client: TestClient) -> None:
    def quarter(account: str, q: int, value: str | None, g: dict) -> dict:  # type: ignore[type-arg]
        return {
            "account_code": account,
            "quarter": q,
            "month": None,
            "value": value,
            "expected": versions_of(g, account) if account in rows(g) else BLANK,
        }

    month = {"account_code": "7000", "month": 0, "value": "1", "expected": BLANK}
    q1 = quarter("7000", 0, "90", client.get(f"{URL}/grid").json())
    assert edits(client, "B2026", q1).status_code == 422
    assert save(client, "B2026", grain="quarter").status_code == 200
    assert client.get(f"{URL}/grid").json()["grain"] == "quarter"
    assert edits(client, "B2026", month).status_code == 422
    # Q1 spreads like 2025's actuals in January to March: February's -10 counts as 0.
    assert edits(client, "B2026", q1).status_code == 200
    g = client.get(f"{URL}/grid").json()
    assert plan(g, "7000")[:3] == ["0.00", "0.00", "90.00"]
    # Q2 has no actuals to follow: evenly over its months.
    assert edits(client, "B2026", quarter("7000", 1, "90", g)).status_code == 200
    g = client.get(f"{URL}/grid").json()
    assert plan(g, "7000")[:6] == ["0.00", "0.00", "90.00", "30.00", "30.00", "30.00"]
    # Back to months: the values stay as stored.
    assert save(client, "B2026", grain="month").status_code == 200
    assert plan(client.get(f"{URL}/grid").json(), "7000")[3] == "30.00"


def test_latest_actuals(client: TestClient) -> None:
    latest = client.get("/api/actuals/latest").json()
    assert latest == {"month": "2025-12-01", "fiscal_year": 2025, "start_month": 1}
    v = client.get("/api/rounds/B2026/setup").json()
    assert v["latest_actuals"] == "2025-12-01"


def test_publish_a_forecast_with_its_actuals(forecast: TestClient, migrated: Settings) -> None:
    p = forecast.get(f"/api/rounds/{F}/publish/preview", params={"scope": "all"}).json()
    # CC1's six forecast months, and the closed months' actuals of both cost centers: CC1 6000
    # January to June, 6010 January, 7000 February and March; CC2 6000 January.
    assert p["rows"] == 6 + 10
    body = {"scope": "all", "fingerprint": p["fingerprint"]}
    ran = forecast.post(f"/api/rounds/{F}/publish", json=body)
    assert ran.status_code == 200, ran.text
    output = create_output_engine(migrated)
    try:
        t = fact_plan_cost_center
        with output.connect() as conn:
            counts = dict(
                conn.execute(
                    select(t.c.origin, func.count())
                    .where(t.c.version_code == F)
                    .group_by(t.c.origin)
                ).all()
            )
            total = conn.execute(
                select(func.sum(t.c.amount)).where(
                    t.c.version_code == F, t.c.cost_center_code == "CC1", t.c.account_code == "6000"
                )
            ).scalar()
    finally:
        output.dispose()
    assert counts == {"plan": 6, "actual": 10}
    assert total == Decimal("1800.00")  # the full year: 600 actual + 1,200 forecast
    csv = forecast.get(f"/api/rounds/{F}/export.csv", params={"scope": "all"})
    assert csv.status_code == 200 and csv.text.splitlines()[0].endswith(",status,origin")
