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

## Supported versions

Only the newest release receives patches. A fix for an older version is the upgrade to the
newest one; there are no maintenance branches.

Patches come from main. While main holds only fixes since the last release, the release PR makes
a patch. The first `feat` merged turns it into the next minor (or major) release, and the
current version receives no more patches. A feature that is done while the current version may
still need fixes waits on its branch.

Planning runs in rounds, so installations upgrade between them ([Installing](install.md#upgrades)): during a
round only a patch that lets planners work again, after it any release, a major included.

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

## Commit messages and the changelog

Commits follow Conventional Commits, and they are the changelog's first draft: `feat`, `fix` and
`perf` messages appear in `CHANGELOG.md` and the release notes as written, under *Features*,
*Bug Fixes* and *Performance*; `refactor`, `docs`, `test` and `chore` don't. So a `feat` or `fix`
message says what changed for an admin or a user, in one line, not how it was built. A breaking
change is marked `feat!:` (or a `BREAKING CHANGE:` footer); before 1.0.0 it makes a minor
release.

## Release notes

Each GitHub release says:

- what changed, for admins and for users;
- whether the release migrates the app store (the container backs it up first);
- *Action required*, when there is anything to do besides pulling: a new setting, a script for
  the DBA, a changed variable.

## Making a release

release-please (`.github/workflows/release-please.yml`, `release-please-config.json`) keeps one
release PR open on main, titled `chore: release 0.6.0`. On every push it recomputes the next
version from the commits since the last release (`fix` → patch, `feat` → minor) and updates the
PR: the version in `backend/pyproject.toml`, `backend/uv.lock`, `frontend/package.json` and its
lock file, `version.txt`, and the new section of `CHANGELOG.md`. A job after it sets the minor
version new installations start on (`PLANLAYER_VERSION` in `deploy/.env.example` and the default
in `deploy/compose.yaml`) to the one the PR releases; a pre-release leaves it.

1. Just before releasing, edit the release PR: the `CHANGELOG.md` section and the PR's
   description, which becomes the release notes. Add whether it migrates and *Action required*.
   A push to main rewrites the PR, so edit last. A `Release-As: 0.6.0` footer on a commit sets
   the version by hand.
2. Merge it. release-please tags `v0.6.0` and publishes the GitHub release; the release workflow
   (`release.yml`, called from release-please's) builds and pushes the images (`0.6.0`, `0.6`,
   `latest`) and signs them. It refuses a tag that doesn't match `pyproject.toml`.
3. Check the published image: `cosign verify ghcr.io/rlei-odes/planlayer:0.6.0
   --certificate-identity-regexp 'https://github.com/rlei-odes/planlayer/' --certificate-oidc-issuer
   https://token.actions.githubusercontent.com`.

Releases are immutable once published: a mistake is fixed by the next release. A tag pushed by
hand (`v0.6.0`, on a commit that declares that version) also runs `release.yml`.

Admins see the new version in Planlayer's update notice once the GitHub release is published.
