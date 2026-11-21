"""Backup and restore of the app store (ADR 0032), with the Postgres client tools.

Skipped without ``pg_dump`` and ``pg_restore`` of the server's major version or newer (CI
installs them; the Planlayer image has them).
"""

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine, make_url, text

from planlayer import backup
from planlayer.config import Settings
from planlayer.store import migrate
from planlayer.store.db import create_app_engine

from .conftest import APP_ROLE, TEST_DATABASE_URL, run_sync

BACKUP_ROLE = "planlayer_test_backup"
# Rows the app role can't all read under its own row-level security, pay among them.
TABLES = ("hr_pay", "hr_staff", "fact_cost_center", "fact_workforce", "app_user", "change_log")


def _client_major() -> int | None:
    if shutil.which("pg_dump") is None or shutil.which("pg_restore") is None:
        return None
    out = subprocess.run(["pg_dump", "--version"], capture_output=True, text=True, check=True)
    return int(out.stdout.split()[-1].split(".")[0])


@dataclass(frozen=True)
class Installation:
    settings: Settings
    tmp: Path


@pytest.fixture
def installation(migrated: Settings, source_schema: str, tmp_path: Path) -> Installation:
    """A synced app store, with a backup role as the install script creates it."""
    if (major := _client_major()) is None or major < backup.PG_MAJOR:
        pytest.skip(f"pg_dump and pg_restore {backup.PG_MAJOR} are not installed")
    assert TEST_DATABASE_URL
    run_sync(migrated, source_schema)
    s = migrated.app_schema
    admin = create_engine(TEST_DATABASE_URL)
    with admin.begin() as conn:
        conn.execute(
            text(f"""
            DO $$ BEGIN
                IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{BACKUP_ROLE}') THEN
                    CREATE ROLE {BACKUP_ROLE} LOGIN BYPASSRLS PASSWORD '{BACKUP_ROLE}';
                END IF;
            EXCEPTION WHEN duplicate_object OR unique_violation THEN NULL;
            END $$
        """)
        )
        conn.execute(text(f"GRANT USAGE ON SCHEMA {s} TO {BACKUP_ROLE}"))
        conn.execute(text(f"GRANT SELECT ON ALL TABLES IN SCHEMA {s} TO {BACKUP_ROLE}"))
        conn.execute(text(f"GRANT SELECT ON ALL SEQUENCES IN SCHEMA {s} TO {BACKUP_ROLE}"))
    admin.dispose()
    url = make_url(TEST_DATABASE_URL).set(username=BACKUP_ROLE, password=BACKUP_ROLE)
    settings = migrated.model_copy(
        update={
            "backup_database_url": SecretStr(url.render_as_string(hide_password=False)),
            "backup_dir": tmp_path / "backups",
        }
    )
    return Installation(settings, tmp_path)


def _counts(schema: str) -> dict[str, int]:
    """Row counts as the superuser, past row-level security."""
    assert TEST_DATABASE_URL
    admin = create_engine(TEST_DATABASE_URL)
    with admin.connect() as conn:
        counts = {
            t: conn.execute(text(f"SELECT count(*) FROM {schema}.{t}")).scalar_one() for t in TABLES
        }
    admin.dispose()
    return counts


def _empty_schema(schema: str) -> None:
    """The DBA's part of a restore in place: the schema dropped and made again, empty."""
    assert TEST_DATABASE_URL
    admin = create_engine(TEST_DATABASE_URL)
    with admin.begin() as conn:
        conn.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        conn.execute(text(f"CREATE SCHEMA {schema} AUTHORIZATION {APP_ROLE}"))
    admin.dispose()


def test_backup_and_restore_keep_every_row(installation: Installation) -> None:
    s = installation.settings
    before = _counts(s.app_schema)
    assert before["hr_pay"] > 0 and before["fact_cost_center"] > 0
    engine = create_app_engine(s)
    try:
        dump = backup.backup(s, engine)
        assert dump.name.endswith(f"-{migrate.head_revision()}.dump")
        assert backup.backups(dump.parent) == [dump]
        with pytest.raises(backup.BackupError, match="not empty"):
            backup.restore(s, engine, dump)
        _empty_schema(s.app_schema)
        backup.restore(s, engine, dump)
    finally:
        engine.dispose()
    assert _counts(s.app_schema) == before
    # Row-level security and the triggers are back, and the app role is held to them again.
    assert TEST_DATABASE_URL
    admin = create_engine(TEST_DATABASE_URL)
    with admin.connect() as conn:
        forced = conn.execute(
            text(
                "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class "
                f"WHERE oid = '{s.app_schema}.hr_pay'::regclass"
            )
        ).scalar_one()
        owner = conn.execute(
            text(f"SELECT tableowner FROM pg_tables WHERE schemaname = '{s.app_schema}' LIMIT 1")
        ).scalar_one()
    admin.dispose()
    assert forced and owner == APP_ROLE
    # The schema was dropped and made again: the backup role reads it again, so the next
    # backup works.
    engine = create_app_engine(s)
    try:
        assert backup.backup(s, engine).exists()
    finally:
        engine.dispose()
    engine = create_app_engine(s)
    with engine.connect() as conn:
        assert conn.execute(text(f"SELECT count(*) FROM {s.app_schema}.hr_pay")).scalar_one() == 0
    engine.dispose()


def test_backups_beyond_keep_are_deleted(installation: Installation) -> None:
    s = installation.settings.model_copy(update={"backup_keep": 2})
    engine = create_app_engine(s)
    try:
        written = []
        for stamp in ("20260101T000000Z", "20260102T000000Z"):
            old = s.backup_dir / f"planlayer-{stamp}-0001.dump"  # type: ignore[operator]
            old.parent.mkdir(parents=True, exist_ok=True)
            old.write_bytes(b"")
            written.append(old)
        unrelated = s.backup_dir / "notes.txt"  # type: ignore[operator]
        unrelated.write_text("kept")
        newest = backup.backup(s, engine)
    finally:
        engine.dispose()
    assert backup.backups(newest.parent) == [written[1], newest]
    assert unrelated.exists()


def test_restore_refuses_a_dump_of_another_schema(installation: Installation) -> None:
    s = installation.settings
    assert TEST_DATABASE_URL
    other = s.model_copy(update={"app_schema": f"{s.app_schema}_other"})
    engine = create_app_engine(s)
    admin = create_engine(TEST_DATABASE_URL)
    try:
        dump = backup.backup(s, engine)
        with pytest.raises(backup.BackupError, match="does not exist"):
            backup.restore(other, engine, dump)
        with admin.begin() as conn:
            conn.execute(text(f"CREATE SCHEMA {other.app_schema} AUTHORIZATION {APP_ROLE}"))
        with pytest.raises(backup.BackupError, match="holds the schema"):
            backup.restore(other, engine, dump)
    finally:
        engine.dispose()
        with admin.begin() as conn:
            conn.execute(text(f"DROP SCHEMA IF EXISTS {other.app_schema} CASCADE"))
        admin.dispose()


def test_backup_needs_its_settings(migrated: Settings) -> None:
    engine = create_app_engine(migrated)
    try:
        with pytest.raises(backup.BackupError, match="PLANLAYER_BACKUP_DIR"):
            backup.backup(migrated, engine)
    finally:
        engine.dispose()


def test_behind_names_the_revision_an_upgrade_starts_from(migrated: Settings) -> None:
    assert migrate.behind(migrated) is None
    migrate.downgrade(migrated, "0031")
    assert migrate.behind(migrated) == "0031"
