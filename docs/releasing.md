# Releasing

How Planlayer's versions are numbered and what each kind of release may contain. Installations
pin a minor version (`PLANLAYER_VERSION=0.5`) and receive its patch releases on the next
`docker compose pull`, without reading anything first. The rules below are what makes that safe.
The release mechanics are in [ADR 0031](adr/0031-deployment-shape.md).

## Kinds of release

| Release | May contain | Migrations |
|---|---|---|
| **Patch** (0.5.1) | Bug fixes and security fixes | Only to correct what a release got wrong: a constraint, an index, a repair of bad data. No new columns, tables or features. Nothing the admin has to do. |
| **Minor** (0.6.0) | Features, new settings, new variables | Yes |
| **Major** (1.0.0, 2.0.0) | Breaking changes: a variable renamed or removed, an `install.sql` change the DBA must run, a new Postgres major version required | Yes |

Before 1.0.0 a breaking change goes into a minor release, marked *Action required* in its notes.

## Rules for every change

- **Migrations go forward only.** A released migration is never edited; a fix is a new
  migration. Each one runs on the previous release's data, and on an app store restored from an
  older backup.
- **Variables and settings change in two steps.** A rename or removal is announced one minor
  release ahead: the old name keeps working and logs a warning that names the new one.
- **The install script stays runnable on a new installation**, and a change existing
  installations need is shipped as its own script the DBA runs, named in the release notes.

## Migrations

Migration `0001` is the baseline: the app store of 0.5.0 in one step, as SQL
(`0001_baseline.sql`, the schema as `__SCHEMA__`) with its seed rows. Every change after it is a
migration of its own, numbered on from `0002`, with an `upgrade` and a `downgrade`; the baseline's
downgrade empties the schema. A released migration, the baseline included, is never edited.

A migration that reads or rewrites rows of a table under row-level security does it inside
`migrate.every_row(conn, schema, tables)`: forced row-level security lifted and the tables'
triggers off for that block, both restored after it. The app role owns the tables, but forced
row-level security would show it no rows, and a rewrite must not trip a lock or the audit log.

## Release notes

Each GitHub release says:

- what changed, for admins and for users;
- whether the release migrates the app store (the container backs it up first);
- *Action required*, when there is anything to do besides pulling: a new setting, a script for
  the DBA, a changed variable.

## Making a release

1. `backend/pyproject.toml` and `frontend/package.json` declare the version (`0.6.0`).
2. A tag `v0.6.0` on that commit, pushed. The release workflow refuses a tag that doesn't match
   `pyproject.toml`; it builds and pushes the images (`0.6.0`, `0.6`, `latest`) and signs them.
3. The GitHub release with its notes, from the tag.
4. Check the published image: `cosign verify ghcr.io/rlei-odes/planlayer:0.6.0
   --certificate-identity-regexp 'https://github.com/rlei-odes/planlayer/' --certificate-oidc-issuer
   https://token.actions.githubusercontent.com`.

Admins see the new version in Planlayer's update notice once the GitHub release is published.
