"""Running Alembic migrations of the app store."""

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Connection, Engine, inspect

from planlayer.config import Settings
from planlayer.store.db import create_app_engine


class SchemaMissingError(RuntimeError):
    """The app schema doesn't exist. Planlayer doesn't create it: the DBA does (D-25)."""


def _config(settings: Settings, engine: Engine) -> Config:
    config = Config()
    config.set_main_option("script_location", "planlayer.store:migrations")
    config.attributes["settings"] = settings
    config.attributes["engine"] = engine
    return config


def _require_schema(engine: Engine, schema: str) -> None:
    with engine.connect() as conn:
        if schema not in inspect(conn).get_schema_names():
            raise SchemaMissingError(
                f"Schema {schema!r} does not exist. Create it with the install script "
                "(deploy/sql/install.sql) before running migrations."
            )


def upgrade(settings: Settings, revision: str = "head") -> None:
    engine = create_app_engine(settings)
    try:
        _require_schema(engine, settings.app_schema)
        command.upgrade(_config(settings, engine), revision)
    finally:
        engine.dispose()


def downgrade(settings: Settings, revision: str) -> None:
    engine = create_app_engine(settings)
    try:
        command.downgrade(_config(settings, engine), revision)
    finally:
        engine.dispose()


def current_revision(conn: Connection, schema: str) -> str | None:
    """The migration revision the app store is at, or None before the first migration."""
    context = MigrationContext.configure(conn, opts={"version_table_schema": schema})
    return context.get_current_revision()
