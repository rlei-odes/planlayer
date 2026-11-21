# 0032: Backup and restore

- **Status:** Accepted
- **Closes:** D-30
- **Date:** 2026-11-21

## Context

The app store (`planlayer_app`) is the only copy of plan data, comments, approvals and the audit
trail (T-14). The published output (`planlayer_out`) is derived from it. With the bundled Postgres
(ADR 0031) there is no DBA whose backups cover it, so Planlayer must take its own. Restore must
work into the same database and into another one (moving an installation).

The app role is held to its own row-level security (forced, ADR 0009); pay is hidden even from
the system user (ADR 0023). A dump taken as the app role would be incomplete, or refused.

## Decision

- **Mechanism:** `pg_dump --format=custom --schema=<app schema>`, run by `planlayer backup` in
  the Planlayer image, as the **backup role**: `LOGIN BYPASSRLS`, `USAGE` on the app schema and
  `SELECT` on its tables and sequences, nothing else. The install script creates it when given
  `backup_password` (default name `planlayer_backup`); `BYPASSRLS` needs a superuser to grant.
  The app role never bypasses row-level security.
- **Target:** a directory, `PLANLAYER_BACKUP_DIR` (`/backups`, a volume, in the compose file).
  Files are `planlayer-<UTC time>-<revision>.dump`, written under a temporary name and renamed
  when complete. The newest `PLANLAYER_BACKUP_KEEP` (default 14) are kept; other files in the
  directory are left alone. Copying the directory off the host is the operator's (rsync, restic,
  the storage's snapshots); S3-compatible targets can follow.
- **Schedule:** before every upgrade (ADR 0031), and from the host's scheduler, e.g. daily:
  `docker compose exec app planlayer backup`. A schedule inside Planlayer waits for D-24.
- **Not backed up:** `planlayer_out`. `planlayer restore` republishes every round's last publish
  run, which rebuilds it.
- **Restore:** `planlayer restore FILE` into an existing, **empty** app schema of the same name,
  as the app role: the dump's schema entry is left out (the install script made the schema, with
  its owner and grants), and owners and privileges are not restored (`--no-owner
  --no-privileges`), so the roles of the target may be named differently. Postgres loads the rows
  before it enables row-level security and creates the triggers, so they go in unchanged and the
  lock and audit triggers don't fire. Then the app role grants the backup role its read access
  again (dropping the schema dropped it), migrations bring an older backup up to the running
  version, and the output is republished.
- **Pay:** a backup holds every row past row-level security, salaries included. Until pay is
  encrypted at rest (ADR 0029, proposed), they are plain numbers in it; with the encryption, a
  restore needs the same key, kept apart from the backups.
- **Admin UI:** no backup status yet. It comes with D-24, whose job history will show the last
  backup.

## Consequences

- One more credential (the backup role's URL) in the deployment's environment. A leaked backup
  URL reads everything, pay included, so it is handled like the app's.
- The restore is tested in CI against the backup role (`tests/test_backup.py`): every row back,
  row-level security forced again, the app role the owner, the next backup working.
- A restore into a different schema name isn't possible (`pg_restore` can't rename schemas); the
  install script for the target uses the same schema name.
- Managed Postgres services differ in whether their admin may grant `BYPASSRLS`; where it can't,
  the provider's snapshots are the backup and `planlayer restore` isn't used.
