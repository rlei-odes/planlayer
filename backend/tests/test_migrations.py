import pytest
from sqlalchemy import inspect, select

from planlayer.config import Settings
from planlayer.store import migrate
from planlayer.store.db import create_app_engine
from planlayer.store.schema import installation


def test_upgrade_creates_installation_row_in_configured_schema(migrated: Settings) -> None:
    engine = create_app_engine(migrated)
    with engine.connect() as conn:
        assert "installation" in inspect(conn).get_table_names(schema=migrated.app_schema)
        row = conn.execute(select(installation)).one()
        assert row.fiscal_year_start_month == 1
        assert migrate.current_revision(conn, migrated.app_schema) == "0001"
    engine.dispose()


def test_downgrade_and_upgrade_round_trip(migrated: Settings) -> None:
    migrate.downgrade(migrated, "base")
    migrate.upgrade(migrated)
    engine = create_app_engine(migrated)
    with engine.connect() as conn:
        assert migrate.current_revision(conn, migrated.app_schema) == "0001"
    engine.dispose()


def test_missing_schema_is_reported(db_settings: Settings) -> None:
    settings = db_settings.model_copy(update={"app_schema": "does_not_exist"})
    with pytest.raises(migrate.SchemaMissingError, match="install script"):
        migrate.upgrade(settings)
