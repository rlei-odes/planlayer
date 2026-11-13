"""Development plan values (TD-19): owners' year totals near last year's actuals."""

from fastapi.testclient import TestClient
from sqlalchemy import select

from planlayer import devdata
from planlayer.config import Settings
from planlayer.store.db import create_app_engine
from planlayer.store.schema import change_log, workforce_position_log

from .conftest import URL, plan, rows


def test_seed_plans_once_per_cost_center(migrated: Settings, client: TestClient) -> None:
    engine = create_app_engine(migrated)
    try:
        # Only CC1 has an owner with a user (anna); CC2 has none.
        assert devdata.seed_plans(engine, "B2026") == [("CC1", "anna", "in_progress")]
        assert devdata.seed_plans(engine, "B2026") == []  # it has values now
        with engine.connect() as conn:
            actors = set(conn.execute(select(change_log.c.actor)).scalars())
    finally:
        engine.dispose()
    assert actors == {"anna"}
    grid = client.get(f"{URL}/grid").json()
    # Last year 1,200 on 6000: this year 6% below to 9% above, in hundreds.
    total = sum(float(v) for v in plan(grid, "6000") if v)
    assert 1100 <= total <= 1300 and total % 100 == 0
    assert rows(grid)["6000"]["plan"][0]["value"] is not None


def test_seed_workforce(migrated: Settings, client: TestClient) -> None:
    """TD-25: HR's people as positions, and a replacement for the announced leaver (CC1's planner
    leaves at the end of June 2026), entered by the owner."""
    engine = create_app_engine(migrated)
    try:
        assert devdata.seed_workforce(engine, "B2026") == (5, 1, 0)
        assert devdata.seed_workforce(engine, "B2026") == (0, 0, 0)  # nothing new
        with engine.connect() as conn:
            actors = set(conn.execute(select(workforce_position_log.c.actor)).scalars())
    finally:
        engine.dispose()
    # Without the test company's controller the system user takes HR's people; the owner
    # enters the replacement.
    assert actors == {"system", "anna"}
    sheet = client.get("/api/versions/B2026/workforce/CC1").json()
    replacement = next(p for p in sheet["positions"] if p["is_replacement"])
    assert (replacement["entry_date"], replacement["title"]) == ("2026-07-01", "Planner")
    assert sheet["build_up"]["replacements"][6] == 1
