"""Test fixtures.

Database tests need ``PLANLAYER_TEST_DATABASE_URL``, a Postgres 18 connection allowed to create
schemas (CI sets it). Each test session works in its own pair of random schemas, so tests never
touch existing data. Without the variable, database tests are skipped.
"""

import os
import secrets
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from planlayer import rounds, sync
from planlayer.adapters.source import PostgresSource
from planlayer.api.app import create_app
from planlayer.config import Settings
from planlayer.store import migrate
from planlayer.store.db import create_app_engine

TEST_DATABASE_URL = os.environ.get("PLANLAYER_TEST_DATABASE_URL")


@pytest.fixture
def db_settings() -> Iterator[Settings]:
    """Settings pointing at a fresh, empty app schema and output schema."""
    if not TEST_DATABASE_URL:
        pytest.skip("PLANLAYER_TEST_DATABASE_URL is not set")
    suffix = secrets.token_hex(4)
    app_schema, output_schema = f"test_app_{suffix}", f"test_out_{suffix}"
    admin = create_engine(TEST_DATABASE_URL)
    with admin.begin() as conn:
        conn.execute(text(f"CREATE SCHEMA {app_schema}"))
        conn.execute(text(f"CREATE SCHEMA {output_schema}"))
    try:
        yield Settings(
            _env_file=None,  # type: ignore[call-arg]
            environment="development",
            app_database_url=TEST_DATABASE_URL,  # type: ignore[arg-type]
            app_schema=app_schema,
            output_schema=output_schema,
        )
    finally:
        with admin.begin() as conn:
            conn.execute(text(f"DROP SCHEMA {app_schema} CASCADE"))
            conn.execute(text(f"DROP SCHEMA {output_schema} CASCADE"))
        admin.dispose()


@pytest.fixture
def migrated(db_settings: Settings) -> Settings:
    """Settings whose app schema is migrated to head."""
    migrate.upgrade(db_settings)
    return db_settings


# --- The end-to-end slice -------------------------------------------------------------------------

# A small hand-made source shaped like the contract (D-11):
# - cost centers: NW > DEP > CC1, CC2 (entity DE)
# - accounts: PL > A (6000, 6010), B (7000)
# - 2025 actuals for CC1: 6000 = 100.00 every month; 6010 = 50.00 in January only;
#   7000 = -10.00 in February (a correction) and 30.00 in March. CC2: 6000 = 999.00 in January.
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
    rounds.create_round(engine, "test", "B2026", "Budget 2026", "budget", 2026)
    rounds.set_round_status(engine, "test", "B2026", "open")
    engine.dispose()
    with TestClient(create_app(migrated)) as c:
        yield c


def rows(grid: dict) -> dict[str, dict]:  # type: ignore[type-arg]
    return {r["code"]: r for r in grid["rows"]}


def plan(grid: dict, code: str) -> list[str | None]:  # type: ignore[type-arg]
    return [c["value"] for c in rows(grid)[code]["plan"]]


def versions_of(grid: dict, code: str) -> list[int | None]:  # type: ignore[type-arg]
    return [c["row_version"] for c in rows(grid)[code]["plan"]]
