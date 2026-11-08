"""The end-to-end slice: sync from a source, then read and edit the planning grid via the API.

The source is ``conftest.SOURCE``.
"""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, text

from planlayer import sync
from planlayer.config import Settings
from planlayer.store.db import create_app_engine
from planlayer.store.schema import change_log, closure, member

from .conftest import BLANK, TEST_DATABASE_URL, URL, plan, rows, run_sync, versions_of

# --- Sync -----------------------------------------------------------------------------------------


def test_sync_writes_members_hierarchies_and_actuals(
    migrated: Settings, source_schema: str
) -> None:
    first = run_sync(migrated, source_schema)
    assert first.actual_rows == 16
    again = run_sync(migrated, source_schema)
    assert (again.members, again.actual_rows) == (first.members, first.actual_rows)
    engine = create_app_engine(migrated)
    with engine.connect() as conn:
        # NW, DEP, CC1, CC2 / PL, A, B, 6000, 6010, 7000 / DE
        assert conn.execute(select(func.count()).select_from(member)).scalar() == 11
        # Leaf CC1 has ancestors CC1, DEP, NW; leaf 6000 has 6000, A, PL.
        n = conn.execute(select(func.count()).select_from(closure)).scalar()
        assert n == (1 + 2 + 3 + 3) + (1 + 2 + 2 + 3 + 3 + 3)
    engine.dispose()


def test_sync_refuses_unknown_codes_and_writes_nothing(
    migrated: Settings, source_schema: str
) -> None:
    admin = create_engine(TEST_DATABASE_URL or "")
    with admin.begin() as conn:
        conn.execute(text(f"INSERT INTO {source_schema}.v_gl_actuals_natural "
                          "VALUES ('2025-01-01', 'DE', 'CC9', '9999', 1)"))  # fmt: skip
    admin.dispose()
    with pytest.raises(sync.SyncError) as error:
        run_sync(migrated, source_schema)
    assert set(error.value.problems) == {
        "actuals: unknown cost center CC9",
        "actuals: unknown account 9999",
    }
    engine = create_app_engine(migrated)
    with engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(member)).scalar() == 0
    engine.dispose()


# --- Grid -----------------------------------------------------------------------------------------


def test_grid_rows_prior_year_and_blank_plan(client: TestClient) -> None:
    grid = client.get(f"{URL}/grid").json()
    assert [r["code"] for r in grid["rows"]] == ["A", "6000", "6010", "B", "7000"]
    assert grid["periods"][0] == "2026-01-01"
    assert grid["comparisons"] == [{"kind": "prior_year_actuals", "label": "Actual 2025"}]
    r = rows(grid)
    assert r["6000"]["comparisons"] == [["100.00"] * 12]
    assert r["6010"]["comparisons"] == [["50.00"] + [None] * 11]
    assert r["A"]["comparisons"][0][:2] == ["150.00", "100.00"]
    assert r["6000"]["entry_form"] == "monthly" and r["6000"]["months_editable"]
    assert plan(grid, "6000") == BLANK


def test_cost_center_switcher(client: TestClient) -> None:
    units = client.get("/api/versions/B2026/cost-centers").json()
    assert [(u["code"], u["status"]) for u in units] == [
        ("CC1", "not_started"),
        ("CC2", "not_started"),
    ]


def test_month_edit_zero_and_blank(client: TestClient) -> None:
    edit = {"account_code": "6000", "month": 0, "value": "123.45", "expected": BLANK}
    grid = client.post(f"{URL}/edits", json={"edits": [edit]}).json()
    assert plan(grid, "6000")[0] == "123.45"
    assert rows(grid)["A"]["plan"][0]["value"] == "123.45"
    v = versions_of(grid, "6000")
    zero = {"account_code": "6000", "month": 0, "value": "0", "expected": v}
    grid = client.post(f"{URL}/edits", json={"edits": [zero]}).json()
    assert plan(grid, "6000")[0] == "0.00"
    blank = {
        "account_code": "6000",
        "month": 0,
        "value": None,
        "expected": versions_of(grid, "6000"),
    }
    grid = client.post(f"{URL}/edits", json={"edits": [blank]}).json()
    assert plan(grid, "6000")[0] is None
    assert rows(grid)["A"]["plan"][0]["value"] is None


def test_stale_edit_is_a_conflict_and_changes_nothing(client: TestClient) -> None:
    first = {"account_code": "6000", "month": 3, "value": "10", "expected": BLANK}
    client.post(f"{URL}/edits", json={"edits": [first]})
    # A second planner still sees the cell as blank.
    stale = {"account_code": "6000", "month": 3, "value": "20", "expected": BLANK}
    other = {"account_code": "6010", "month": 3, "value": "5", "expected": BLANK}
    response = client.post(f"{URL}/edits", json={"edits": [other, stale]})
    assert response.status_code == 409
    assert response.json() == [{"account_code": "6000", "month": 3}]
    grid = client.get(f"{URL}/grid").json()
    assert plan(grid, "6000")[3] == "10.00"
    assert plan(grid, "6010")[3] is None  # all or nothing


def test_year_total_spreads_by_prior_year(client: TestClient) -> None:
    edits = [
        {"account_code": "6000", "month": None, "value": "1200.00", "expected": BLANK},
        # Only January last year: everything goes to January.
        {"account_code": "6010", "month": None, "value": "10", "expected": BLANK},
        # The negative February counts as 0: everything goes to March.
        {"account_code": "7000", "month": None, "value": "100.01", "expected": BLANK},
    ]
    grid = client.post(f"{URL}/edits", json={"edits": edits}).json()
    assert plan(grid, "6000") == ["100.00"] * 12
    assert plan(grid, "6010") == ["10.00"] + ["0.00"] * 11
    assert plan(grid, "7000") == ["0.00", "0.00", "100.01"] + ["0.00"] * 9
    assert sum(Decimal(v or 0) for v in plan(grid, "6000")) == Decimal("1200.00")


def test_year_total_with_uneven_reference_ties_exactly(client: TestClient) -> None:
    edit = {"account_code": "6000", "month": None, "value": "1000.00", "expected": BLANK}
    grid = client.post(f"{URL}/edits", json={"edits": [edit]}).json()
    values = [Decimal(v) for v in plan(grid, "6000") if v is not None]
    assert sum(values) == Decimal("1000.00")
    assert values[:4] == [Decimal("83.34")] * 4 and values[4] == Decimal("83.33")


def test_invalid_values_are_refused(client: TestClient) -> None:
    for value in ("1.005", "abc", "NaN"):
        edit = {"account_code": "6000", "month": 0, "value": value, "expected": BLANK}
        assert client.post(f"{URL}/edits", json={"edits": [edit]}).status_code == 422
    group = {"account_code": "A", "month": 0, "value": "1", "expected": BLANK}
    assert client.post(f"{URL}/edits", json={"edits": [group]}).status_code == 422
    assert client.get("/api/versions/B2026/cost-centers/NOPE/grid").status_code == 404


def test_changes_are_audited(client: TestClient, migrated: Settings) -> None:
    edit = {"account_code": "6000", "month": 0, "value": "5", "expected": BLANK}
    grid = client.post(f"{URL}/edits", json={"edits": [edit]}).json()
    update = {
        "account_code": "6000",
        "month": 0,
        "value": "7",
        "expected": versions_of(grid, "6000"),
    }
    client.post(f"{URL}/edits", json={"edits": [update]})
    engine = create_app_engine(migrated)
    with engine.connect() as conn:
        c = change_log.c
        query = select(c.actor, c.op, c.old_amount, c.new_amount).order_by(c.id)
        log = conn.execute(query).all()
    engine.dispose()
    # Synced actuals are not in the change log; plan edits are.
    assert [tuple(r) for r in log] == [
        ("dev", "INSERT", None, Decimal("5.00")),
        ("dev", "UPDATE", Decimal("5.00"), Decimal("7.00")),
    ]
