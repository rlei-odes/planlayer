"""Dataset mappings, sync preview and confirmation, manual members and overrides (ADR 0012).

The ``client`` fixture syncs ``conftest.SOURCE`` through the default mappings; the controller
is logged in.
"""

from collections.abc import Iterator
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, Integer, create_engine, func, select, text

from planlayer import access, masterdata, rounds, sync
from planlayer.adapters.source import PostgresSource
from planlayer.api.app import create_app
from planlayer.config import Settings
from planlayer.store.db import create_app_engine
from planlayer.store.schema import fact_cost_center, member, member_shadow, sync_run, version
from planlayer.store.uow import unit_of_work

from .conftest import APP_ROLE, BLANK, OWNER, TEST_DATABASE_URL, login, plan, rows

ADMIN = "root@x.example"


@pytest.fixture
def engine(migrated: Settings, client: TestClient) -> Iterator[Engine]:
    e = create_app_engine(migrated)
    admin = replace(access.system_user(e), is_admin=True)
    access.create_user(e, admin, ADMIN, "Root", is_admin=True)
    yield e
    e.dispose()


@pytest.fixture
def source(migrated: Settings) -> Iterator[PostgresSource]:
    s = PostgresSource(migrated.app_url)
    yield s
    s.dispose()


def members(client: TestClient, dataset: str) -> dict[str, dict]:  # type: ignore[type-arg]
    return {m["code"]: m for m in client.get(f"/api/members/{dataset}").json()}


def system(engine: Engine) -> access.User:
    return access.system_user(engine)


# --- Mappings and preview -------------------------------------------------------------------------


def test_only_admins_map_datasets(engine: Engine, client: TestClient) -> None:
    assert client.get("/api/datasets").status_code == 403  # the controller
    login(client, ADMIN)
    ds = {d["dataset"]: d for d in client.get("/api/datasets").json()}
    assert ds["cost_centers"]["has_hierarchy"] and not ds["actuals"]["has_hierarchy"]
    assert ds["cost_centers"]["mapping"]["hierarchy"] == {"parent": "parent_code"}
    assert [f["name"] for f in ds["entities"]["fields"] if f["required"]] == ["code", "name"]


def test_preview_raw_mapped_and_errors(
    engine: Engine, client: TestClient, source_schema: str
) -> None:
    login(client, ADMIN)
    query = f"SELECT account_code, account_name, 'cost' AS t FROM {source_schema}.v_accounts"
    raw = client.post("/api/datasets/accounts/preview", json={"query": query, "fields": {}}).json()
    assert raw["columns"] == ["account_code", "account_name", "t"] and len(raw["rows"]) == 4
    body = {
        "query": query,
        "fields": {"code": "account_code", "name": "account_name", "account_type": "t"},
        "hierarchy": {"levels": [{"code": "t", "name": "t"}]},
        "mapped": True,
    }
    mapped = client.post("/api/datasets/accounts/preview", json=body).json()
    assert mapped["columns"][:3] == ["code", "name", "account_type"] and mapped["error"] is None
    bad = client.post("/api/datasets/accounts/preview", json={**body, "query": "SELECT nope"})
    assert "nope" in bad.json()["error"]
    missing = client.post("/api/datasets/accounts/preview", json={**body, "fields": {"code": "x"}})
    assert missing.json()["error"] == "name a column for: name"
    # A query can't write: a function that would (with the rights to) fails read-only.
    admin_db = create_engine(TEST_DATABASE_URL or "")
    with admin_db.begin() as conn:
        conn.execute(
            text(f"""
            CREATE FUNCTION {source_schema}.sneaky() RETURNS int LANGUAGE sql SECURITY DEFINER
            AS $$ INSERT INTO {source_schema}.v_entities VALUES ('Z', 'Z') RETURNING 1 $$
        """)
        )
    admin_db.dispose()
    write = {"query": f"SELECT {source_schema}.sneaky()", "fields": {}}
    error = client.post("/api/datasets/entities/preview", json=write).json()["error"]
    assert "read-only transaction" in error


# --- Hierarchy forms ------------------------------------------------------------------------------


def test_level_columns_build_a_ragged_tree(
    engine: Engine, client: TestClient, source_schema: str, source: PostgresSource
) -> None:
    admin_db = create_engine(TEST_DATABASE_URL or "")
    with admin_db.begin() as conn:
        conn.execute(
            text(f"""
            CREATE TABLE {source_schema}.flat_cc AS SELECT * FROM (VALUES
                ('CC1', 'Machining', 'NW', 'Group', 'DEP', 'Production'),
                ('CC2', 'Assembly', 'NW', 'Group', 'DEP', 'Production'),
                ('CC3', 'Board', 'NW', 'Group', NULL, NULL)
            ) t(code, name, l1, l1_name, l2, l2_name);
            GRANT SELECT ON {source_schema}.flat_cc TO {APP_ROLE};
        """)
        )
    admin_db.dispose()
    sync.save_mapping(
        engine,
        system(engine),
        sync.Mapping(
            "cost_centers",
            f"SELECT * FROM {source_schema}.flat_cc",
            {"code": "code", "name": "name"},
            {"levels": [{"code": "l1", "name": "l1_name"}, {"code": "l2", "name": "l2_name"}]},
        ),
    )
    p = sync.plan(engine, source)
    assert p.problems == [] and p.new == ["cost center CC3 Board"]
    sync.run(engine, source, system(engine))
    m = members(client, "cost_centers")
    assert (m["CC3"]["parent"], m["CC3"]["depth"]) == ("NW", 1)  # level 2 empty: ragged
    assert (m["CC1"]["parent"], m["DEP"]["is_group"]) == ("DEP", True)
    assert m["CC3"]["entity_code"] == "-"  # no entity named


def test_plan_reports_problems_and_writes_nothing(
    engine: Engine, source_schema: str, source: PostgresSource
) -> None:
    sync.save_mapping(
        engine,
        system(engine),
        sync.Mapping(
            "entities", f"SELECT 'X' AS c FROM {source_schema}.nope", {"code": "c", "name": "c"}
        ),
    )
    p = sync.plan(engine, source)
    assert any(pr.startswith("entities: reading failed") for pr in p.problems)
    with pytest.raises(sync.SyncError):
        sync.run(engine, source, system(engine))


# --- Manual members and matches -------------------------------------------------------------------


def test_build_from_scratch_and_plan(migrated: Settings) -> None:
    """The Simple level: no source at all, master data entered by hand."""
    e = create_app_engine(migrated)
    sys_ = system(e)
    for dataset, code, name, parent, group in (
        ("cost_centers", "ALL", "Company", None, True),
        ("cost_centers", "K100", "Workshop", "ALL", False),
        ("accounts", "COSTS", "Costs", None, True),
        ("accounts", "4000", "Material", "COSTS", False),
    ):
        masterdata.add_member(e, sys_, dataset, code, name, parent=parent, is_group=group)
    rounds.create_round(e, sys_, "B2027", "Budget 2027", "budget", 2027)
    rounds.set_round_status(e, sys_, "B2027", "open")
    admin = replace(sys_, is_admin=True)
    access.create_user(e, admin, "ctl", "Ctl", is_controller=True)
    e.dispose()
    with TestClient(create_app(migrated)) as c:
        login(c, "ctl")
        url = "/api/versions/B2027/cost-centers/K100"
        assert c.post(f"{url}/accounts", json={"account_code": "4000"}).status_code == 200
        edit = {"account_code": "4000", "month": None, "value": "1200", "expected": BLANK}
        grid = c.post(f"{url}/edits", json={"edits": [edit]}).json()
        assert plan(grid, "4000") == ["100.00"] * 12  # no reference: spread evenly
        assert rows(grid)["4000"]["fallback"] is True


def test_manual_member_taken_over_after_confirmation(
    engine: Engine, client: TestClient, source_schema: str, source: PostgresSource
) -> None:
    masterdata.add_member(
        engine, system(engine), "accounts", "7100", "Leasing (manual)", parent="B"
    )
    with engine.connect() as conn:
        before = conn.execute(select(member.c.member_id).where(member.c.code == "7100")).scalar()
    admin_db = create_engine(TEST_DATABASE_URL or "")
    with admin_db.begin() as conn:
        conn.execute(
            text(f"""
            INSERT INTO {source_schema}.v_accounts VALUES ('7100', 'Leasing', 'cost');
            INSERT INTO {source_schema}.v_account_hierarchy
                VALUES ('7100', 'Leasing', 'B', false, 2);
        """)
        )
    admin_db.dispose()
    plan_ = client.post("/api/sync/plan", json={}).json()
    assert plan_["matches"] == [
        {"key": "account:7100", "dimension": "account", "code": "7100", "name": "Leasing"}
    ]
    refused = client.post("/api/sync/run", json={})
    assert refused.status_code == 422
    assert refused.json()["detail"]["problems"] == [
        "confirm the match with the source: account:7100"
    ]
    ok = client.post("/api/sync/run", json={"confirm": ["account:7100"]}).json()
    assert ok["matched"] == ["account:7100"]
    m = members(client, "accounts")["7100"]
    assert (m["origin"], m["name"]) == ("source", "Leasing")
    with engine.connect() as conn:
        assert (
            conn.execute(select(member.c.member_id).where(member.c.code == "7100")).scalar()
            == before
        )
        shadow = conn.execute(select(member_shadow)).one()
    assert (shadow.name, shadow.parents, shadow.confirmed_by) == (
        "Leasing (manual)",
        {"account": "B"},
        "ctrl@x.example",
    )
    assert client.post("/api/sync/plan", json={}).json()["matches"] == []


def test_overrides_survive_sync_and_reset(
    engine: Engine, client: TestClient, source: PostgresSource
) -> None:
    client.patch("/api/members/cost_centers/CC2", json={"name": "Assembly (local)"})
    masterdata.add_member(
        engine, system(engine), "cost_centers", "DEP2", "Logistics", parent="NW", is_group=True
    )
    client.patch("/api/members/cost_centers/CC2", json={"parent": "DEP2"})
    sync.run(engine, source, system(engine))
    m = members(client, "cost_centers")
    assert (m["CC2"]["name"], m["CC2"]["source_name"], m["CC2"]["renamed"]) == (
        "Assembly (local)",
        "Assembly",
        True,
    )
    assert (m["CC2"]["parent"], m["CC2"]["source_parent"], m["CC2"]["moved"]) == (
        "DEP2",
        "DEP",
        True,
    )
    status = {r["code"]: r for r in client.get("/api/versions/B2026/overview").json()["rows"]}
    assert status["CC2"]["depth"] == 2 and "DEP2" in status  # the roll-up follows the move
    client.delete("/api/members/cost_centers/CC2/override")
    m = members(client, "cost_centers")
    assert (m["CC2"]["name"], m["CC2"]["parent"]) == ("Assembly", "DEP")


def test_member_rules(engine: Engine, client: TestClient) -> None:
    assert client.patch("/api/members/cost_centers/DEP", json={"parent": "CC1"}).status_code == 422
    assert client.patch("/api/members/cost_centers/NW", json={"parent": "DEP"}).status_code == 422
    assert client.patch("/api/members/cost_centers/CC1", json={"owner": "x"}).status_code == 422
    assert client.delete("/api/members/cost_centers/CC1").status_code == 422  # synced
    new = {"code": "CC9", "name": "New", "parent": "DEP", "owner": "anna@x.example"}
    assert client.post("/api/members/cost_centers", json=new).status_code == 200
    assert client.post("/api/members/cost_centers", json=new).status_code == 422  # exists
    assert client.delete("/api/members/cost_centers/CC9").status_code == 200
    login(client, OWNER)
    assert client.get("/api/members/cost_centers").status_code == 403
    assert client.post("/api/sync/plan", json={}).status_code == 403


# --- Windows of fiscal years, admins syncing, members no longer delivered ------------------------


def _source_sql(sql: str) -> None:
    admin_db = create_engine(TEST_DATABASE_URL or "")
    with admin_db.begin() as conn:
        conn.execute(text(sql))
    admin_db.dispose()


def _actual_years(engine: Engine) -> dict[int, int]:
    """Stored actual cells per year, read as the system user (row-level security)."""
    f = fact_cost_center
    year = func.extract("year", f.c.period).cast(Integer)
    with unit_of_work(engine, system(engine)) as conn:
        return dict(
            conn.execute(
                select(year, func.count())
                .join(version, version.c.version_id == f.c.version_id)
                .where(version.c.code == "ACT")
                .group_by(year)
            ).all()
        )


def test_actuals_sync_replaces_only_the_window(
    engine: Engine, client: TestClient, source_schema: str, source: PostgresSource
) -> None:
    # Any day of a month counts as the month, so daily rows add up; the window's last day is in.
    _source_sql(
        f"INSERT INTO {source_schema}.v_gl_actuals_natural VALUES "
        "('2024-03-01', 'DE', 'CC1', '6000', 77.00), ('2024-03-17', 'DE', 'CC1', '6000', 23.00),"
        "('2024-12-31', 'DE', 'CC1', '6010', 5.00)"
    )
    sync.run(engine, source, system(engine), years=(2024, 2025))
    assert _actual_years(engine) == {2024: 2, 2025: 16}
    # The admin narrows the query to one year, as a warehouse query often does.
    with engine.connect() as conn:
        m = sync.mappings(conn)["actuals"]
    narrowed = replace(m, query=f"{m.query} WHERE extract(year FROM posting_month) = 2025")
    sync.save_mapping(engine, system(engine), narrowed)
    # The default window is what round B2026 compares with: 2025 and 2026. 2024 stays.
    p = sync.plan(engine, source)
    assert p.window is not None and (p.window.first, p.window.last) == (2025, 2026)
    assert [(y.fiscal_year, y.stored, y.delivered) for y in p.years] == [(2025, 16, 16)]
    sync.run(engine, source, system(engine))
    assert _actual_years(engine) == {2024: 2, 2025: 16}
    # A window reaching 2024 shows that the narrowed query would empty it.
    wide = sync.plan(engine, source, years=(2024, 2025))
    assert [(y.fiscal_year, y.stored, y.delivered) for y in wide.years] == [
        (2024, 2, 0),
        (2025, 16, 16),
    ]
    # The window's filter is added to the query's own, on the column mapped to period.
    own = replace(m, query=f"{m.query} WHERE gl_account <> '6010'")
    sync.save_mapping(engine, system(engine), own)
    by_year = sync.plan(engine, source, years=(2024, 2025)).years
    assert [(y.fiscal_year, y.delivered) for y in by_year] == [(2024, 1), (2025, 15)]
    with pytest.raises(sync.SyncError):
        sync.plan(engine, source, years=(2026, 2025))


def test_an_admin_runs_the_sync(engine: Engine, client: TestClient) -> None:
    """The admin reads no actuals (ADR 0009), but syncs them: the system user writes."""
    login(client, ADMIN)
    p = client.post("/api/sync/plan", json={"years": {"first": 2025, "last": 2025}}).json()
    assert p["years"] == {"first": 2025, "last": 2025}
    assert p["actual_years"] == [{"fiscal_year": 2025, "stored": 16, "delivered": 16}]
    run = client.post("/api/sync/run", json={"years": {"first": 2025, "last": 2025}})
    assert run.status_code == 200 and run.json()["actual_rows"] == 16
    with engine.connect() as conn:
        actor = conn.execute(
            select(sync_run.c.actor).order_by(sync_run.c.id.desc()).limit(1)
        ).scalar()
    assert actor == ADMIN
    assert _actual_years(engine) == {2025: 16}


def test_a_member_no_longer_delivered_keeps_its_place(
    engine: Engine, client: TestClient, source_schema: str, source: PostgresSource
) -> None:
    _source_sql(
        f"DELETE FROM {source_schema}.v_cost_center_hierarchy WHERE member_code = 'CC2';"
        f"DELETE FROM {source_schema}.v_cost_centers WHERE cost_center_code = 'CC2';"
        f"DELETE FROM {source_schema}.v_gl_actuals_natural WHERE cost_center = 'CC2';"
    )
    p = sync.plan(engine, source)
    assert p.gone == ["cost center CC2 Assembly"]
    sync.run(engine, source, system(engine))
    m = members(client, "cost_centers")["CC2"]
    assert (m["parent"], m["origin"]) == ("DEP", "source")
    assert m["missing_since"] is not None  # the Members page says so
    assert members(client, "cost_centers")["CC1"]["missing_since"] is None
    status = {r["code"]: r for r in client.get("/api/versions/B2026/overview").json()["rows"]}
    assert status["CC2"]["depth"] == 2  # still under DEP


def test_the_preview_says_what_changed(
    engine: Engine, client: TestClient, source_schema: str, source: PostgresSource
) -> None:
    _source_sql(
        f"UPDATE {source_schema}.v_cost_centers SET valid_to = '2026-12-31' "
        "WHERE cost_center_code = 'CC2'"
    )
    p = sync.plan(engine, source)
    assert p.changed == ["cost center CC2 Assembly: valid to 9999-12-31 → 2026-12-31"]
