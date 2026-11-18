"""Capacity planning in prepared rounds, forecasts and quarters (ADR 0016).

The fixture source gives CC1 a lathe (WC1, machine hours at 80, crediting account 4900) that ran
100 hours a month in 2025, 20 in August. Budget 2025 is prepared in setup here with CC1
productive; Forecast 2025 is a 6+6 set up like it.
"""

import io
from decimal import Decimal

import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import create_engine, func, select, text

from planlayer.config import Settings
from planlayer.store.output import create_output_engine, fact_plan_capacity

from .conftest import BLANK, TEST_DATABASE_URL, plan, run_sync

B, F = "B2025", "F2025"


def sheet_url(code: str) -> str:
    return f"/api/versions/{code}/capacity/CC1"


def sheet(client: TestClient, code: str) -> dict:  # type: ignore[type-arg]
    response = client.get(sheet_url(code))
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


def lathe(s: dict) -> dict:  # type: ignore[type-arg]
    return next(r for r in s["rows"] if r["kind"] == "line" and r["work_center_code"] == "WC1")


def hours_of(s: dict) -> list[str | None]:  # type: ignore[type-arg]
    return [c["value"] for c in lathe(s)["plan"]]


def credit(s: dict) -> list[str | None]:  # type: ignore[type-arg]
    row = next(r for r in s["rows"] if r["kind"] == "credit" and r["activity_type_code"] == "MH")
    return [c["value"] for c in row["plan"]]


def edit(
    client: TestClient, code: str, value: str | None, *, month: int | None = None,
    quarter: int | None = None, expected: list[int | None] = BLANK,
) -> Response:  # fmt: skip
    body = {
        "work_center_code": "WC1",
        "activity_type_code": "MH",
        "month": month,
        "quarter": quarter,
        "value": value,
        "expected": expected,
    }
    return client.post(f"{sheet_url(code)}/edits", json={"edits": [body]})


def save(client: TestClient, code: str, **changes: object) -> Response:
    setup = client.get(f"/api/rounds/{code}/setup").json()["setup"]
    return client.put(f"/api/rounds/{code}/setup", json={**setup, **changes})


@pytest.fixture
def budget(client: TestClient) -> TestClient:
    """Budget 2025 in setup, CC1 productive, the lathe planned at 100 hours a month."""
    body = {"code": B, "name": "Budget 2025", "kind": "budget", "fiscal_year": 2025}
    assert client.post("/api/rounds", json=body).status_code == 200
    assert save(client, B, capacity_work_centers=["CC1"]).status_code == 200
    return client


def test_the_controller_prepares_capacity_in_setup(budget: TestClient) -> None:
    # No 2024 actual hours to follow: the year spreads evenly.
    assert edit(budget, B, "1200").status_code == 200
    s = sheet(budget, B)
    assert hours_of(s) == ["100.00"] * 12
    assert credit(s) == ["8000.00"] * 12
    assert s["status"] == "not_started"  # preparation leaves the workflow alone


@pytest.fixture
def forecast(budget: TestClient) -> TestClient:
    assert edit(budget, B, "1200").status_code == 200
    body = {
        "code": F,
        "name": "Forecast 2025",
        "kind": "forecast",
        "fiscal_year": 2025,
        "copy_from": B,
        "carry": {"values": True},
        "actuals_through": "2025-06-01",
    }
    made = budget.post("/api/rounds", json=body)
    assert made.status_code == 200, made.text
    return budget


def test_hours_carry_with_plan_values_into_open_months(forecast: TestClient) -> None:
    s = sheet(forecast, F)
    assert (s["version_kind"], s["closed_months"]) == ("forecast", 6)
    # January to June are the lathe's actual hours; July to December carried from the budget.
    assert hours_of(s) == ["100.00"] * 12
    assert [c["row_version"] for c in lathe(s)["plan"]][:6] == [None] * 6
    # Credits are recomputed for the open months; the closed months show the cost center's
    # actuals on 4900, which has none.
    assert credit(s) == [None] * 6 + ["8000.00"] * 6
    created = forecast.get(f"/api/rounds/{F}/setup").json()["created"]
    assert created["hours"] == 6
    # The round's comparison columns, in hours: Budget 2025's plan.
    assert [c["label"] for c in s["comparisons"]] == ["Actual 2024", "Budget 2025"]
    assert lathe(s)["comparisons"][1] == ["100.00"] * 12


def test_forecast_hours(forecast: TestClient) -> None:
    assert edit(forecast, F, "5", month=2).status_code == 422  # closed
    expected = [c["row_version"] for c in lathe(sheet(forecast, F))["plan"]]
    # The year total is the full year: 1,100 less 600 actual hours to date.
    assert edit(forecast, F, "1100", expected=expected).status_code == 200
    s = sheet(forecast, F)
    rest = [Decimal(v) for v in hours_of(s)[6:] if v is not None]
    assert sum(rest) == Decimal("500.00") and len(rest) == 6
    expected = [c["row_version"] for c in lathe(s)["plan"]]
    refused = edit(forecast, F, "500", expected=expected)
    assert refused.status_code == 422 and "below the actual hours" in refused.text


def test_quarter_hours(forecast: TestClient) -> None:
    assert save(forecast, F, grain="quarter").status_code == 200
    expected = [c["row_version"] for c in lathe(sheet(forecast, F))["plan"]]
    assert edit(forecast, F, "1", month=7, expected=expected).status_code == 422
    assert edit(forecast, F, "1", quarter=1, expected=expected).status_code == 422  # closed
    assert edit(forecast, F, "330", quarter=2, expected=expected).status_code == 200
    assert hours_of(sheet(forecast, F))[6:9] == ["110.00"] * 3


def test_publish_planned_hours(forecast: TestClient, migrated: Settings) -> None:
    p = forecast.get(f"/api/rounds/{F}/publish/preview", params={"scope": "all"}).json()
    assert p["hours_rows"] == 12  # six planned months, six closed months of actual hours
    body = {"scope": "all", "fingerprint": p["fingerprint"]}
    assert forecast.post(f"/api/rounds/{F}/publish", json=body).status_code == 200
    output = create_output_engine(migrated)
    try:
        t = fact_plan_capacity
        with output.connect() as conn:
            counts = dict(
                conn.execute(
                    select(t.c.origin, func.sum(t.c.amount))
                    .where(t.c.version_code == F)
                    .group_by(t.c.origin)
                ).all()
            )
    finally:
        output.dispose()
    assert counts == {"plan": Decimal("600.00"), "actual": Decimal("600.00")}
    # The download holds the same rows, with the table's columns but the run's.
    text = forecast.get(f"/api/rounds/{F}/export.csv", params={"scope": "all", "table": "hours"})
    assert text.headers["content-disposition"].endswith(f'{F}-hours-all.csv"')
    lines = text.text.strip().split("\n")
    assert lines[0].split(",") == [
        c.name for c in t.columns if c.name not in ("published_at", "publish_run_id")
    ]
    assert len(lines) == 13
    parquet = forecast.get(f"/api/rounds/{F}/export.parquet?scope=all&table=hours").content
    hours = pq.read_table(io.BytesIO(parquet)).column("amount").to_pylist()
    assert sum(hours) == Decimal("1200.00")


def test_closed_months_show_credits_the_ledger_books(
    forecast: TestClient, migrated: Settings, source_schema: str
) -> None:
    """Credit accounts are ordinary accounts to the sync: credits the ledger books on them are
    synced as actuals and show in a forecast's closed months (ADR 0016), on both sheets."""
    assert TEST_DATABASE_URL
    admin = create_engine(TEST_DATABASE_URL)
    with admin.begin() as conn:
        conn.execute(
            text(
                f"INSERT INTO {source_schema}.v_gl_actuals_natural "
                "VALUES ('2025-03-01', 'DE', 'CC1', '4900', 8000.00)"
            )
        )
    admin.dispose()
    run_sync(migrated, source_schema)
    assert credit(sheet(forecast, F))[2] == "8000.00"
    grid = forecast.get(f"/api/versions/{F}/cost-centers/CC1/grid").json()
    assert plan(grid, "4900")[2] == "-8000.00"
