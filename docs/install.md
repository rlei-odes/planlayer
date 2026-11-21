# Installing Planlayer

Planlayer is one container image, `ghcr.io/rlei-odes/planlayer`, for amd64 and arm64. It needs
Postgres 18 for its app store: the company's own, or one bundled in the compose file. The
decisions behind this are in [ADR 0031](adr/0031-deployment-shape.md) (deployment) and
[ADR 0032](adr/0032-backup-and-restore.md) (backups).

## Try it: the demo

The Nordwerk test company in an open budget round, with users to log in as, from one file:

```
curl -fsSLO https://raw.githubusercontent.com/rlei-odes/planlayer/main/deploy/demo/compose.yaml
docker compose up -d
```

Open http://localhost:8000 after about a minute (the first start generates the test warehouse
and fills the round). The login lists the users; anyone logs in as anyone without a password,
so the demo stays on localhost. `docker compose down -v` removes it with its data.

## Production

### 1. Files

In a directory of its own on the server (e.g. `/opt/planlayer`):

```
curl -fsSLO https://raw.githubusercontent.com/rlei-odes/planlayer/main/deploy/compose.yaml
curl -fsSL -o planlayer.env https://raw.githubusercontent.com/rlei-odes/planlayer/main/deploy/planlayer.env.example
curl -fsSL -o .env https://raw.githubusercontent.com/rlei-odes/planlayer/main/deploy/.env.example
chmod 600 planlayer.env .env
```

`planlayer.env` holds Planlayer's settings and is read by the container. `.env` holds what the
compose file itself needs: the image version, the address it listens on, and the bundled
Postgres's superuser password. Passwords: letters and digits only (`openssl rand -hex 24`), as
they stand inside URLs.

### 2a. With the bundled Postgres

For a company without a Postgres of its own. In `.env`, set `POSTGRES_PASSWORD`. In
`planlayer.env`, replace each `<password>` with a new one (the app role's and the backup role's
must differ); keep host `db`. Then:

```
docker compose --profile postgres up -d
```

At the first start, `db-init` creates the database `planlayer` with the install script: the
app role and the backup role with the passwords from their URLs, the schemas `planlayer_app` and
`planlayer_out`. It does nothing on later starts. Always start with `--profile postgres`, or set
`COMPOSE_PROFILES=postgres` in `.env`.

The source views (master data and actuals) are in the company's warehouse: set
`PLANLAYER_SOURCE_DATABASE_URL` to it, with a role its DBA has given read access to the views
(`GRANT USAGE ON SCHEMA`, `GRANT SELECT ON ALL TABLES IN SCHEMA`). The published output goes into
the bundled database unless `PLANLAYER_OUTPUT_DATABASE_URL` points elsewhere.

### 2b. With the company's own Postgres

The DBA reviews and runs [deploy/sql/install.sql](../deploy/sql/install.sql) as a superuser. It
creates the roles and schemas; Planlayer never does. For example, the app store in its own
database and the output next to the warehouse's views:

```
psql -v app_db=planlayer -v output_db=dwh -v source_db=dwh -v source_schema=gold \
     -v app_password='…' -v backup_password='…' -f install.sql
```

| Variable | Default | |
|---|---|---|
| `app_db` | | the app store's database (exists already) |
| `output_db`, `source_db` | `app_db` | |
| `source_schema` | | the views Planlayer reads; unset when the warehouse is on another server and its DBA grants there |
| `app_schema`, `output_schema` | `planlayer_app`, `planlayer_out` | |
| `app_role`, `app_password` | `planlayer` | the role Planlayer connects as |
| `bi_role` | `planlayer_bi` | group role for BI tools reading the output: grant it to their users |
| `backup_role`, `backup_password` | `planlayer_backup` | the backup role; unset password: none (no backups by Planlayer) |

`backup_role` has `BYPASSRLS`, which only a superuser can grant: Planlayer's own role is held to
its row-level security, so only such a role can back up every row. It reads and nothing else.

Then set the URLs in `planlayer.env` to the company's servers, and:

```
docker compose up -d
```

### 3. HTTPS and the first admin

The container listens on `127.0.0.1:8000` (`PLANLAYER_LISTEN` in `.env`). Put the company's
reverse proxy in front of it with HTTPS (session cookies are `Secure` in production), and set
`PLANLAYER_PUBLIC_URL` to the address users open. A proxy that changes the `Host` header needs
it for the OpenID Connect callback.

At the first start the log shows a setup code:

```
docker compose logs app | grep "Setup code"
```

The login page takes it to create the first admin, a local account. Then the login provider:
[login.md](login.md).

## Backups

`planlayer backup` writes `planlayer-<time>-<revision>.dump` into the `backups` volume and keeps
the newest 14 (`PLANLAYER_BACKUP_KEEP`). Planlayer takes one before every upgrade by itself.
Schedule the daily one on the host, e.g. with cron (`crontab -e` as a user in the `docker` group):

```
15 2 * * * cd /opt/planlayer && docker compose exec -T app planlayer backup >> backup.log 2>&1
```

The volume is on the same server as the database, so copy the backups elsewhere too:

```
docker compose cp app:/backups ./backups-copy
```

or point your backup tool (restic, borg, the VM's snapshots) at the volume's directory
(`docker volume inspect planlayer_backups`).

Backups hold everything in the app store, salaries included, in plain numbers. Keep them where
only those who may read pay can reach them.

## Upgrades

Pin a minor version in `.env` (`PLANLAYER_VERSION=0.5`): `docker compose pull && docker compose
up -d` then brings the latest patch release of it. For a new minor version, read its release
notes, set it, and run the same two commands.

At start, the container backs up the app store when its schema is older than the image, then
migrates it (`planlayer migrate`), then serves. Without backup settings it refuses to upgrade
in production: set them, or run `docker compose run --rm app planlayer migrate --without-backup`
once on purpose.

### Rolling back

Migrations only go forward. To return to the previous version, restore the backup taken before
the upgrade (its name carries the revision it was taken at) with the previous image:

```
docker compose stop app
```

The DBA empties the app schema (with the bundled Postgres: `docker compose exec db psql -U
postgres -d planlayer`):

```sql
DROP SCHEMA planlayer_app CASCADE;
CREATE SCHEMA planlayer_app AUTHORIZATION planlayer;
REVOKE ALL ON SCHEMA planlayer_app FROM PUBLIC;
```

Then, with `PLANLAYER_VERSION` set back to the previous version in `.env`:

```
docker compose run --rm app planlayer restore /backups/planlayer-20261121T020000Z-0031.dump
docker compose up -d
```

`planlayer restore` loads the backup into the empty schema, grants the backup role its read
access again, runs the migrations up to the image's version, and republishes the output.
Whatever was entered after the backup is lost.

### Moving to another server

Run the install script there (the same schema names), copy a backup over, and run `planlayer
restore` against the new database. Its roles may have other names than the old ones.
