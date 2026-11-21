# 0031: Deployment shape

- **Status:** Accepted
- **Closes:** D-25
- **Date:** 2026-11-21

## Context

Planlayer runs at companies that host it themselves, usually next to a Postgres their DBA
already runs (T-14), sometimes without any. An installation must be possible from published
images and one compose file, upgrades must be routine, and a failed upgrade must be undone
without a developer.

## Decision

### The image

- One image, `ghcr.io/rlei-odes/planlayer`, for `linux/amd64` and `linux/arm64`. The API serves
  the built frontend. Configuration comes from `PLANLAYER_*` environment variables only.
- It carries the Postgres 18 client tools (`pg_dump`, `pg_restore`, `psql`: backups, ADR 0032,
  and the bundled database's first start), the install script at `/app/deploy/install.sql`, and
  the Nordwerk test warehouse generator (`gen-testdata`) for the demo. One image for production
  and demo keeps "what the demo runs" and "what is installed" the same.
- It runs as an unprivileged user (uid 10001).

### Releases

A tag `vX.Y.Z` on a commit whose `backend/pyproject.toml` declares `X.Y.Z` builds the image and
pushes it as `X.Y.Z`, `X.Y` and `latest` (a pre-release `vX.Y.Z-rc.N` only under its own tag),
with an SBOM and build provenance attached and a keyless cosign signature on the digest
(`.github/workflows/release.yml`). Mirroring to Docker Hub is switched on by the repository
variable `DOCKERHUB_IMAGE`. Installations pin `X.Y`: they receive patch releases by pulling, and
take minor releases on purpose.

### Connections

Three URLs (T-14): `PLANLAYER_APP_DATABASE_URL` (required), `PLANLAYER_SOURCE_DATABASE_URL` and
`PLANLAYER_OUTPUT_DATABASE_URL`, which default to the app store's. A fourth,
`PLANLAYER_BACKUP_DATABASE_URL`, is the backup role's (ADR 0032).

### The database

Planlayer never creates schemas or roles. The DBA runs `install.sql`, which creates the app role,
the schemas, the BI group role, optionally the backup role, and the read grants on the source.
Its source part is optional, for a warehouse on another server whose DBA grants read access
there.

For a company without Postgres, `deploy/compose.yaml` bundles one (profile `postgres`): at its
first start a one-shot step in the Planlayer image runs `install.sql` as the database's
superuser, taking the roles' passwords from the connection URLs, and does nothing on later
starts.

### Migrations at start

The container runs `planlayer migrate && planlayer serve`: an upgrade is pulling the new image
and starting it. Before it upgrades an existing app store, `planlayer migrate` takes a backup
(ADR 0032) named after the revision it upgrades from. In production it refuses to upgrade
without one, unless run with `--without-backup`; in development, without backup settings, it
says so and upgrades.

### Rolling back an upgrade

Migrations only go forward in production. A failed or unwanted upgrade is rolled back by:

1. stopping the app;
2. the DBA dropping and recreating the empty app schema (owner: the app role);
3. `planlayer restore` of the backup taken before the upgrade, run with the **previous** image,
   which restores and republishes;
4. starting the previous image.

Plan data entered between the upgrade and the rollback is lost; the rollback is for the first
hours after an upgrade. Downgrade migrations stay for development and tests only: a downgrade
would drop the columns the new version wrote, silently, where a restore is explicit about the
point in time it returns to.

## Consequences

- An installation is `deploy/compose.yaml` plus `planlayer.env`; the demo is
  `deploy/demo/compose.yaml` alone (G-3).
- Every upgrade has a backup by construction, so rollback needs no preparation.
- The image grows by the Postgres client (about 15 MB) and the generator (a few hundred KB).
- `pg_dump` 18 can't dump a newer server. When Postgres 19 is current, the image moves to its
  client, which still dumps 18.
- Background jobs (D-24) are not decided: scheduled backups run from the host's scheduler
  (cron, a systemd timer) until they are.
