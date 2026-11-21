import pytest
from sqlalchemy import inspect, select, text

from planlayer.config import Settings
from planlayer.store import migrate
from planlayer.store.db import create_app_engine
from planlayer.store.schema import installation

from .conftest import run_sync


def test_upgrade_creates_installation_row_in_configured_schema(migrated: Settings) -> None:
    engine = create_app_engine(migrated)
    with engine.connect() as conn:
        assert "installation" in inspect(conn).get_table_names(schema=migrated.app_schema)
        row = conn.execute(select(installation)).one()
        assert row.fiscal_year_start_month == 1
        assert migrate.current_revision(conn, migrated.app_schema) == migrate.head_revision()
    engine.dispose()


def test_downgrade_and_upgrade_round_trip(migrated: Settings) -> None:
    migrate.downgrade(migrated, "base")
    migrate.upgrade(migrated)
    engine = create_app_engine(migrated)
    with engine.connect() as conn:
        assert migrate.current_revision(conn, migrated.app_schema) == migrate.head_revision()
    engine.dispose()


def test_missing_schema_is_reported(db_settings: Settings) -> None:
    settings = db_settings.model_copy(update={"app_schema": "does_not_exist"})
    with pytest.raises(migrate.SchemaMissingError, match="install script"):
        migrate.upgrade(settings)


def test_every_row_lifts_forced_row_level_security_for_a_migration(
    migrated: Settings, source_schema: str
) -> None:
    run_sync(migrated, source_schema)
    engine = create_app_engine(migrated)
    s = f'"{migrated.app_schema}"'
    count = f"SELECT count(*) FROM {s}.hr_staff"
    with engine.begin() as conn:  # the app role, no user: row-level security shows nothing
        assert conn.execute(text(count)).scalar_one() == 0
        with migrate.every_row(conn, s, ("hr_staff",)):
            assert conn.execute(text(count)).scalar_one() > 0
        assert conn.execute(text(count)).scalar_one() == 0
    engine.dispose()
