# Planlayer

**Budgeting and forecasting for companies that have outgrown their Excel files, on the Postgres
they already run.**

Planlayer is an open-source, self-hosted planning app. Cost center owners plan in an Excel-like
grid next to their actuals, controllers run the round from setup to approval, and the result lands
as plain, documented tables in the warehouse, where any BI tool reads it.

[planlayer.app](https://planlayer.app) · [Try the demo](#try-it) · [Install](docs/install.md) ·
[Scope](docs/scope.md)

![The status page of a budget round: progress per status and per hierarchy node, with actions per cost center](docs/assets/status-dark.png)

## What it does

**Planning**
- A grid for one cost center at a time: paste from Excel, fill-drag, keyboard flow, undo and redo,
  subtotals, actuals and other versions in comparison columns.
- Spreading that ties exactly to the total you enter: evenly, by a seasonality profile, or in
  proportion to last year's actuals or another version.
- Budgets planned by month or year, forecasts by quarter, with the closed months showing actuals.
- Notes with replies, @mentions and tasks on any line; item lists for lines planned item by item.

**Running the round**
- A setup page that composes a round from modules, entry forms per account group and account sets
  per cost center, with a live preview of the sheet planners will get.
- New rounds carried over from an earlier one, plan values optionally with an uplift.
- Submit, approve, reject and reopen per cost center; a status page that rolls progress up the
  hierarchy; locks enforced by the database.

**Modules**
- **Capacity:** work centers, activity types and hourly rates; planned hours credit their accounts.
- **Cost forwarding:** a cost center's costs passed on in full or as a fixed amount by shares, or
  at a rate per unit (m², PCs, licences), with the over- or under-coverage shown.
- **Workforce:** FTE, headcount or positions, with HR's people as the starting point, and personnel
  costs from salaries, raises, bonuses and contributions. Pay is read only by HR and the owner,
  and encrypted at rest.
- **Capex:** investments and large projects, confirmations by the approver, cash-out schedules.

**Data in and out**
- Master data, hierarchies and actuals synced from your Postgres views, after a preview of what
  changes. Without a source, the controller enters master data by hand.
- Publishing into a schema of its own (`planlayer_out`), after a preview of the rows it adds,
  changes and removes. The same data downloads as CSV or Parquet.

**Built for finance IT**
- Row-level security, audit log and locks live in Postgres, not only in the app.
- Login through OpenID Connect (Microsoft Entra ID, Okta, Google Workspace, Keycloak) or LDAP
  (Active Directory, OpenLDAP).
- One container, signed images for amd64 and arm64, migrations with a backup first, built-in
  scheduled backups.

Planned next: sales planning, plan reports as PDF, access for AI assistants through MCP
([BACKLOG.md](BACKLOG.md)). Out of scope: consolidation, FX translation, balance sheet planning
and multi-tenant hosting ([docs/scope.md](docs/scope.md)).

## Try it

```
curl -fsSLO https://raw.githubusercontent.com/rlei-odes/planlayer/main/deploy/demo/compose.yaml
docker compose up -d
```

Then open http://localhost:8000: Nordwerk Industrietechnik, a fictional manufacturer, in its
Budget 2026 and a 6+6 forecast. Plans are begun and some approved and published; there are
capacity hours, positions with personnel costs, cost forwarding, investments, notes and item
lists. Log in as any of its users (controller, owners, approvers, HR), no password needed.

## How it runs

One container, against Postgres 18 or newer: the company's existing server or one bundled with
it. Planlayer keeps its data in one schema (`planlayer_app`) and publishes to another
(`planlayer_out`), typically in the warehouse next to the gold layer. It reads master data and
actuals from views the admin points it to. A DBA creates the schemas and roles with
[deploy/sql/install.sql](deploy/sql/install.sql); Planlayer never creates them itself.

Installing, backups, upgrades and rollback: [docs/install.md](docs/install.md). Login:
[docs/login.md](docs/login.md). The published tables: [docs/output.md](docs/output.md).
Versioning: [docs/releasing.md](docs/releasing.md). The statuses of a round and its cost centers:
[docs/workflow.md](docs/workflow.md).

| Variable | Meaning |
|---|---|
| `PLANLAYER_APP_DATABASE_URL` | App store connection (required) |
| `PLANLAYER_SOURCE_DATABASE_URL` | Source views connection (default: app store) |
| `PLANLAYER_OUTPUT_DATABASE_URL` | Published output connection (default: app store) |
| `PLANLAYER_APP_SCHEMA`, `PLANLAYER_OUTPUT_SCHEMA` | Schema names (default `planlayer_app`, `planlayer_out`) |
| `PLANLAYER_PAY_KEY` | Key that encrypts pay and the OIDC client secret ([ADR 0029](docs/adr/0029-pay-encryption-at-rest.md)) |
| `PLANLAYER_PUBLIC_URL` | The address users open, e.g. `https://plan.example.com` (default: each request's) |
| `PLANLAYER_DEMO` | `true` for a demo installation: log in as any user without a password |
| `PLANLAYER_BACKUP_DATABASE_URL`, `PLANLAYER_BACKUP_DIR`, `PLANLAYER_BACKUP_KEEP` | Backups of the app store ([ADR 0032](docs/adr/0032-backup-and-restore.md)) |
| `PLANLAYER_UPDATE_CHECK` | `false` turns off the admins' notice of a new release |
| `PLANLAYER_LOGIN_*`, `PLANLAYER_OIDC_CLIENT_SECRET` | The login provider, instead of the Settings page ([docs/login.md](docs/login.md)) |

URLs use the form `postgresql+psycopg://user:password@host:5432/database`.

## Development

Requirements: Docker with Compose, [uv](https://docs.astral.sh/uv/), Node 24.

Everything in containers (database plus the built app on http://localhost:8000):

```
docker compose up --build
```

With reload, database in a container and the rest local:

```
docker compose up -d db
cd backend && cp .env.example .env && uv sync && uv run planlayer migrate && uv run planlayer serve --reload
cd frontend && npm ci && npm run dev        # http://localhost:5173, /api goes to the backend
```

Test data (Nordwerk, see [tools/gen_testdata](tools/gen_testdata/README.md)) into the `dwh`
database, then the demo's plans into an empty app store:

```
cd tools/gen_testdata && uv run gen-testdata --db postgresql://postgres:postgres@localhost:5432/dwh
docker compose exec app planlayer seed-demo
```

`compose.yaml` runs a demo installation: the login lists the users, among them
`controller@nordwerk.example`, `admin@nordwerk.example`, `cfo@nordwerk.example` and
`auditor@nordwerk.example`. Keycloak and lldap to develop the login against:
`docker compose --profile auth up -d keycloak lldap`
([docs/login.md](docs/login.md#developing-against-real-providers)).

Checks, as run in CI:

```
cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy
PLANLAYER_TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/postgres uv run pytest
cd frontend && npm run lint && npm run typecheck && npm run build
```

`pre-commit install` enables ruff and oxlint on commit. Design decisions are recorded in
[docs/adr/](docs/adr/), open work in [BACKLOG.md](BACKLOG.md). Bugs and security issues:
[GitHub issues](https://github.com/rlei-odes/planlayer/issues).

## Licence

[Apache License 2.0](LICENSE). Forks and derivative works keep the [NOTICE](NOTICE) file with its
copyright and attribution to [planlayer.app](https://planlayer.app).
