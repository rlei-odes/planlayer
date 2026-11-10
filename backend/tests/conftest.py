"""Test fixtures.

Database tests need ``PLANLAYER_TEST_DATABASE_URL``, a Postgres 18 connection allowed to create
schemas and roles (CI sets it). Each test works in its own pair of random schemas, so tests never
touch existing data. Without the variable, database tests are skipped.

Planlayer itself connects as the unprivileged role ``planlayer_test``, which owns the test
schemas, as the app role does in an installation: a superuser would bypass row-level security.
"""

import os
import secrets
from collections.abc import Iterator
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, make_url, text

from planlayer import access, rounds, sync
from planlayer.adapters.source import PostgresSource
from planlayer.api.app import create_app
from planlayer.config import Settings
from planlayer.store import migrate
from planlayer.store.db import create_app_engine

TEST_DATABASE_URL = os.environ.get("PLANLAYER_TEST_DATABASE_URL")
APP_ROLE = "planlayer_test"


def _app_url() -> str:
    assert TEST_DATABASE_URL
    url = make_url(TEST_DATABASE_URL).set(username=APP_ROLE, password=APP_ROLE)
    return url.render_as_string(hide_password=False)


@pytest.fixture
def db_settings() -> Iterator[Settings]:
    """Settings pointing at a fresh, empty app schema and output schema."""
    if not TEST_DATABASE_URL:
        pytest.skip("PLANLAYER_TEST_DATABASE_URL is not set")
    suffix = secrets.token_hex(4)
    app_schema, output_schema = f"test_app_{suffix}", f"test_out_{suffix}"
    admin = create_engine(TEST_DATABASE_URL)
    with admin.begin() as conn:
        conn.execute(
            text(f"""
            DO $$ BEGIN
                IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                    CREATE ROLE {APP_ROLE} LOGIN PASSWORD '{APP_ROLE}' NOSUPERUSER;
                END IF;
            END $$
        """)
        )
        conn.execute(text(f"CREATE SCHEMA {app_schema} AUTHORIZATION {APP_ROLE}"))
        conn.execute(text(f"CREATE SCHEMA {output_schema} AUTHORIZATION {APP_ROLE}"))
    try:
        yield Settings(
            _env_file=None,  # type: ignore[call-arg]
            environment="development",
            app_database_url=_app_url(),  # type: ignore[arg-type]
            app_schema=app_schema,
            output_schema=output_schema,
            dev_login=True,
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
    ('6000', 'Wages', 'cost'), ('6010', 'Bonus', 'cost'), ('7000', 'Rent', 'cost'),
    ('4900', 'Machine hour credits', 'cost');
CREATE TABLE {s}.v_account_hierarchy (member_code text, member_name text, parent_code text,
    is_group bool, sort_order int);
INSERT INTO {s}.v_account_hierarchy VALUES
    ('PL', 'P&L', NULL, true, 0), ('A', 'Personnel', 'PL', true, 1),
    ('B', 'Facilities', 'PL', true, 2), ('6000', 'Wages', 'A', false, 1),
    ('6010', 'Bonus', 'A', false, 2), ('7000', 'Rent', 'B', false, 1),
    ('C', 'Credits', 'PL', true, 3), ('4900', 'Machine hour credits', 'C', false, 1);
CREATE TABLE {s}.v_gl_actuals_natural (period date, entity_code text, cost_center_code text,
    account_code text, amount numeric);
INSERT INTO {s}.v_gl_actuals_natural
    SELECT make_date(2025, m, 1), 'DE', 'CC1', '6000', 100.00 FROM generate_series(1, 12) m;
INSERT INTO {s}.v_gl_actuals_natural VALUES
    ('2025-01-01', 'DE', 'CC1', '6010', 50.00),
    ('2025-02-01', 'DE', 'CC1', '7000', -10.00), ('2025-03-01', 'DE', 'CC1', '7000', 30.00),
    ('2025-01-01', 'DE', 'CC2', '6000', 999.00);
-- Capacity planning (ADR 0013): CC1 has a lathe and a mill, CC2 a press.
CREATE TABLE {s}.v_activity_types (activity_type_code text, activity_type_name text,
    account_code text, variable_account_code text);
INSERT INTO {s}.v_activity_types VALUES
    ('MH', 'Machine hours', '4900', NULL), ('RW', 'Repair work', NULL, NULL);
CREATE TABLE {s}.v_work_centers (work_center_code text, work_center_name text,
    cost_center_code text);
INSERT INTO {s}.v_work_centers VALUES
    ('WC1', 'Lathe', 'CC1'), ('WC2', 'Mill', 'CC1'), ('WC9', 'Press', 'CC2');
CREATE TABLE {s}.v_work_center_rates (work_center_code text, activity_type_code text,
    rate numeric);
INSERT INTO {s}.v_work_center_rates VALUES
    ('WC1', 'MH', 80), ('WC2', 'MH', 100.5), ('WC2', 'RW', 60), ('WC9', 'MH', 50);
CREATE TABLE {s}.v_capacity_actuals (period date, work_center_code text,
    activity_type_code text, hours numeric);
-- The lathe ran 100 hours a month in 2025, 20 in the August shutdown.
INSERT INTO {s}.v_capacity_actuals
    SELECT make_date(2025, m, 1), 'WC1', 'MH', CASE WHEN m = 8 THEN 20 ELSE 100 END
    FROM generate_series(1, 12) m;
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
        conn.execute(text(f"GRANT USAGE ON SCHEMA {schema} TO {APP_ROLE}"))
        conn.execute(text(f"GRANT SELECT ON ALL TABLES IN SCHEMA {schema} TO {APP_ROLE}"))
    try:
        yield schema
    finally:
        with admin.begin() as conn:
            conn.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        admin.dispose()


def run_sync(settings: Settings, schema: str) -> sync.SyncResult:
    engine, source = create_app_engine(settings), PostgresSource(settings.app_url)
    try:
        actor = access.system_user(engine)
        for m in sync.default_mappings(schema):
            sync.save_mapping(engine, actor, m)
        return sync.run(engine, source, actor)
    finally:
        engine.dispose()
        source.dispose()


# Users of the slice: CC1's owner is matched by e-mail; CC2 has no owner.
CONTROLLER = "ctrl@x.example"
OWNER = "anna"  # login; owner of CC1 through the e-mail below
OWNER_EMAIL = "anna@x.example"
APPROVER = "appr@x.example"  # approver at DEP
VIEWER = "view@x.example"
NOBODY = "bob@x.example"  # no rights


@pytest.fixture
def client(migrated: Settings, source_schema: str) -> Iterator[TestClient]:
    """The slice synced, round B2026 open, users created; logged in as the controller."""
    run_sync(migrated, source_schema)
    engine = create_app_engine(migrated)
    system = access.system_user(engine)
    admin = replace(system, is_admin=True)
    access.create_user(engine, admin, CONTROLLER, "Ctrl", is_controller=True)
    access.create_user(engine, admin, OWNER, "Anna", OWNER_EMAIL)
    access.create_user(engine, admin, APPROVER, "Appr")
    access.create_user(engine, admin, VIEWER, "View", is_viewer=True)
    access.create_user(engine, admin, NOBODY, "Bob")
    access.add_approver(engine, system, "DEP", APPROVER)
    rounds.create_round(engine, system, "B2026", "Budget 2026", "budget", 2026)
    rounds.set_round_status(engine, system, "B2026", "open")
    engine.dispose()
    with TestClient(create_app(migrated)) as c:
        login(c, CONTROLLER)
        yield c


def login(client: TestClient, user: str) -> None:
    response = client.post("/api/auth/dev-login", json={"login": user})
    assert response.status_code == 200, response.text


def rows(grid: dict) -> dict[str, dict]:  # type: ignore[type-arg]
    return {r["code"]: r for r in grid["rows"]}


def plan(grid: dict, code: str) -> list[str | None]:  # type: ignore[type-arg]
    return [c["value"] for c in rows(grid)[code]["plan"]]


def versions_of(grid: dict, code: str) -> list[int | None]:  # type: ignore[type-arg]
    return [c["row_version"] for c in rows(grid)[code]["plan"]]
