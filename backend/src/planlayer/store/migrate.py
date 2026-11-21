"""Running Alembic migrations of the app store."""

from collections.abc import Iterator
from contextlib import contextmanager

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, Engine, inspect, text

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


def behind(settings: Settings) -> str | None:
    """The revision of an app store that migrations would upgrade, or None when there is
    nothing to keep (a new, empty app store) or nothing to do (at head)."""
    engine = create_app_engine(settings)
    try:
        _require_schema(engine, settings.app_schema)
        with engine.connect() as conn:
            revision = current_revision(conn, settings.app_schema)
    finally:
        engine.dispose()
    return None if revision in (None, head_revision()) else revision


def downgrade(settings: Settings, revision: str) -> None:
    engine = create_app_engine(settings)
    try:
        command.downgrade(_config(settings, engine), revision)
    finally:
        engine.dispose()


def head_revision() -> str:
    """The newest migration revision shipped with this version of Planlayer."""
    config = Config()
    config.set_main_option("script_location", "planlayer.store:migrations")
    head = ScriptDirectory.from_config(config).get_current_head()
    assert head is not None
    return head


def migration_schema() -> str:
    """The configured app schema, quoted, for raw SQL inside a migration (functions, triggers).

    Only callable while a migration runs; table DDL uses the symbolic schema instead.
    """
    from alembic import context

    settings: Settings = context.config.attributes["settings"]
    return f'"{settings.app_schema}"'


def current_revision(conn: Connection, schema: str) -> str | None:
    """The migration revision the app store is at, or None before the first migration."""
    context = MigrationContext.configure(conn, opts={"version_table_schema": schema})
    return context.get_current_revision()


@contextmanager
def every_row(conn: Connection, schema: str, tables: tuple[str, ...]) -> Iterator[None]:
    """For a migration that reads or rewrites rows: every row of ``tables``, their triggers off.
    The app role owns them, but forced row-level security would show it none, and rewriting
    must not trip a lock or write the audit log. Restored at the end; ``schema`` quoted, as
    ``migration_schema`` gives it."""
    forced = {
        t: conn.execute(
            text(f"SELECT relforcerowsecurity FROM pg_class WHERE oid = '{schema}.{t}'::regclass")
        ).scalar_one()
        for t in tables
    }
    for t in tables:
        conn.execute(text(f"ALTER TABLE {schema}.{t} DISABLE TRIGGER USER"))
        if forced[t]:
            conn.execute(text(f"ALTER TABLE {schema}.{t} NO FORCE ROW LEVEL SECURITY"))
    try:
        yield
    finally:
        for t in tables:
            conn.execute(text(f"ALTER TABLE {schema}.{t} ENABLE TRIGGER USER"))
            if forced[t]:
                conn.execute(text(f"ALTER TABLE {schema}.{t} FORCE ROW LEVEL SECURITY"))
