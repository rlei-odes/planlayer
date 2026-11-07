"""Alembic environment. Started only through ``planlayer.store.migrate``, which passes the
settings and engine in ``config.attributes``."""

from alembic import context

from planlayer.store.schema import metadata

config = context.config
settings = config.attributes["settings"]
engine = config.attributes["engine"]

with engine.connect() as connection:
    context.configure(
        connection=connection,
        target_metadata=metadata,
        version_table_schema=settings.app_schema,
    )
    # Postgres DDL is transactional: an upgrade applies completely or not at all.
    with context.begin_transaction():
        context.run_migrations()
