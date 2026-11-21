"""Backup and restore of the app store (ADR 0032).

A backup is ``pg_dump --format=custom`` of the app schema, taken as the backup role: the app
role is subject to its own row-level security (forced), so only a role with ``BYPASSRLS`` sees
every row, and that role only reads. A restore loads the dump into an empty app schema as the
app role. Postgres enables row-level security and creates the triggers after the data is
loaded, so the rows go in unchanged. The published output is not backed up: a republish
rebuilds it from the restored app store.

Passwords go to the client tools in ``PGPASSWORD``, never on their command line.
"""

import logging
import os
import re
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Engine, make_url, text

from planlayer.config import Settings
from planlayer.store.migrate import current_revision

log = logging.getLogger(__name__)

PG_MAJOR = 18  # the client tools in the image; pg_dump refuses newer servers
_NAME = re.compile(r"^planlayer-\d{8}T\d{6}Z-[0-9a-z_]+\.dump$")


class BackupError(Exception):
    """A backup or restore that did not complete; the message says why, without secrets."""


def _libpq(url: str) -> tuple[str, dict[str, str]]:
    """A SQLAlchemy URL as a libpq URL without the password, and the environment carrying it."""
    u = make_url(url)
    env = {**os.environ}
    if u.password is not None:
        env["PGPASSWORD"] = str(u.password)
    plain = u.set(drivername="postgresql", password=None)
    return plain.render_as_string(hide_password=False), env


def _run(args: list[str], env: dict[str, str]) -> None:
    if shutil.which(args[0]) is None:
        raise BackupError(f"{args[0]} is not installed (the Planlayer image has it)")
    done = subprocess.run(args, env=env, capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise BackupError(f"{args[0]} failed: {done.stderr.strip()[-2000:]}")


def _server_major(engine: Engine) -> int:
    with engine.connect() as conn:
        return int(conn.execute(text("SHOW server_version_num")).scalar_one()) // 10000


def backups(directory: Path) -> list[Path]:
    """Planlayer's backups in ``directory``, oldest first (the names sort by time)."""
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.iterdir() if _NAME.match(p.name))


def backup(settings: Settings, engine: Engine) -> Path:
    """A new backup in the backup directory; the oldest beyond ``backup_keep`` are deleted."""
    if settings.backup_dir is None or settings.backup_database_url is None:
        raise BackupError("set PLANLAYER_BACKUP_DIR and PLANLAYER_BACKUP_DATABASE_URL")
    major = _server_major(engine)
    if major > PG_MAJOR:
        raise BackupError(f"the server is Postgres {major}; this image's pg_dump is {PG_MAJOR}")
    with engine.connect() as conn:
        revision = current_revision(conn, settings.app_schema) or "empty"
    directory = settings.backup_dir
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = directory / f"planlayer-{stamp}-{revision.lower()}.dump"
    url, env = _libpq(settings.backup_database_url.get_secret_value())
    # Written under a temporary name, so a failed or interrupted dump never looks like a backup.
    with tempfile.NamedTemporaryFile(dir=directory, prefix=".partial-", delete=False) as tmp:
        partial = Path(tmp.name)
    try:
        _run(
            [
                "pg_dump",
                "--format=custom",
                f"--schema={settings.app_schema}",
                f"--file={partial}",
                f"--dbname={url}",
            ],
            env,
        )
        partial.rename(target)
    finally:
        partial.unlink(missing_ok=True)
    for old in backups(directory)[: -settings.backup_keep]:
        old.unlink()
        log.info("deleted the old backup %s", old.name)
    log.info("backup written: %s (%d bytes)", target, target.stat().st_size)
    return target


def restore(settings: Settings, engine: Engine, dump: Path) -> None:
    """Load ``dump`` into the app schema, which must exist (the install script) and be empty."""
    if not dump.is_file():
        raise BackupError(f"{dump} is not a file")
    with engine.connect() as conn:
        found = conn.execute(
            text(
                "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = :schema"
            ),
            {"schema": settings.app_schema},
        ).scalar_one()
        exists = conn.execute(
            text("SELECT 1 FROM pg_namespace WHERE nspname = :schema"),
            {"schema": settings.app_schema},
        ).first()
    if exists is None:
        raise BackupError(
            f"schema {settings.app_schema!r} does not exist: create it with the install script"
        )
    if found:
        raise BackupError(f"schema {settings.app_schema!r} is not empty; restore needs it empty")
    url, env = _libpq(settings.app_url)
    # The dump creates the schema; the install script has already, with its owner and grants.
    listed = subprocess.run(
        ["pg_restore", "--list", str(dump)], env=env, capture_output=True, text=True, check=False
    )
    if listed.returncode != 0:
        raise BackupError(f"pg_restore can't read {dump.name}: {listed.stderr.strip()[-2000:]}")
    lines = listed.stdout.splitlines()
    schemas = [line.split(" SCHEMA - ")[1].split()[0] for line in lines if " SCHEMA - " in line]
    if schemas != [settings.app_schema]:
        raise BackupError(
            f"{dump.name} holds the schema {', '.join(schemas) or 'none'}, "
            f"not {settings.app_schema!r}"
        )
    entries = [line for line in lines if " SCHEMA - " not in line]
    with tempfile.NamedTemporaryFile("w", suffix=".list", delete=False) as f:
        f.write("\n".join(entries) + "\n")
        listing = Path(f.name)
    try:
        _run(
            [
                "pg_restore",
                "--no-owner",
                "--no-privileges",
                "--single-transaction",
                "--exit-on-error",
                f"--use-list={listing}",
                f"--dbname={url}",
                str(dump),
            ],
            env,
        )
    finally:
        listing.unlink(missing_ok=True)
    if settings.backup_database_url is not None:
        _grant_backup_role(settings, engine)
    log.info("restored %s into %s", dump.name, settings.app_schema)


def _grant_backup_role(settings: Settings, engine: Engine) -> None:
    """The backup role's read access to the restored schema, as the install script grants it.
    Dropping the schema for a restore drops those grants with it; the app role owns the
    schema, so it grants them again (the dump leaves privileges out: role names may differ)."""
    assert settings.backup_database_url is not None
    name = make_url(settings.backup_database_url.get_secret_value()).username
    if not name:
        return
    quote = engine.dialect.identifier_preparer.quote
    role, schema = quote(name), quote(settings.app_schema)
    with engine.begin() as conn:
        conn.execute(text(f"GRANT USAGE ON SCHEMA {schema} TO {role}"))
        for kind in ("TABLES", "SEQUENCES"):
            conn.execute(text(f"GRANT SELECT ON ALL {kind} IN SCHEMA {schema} TO {role}"))
            conn.execute(
                text(
                    f"ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} GRANT SELECT ON {kind} TO {role}"
                )
            )
