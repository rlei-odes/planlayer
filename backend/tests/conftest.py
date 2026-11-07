"""Test fixtures.

Database tests need ``PLANLAYER_TEST_DATABASE_URL``, a Postgres 18 connection allowed to create
schemas (CI sets it). Each test session works in its own pair of random schemas, so tests never
touch existing data. Without the variable, database tests are skipped.
"""

import os
import secrets
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text

from planlayer.config import Settings
from planlayer.store import migrate

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
