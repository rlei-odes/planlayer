"""The end-to-end slice: sync from a source, then read and edit the planning grid via the API.

The source is a small hand-made schema shaped like the contract (D-11):
- cost centers: NW > DEP > CC1, CC2 (entity DE)
- accounts: PL > A (6000, 6010), B (7000)
- 2025 actuals for CC1: 6000 = 100.00 every month; 6010 = 50.00 in January only;
  7000 = -10.00 in February (a correction) and 30.00 in March. CC2: 6000 = 999.00 in January.
"""

import secrets
from collections.abc import Iterator
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, text

from planlayer import sync
from planlayer.adapters.source import PostgresSource
from planlayer.api.app import create_app
from planlayer.config import Settings
from planlayer.store.db import create_app_engine
from planlayer.store.schema import change_log, closure, member

from .conftest import TEST_DATABASE_URL

SOURCE = """
CREATE TABLE {s}.v_entities AS SELECT 'DE'::text AS entity_code, 'Nordwerk DE'::text AS entity_name;
CREATE TABLE {s}.v_cost_centers (cost_center_code text, cost_center_name text, entity_code text,
    owner text, valid_from date, valid_to date);
INSERT INTO {s}.v_cost_centers VALUES
    ('CC1', 'Machining', 'DE', 'anna@x.example', '2010-01-01', '9999-12-31'),
    ('CC2', 'Assembly', 'DE', NULL, '2010-01-01', '9999-12-31');
CREATE TABLE {s}.v_cost_center_hierarchy (member_code text, member_name text, parent_code text,
    is_group bool, sort_order int);
INSERT INTO {s}.v_cost_center_hierarchy VALUES
    ('NW', 'Group', NULL, true, 0), ('DEP', 'Production', 'NW', true, 1),
    ('CC1', 'Machining', 'DEP', false, 1), ('CC2', 'Assembly', 'DEP', false, 2);
CREATE TABLE {s}.v_accounts (account_code text, account_name text, account_type text);
INSERT INTO {s}.v_accounts VALUES
    ('6000', 'Wages', 'cost'), ('6010', 'Bonus', 'cost'), ('7000', 'Rent', 'cost');
CREATE TABLE {s}.v_account_hierarchy (member_code text, member_name text, parent_code text,
    is_group bool, sort_order int);
INSERT INTO {s}.v_account_hierarchy VALUES
    ('PL', 'P&L', NULL, true, 0), ('A', 'Personnel', 'PL', true, 1),
    ('B', 'Facilities', 'PL', true, 2), ('6000', 'Wages', 'A', false, 1),
    ('6010', 'Bonus', 'A', false, 2), ('7000', 'Rent', 'B', false, 1);
CREATE TABLE {s}.v_gl_actuals_natural (period date, entity_code text, cost_center_code text,
    account_code text, amount numeric);
INSERT INTO {s}.v_gl_actuals_natural
    SELECT make_date(2025, m, 1), 'DE', 'CC1', '6000', 100.00 FROM generate_series(1, 12) m;
INSERT INTO {s}.v_gl_actuals_natural VALUES
    ('2025-01-01', 'DE', 'CC1', '6010', 50.00),
    ('2025-02-01', 'DE', 'CC1', '7000', -10.00), ('2025-03-01', 'DE', 'CC1', '7000', 30.00),
    ('2025-01-01', 'DE', 'CC2', '6000', 999.00);
"""

BLANK = [None] * 12
URL = "/api/versions/B2026/cost-centers/CC1"


@pytest.fixture
def source_schema(migrated: Settings) -> Iterator[str]:
    assert TEST_DATABASE_URL
    schema = f"test_src_{secrets.token_hex(4)}"
    admin = create_engine(TEST_DATABASE_URL)
    with admin.begin() as conn:
        conn.execute(text(f"CREATE SCHEMA {schema}"))
        conn.execute(text(SOURCE.format(s=schema)))
    try:
        yield schema
    finally:
        with admin.begin() as conn:
            conn.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        admin.dispose()


def run_sync(settings: Settings, schema: str) -> sync.SyncResult:
    engine, source = create_app_engine(settings), PostgresSource(settings.app_url)
    try:
        return sync.run(engine, source, sync.default_mapping(schema), actor="test")
    finally:
        engine.dispose()
        source.dispose()


@pytest.fixture
def client(migrated: Settings, source_schema: str) -> Iterator[TestClient]:
    run_sync(migrated, source_schema)
    engine = create_app_engine(migrated)
    sync.create_version(engine, "B2026", "Budget 2026", "budget", 2026, "test")
    engine.dispose()
    with TestClient(create_app(migrated)) as c:
        yield c


def rows(grid: dict) -> dict[str, dict]:  # type: ignore[type-arg]
    return {r["code"]: r for r in grid["rows"]}


def plan(grid: dict, code: str) -> list[str | None]:  # type: ignore[type-arg]
    return [c["value"] for c in rows(grid)[code]["plan"]]


def versions_of(grid: dict, code: str) -> list[int | None]:  # type: ignore[type-arg]
    return [c["row_version"] for c in rows(grid)[code]["plan"]]


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
    assert grid["periods"][0] == "2026-01-01" and grid["prior_periods"][0] == "2025-01-01"
    r = rows(grid)
    assert r["6000"]["prior_year"] == ["100.00"] * 12
    assert r["6010"]["prior_year"] == ["50.00"] + [None] * 11
    assert r["A"]["prior_year"][0] == "150.00" and r["A"]["prior_year"][1] == "100.00"
    assert plan(grid, "6000") == BLANK


def test_cost_center_switcher(client: TestClient) -> None:
    units = client.get("/api/versions/B2026/cost-centers").json()
    assert [u["code"] for u in units] == ["CC1", "CC2"]


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
