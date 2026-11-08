# Planlayer

An open-source, self-hosted web app for corporate budgeting and forecasting. Planners enter plan values in an Excel-like grid next to their actuals. The data lives in plain Postgres tables that any BI tool can read.

Status: early development. A first end-to-end slice runs: sync from Postgres views, a budget version, and a planning grid for one cost center.

- [docs/scope.md](docs/scope.md): what v1 does and does not do
- [BACKLOG.md](BACKLOG.md): decisions, spikes, test data and epics
- [planning-app-brief.md](planning-app-brief.md): the original product brief
- [docs/adr/](docs/adr/): architecture decision records
- [docs/research/](docs/research/): notes on related projects

## How it runs

One container, connected to an existing Postgres 18 (or newer). Planlayer keeps its own data in a schema `planlayer_app`, and publishes plan data to a schema `planlayer_out`, typically in the warehouse database next to the gold layer. It reads master data and actuals from views the admin points it to. A DBA creates the schemas and roles with [deploy/sql/install.sql](deploy/sql/install.sql); Planlayer then migrates its own schema.

| Variable | Meaning |
|---|---|
| `PLANLAYER_APP_DATABASE_URL` | App store connection (required) |
| `PLANLAYER_SOURCE_DATABASE_URL` | Source views connection (default: app store) |
| `PLANLAYER_OUTPUT_DATABASE_URL` | Published output connection (default: app store) |
| `PLANLAYER_APP_SCHEMA`, `PLANLAYER_OUTPUT_SCHEMA` | Schema names (default `planlayer_app`, `planlayer_out`) |

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

Test data (the fictional manufacturer Nordwerk, see [tools/gen_testdata](tools/gen_testdata/README.md)) into the `dwh` database:

```
cd tools/gen_testdata && uv run gen-testdata --db postgresql://postgres:postgres@localhost:5432/dwh
```

Sync the test company into the app and create a budget to plan (the planning page is on http://localhost:8000, or :5173 with Vite):

```
docker compose exec app planlayer seed-dev-mappings     # map the datasets to the gold views
docker compose exec app planlayer sync --entity DE
docker compose exec app planlayer create-version B2026 --name "Budget 2026" --fiscal-year 2026
docker compose exec app planlayer seed-dev-users
```

The development login (`PLANLAYER_DEV_LOGIN`, on in `compose.yaml`, refused in production) lets you log in as any of these users without a password: the cost center owners, `controller@nordwerk.example`, `admin@nordwerk.example`, `cfo@nordwerk.example` and `auditor@nordwerk.example`. A new planning round starts in status `setup`; the controller opens it on the status page. Mappings, sync previews and master data are on the "Master data" page; `planlayer sync --dry-run` shows the plan without writing.

Checks, as run in CI:

```
cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy
PLANLAYER_TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/postgres uv run pytest
cd frontend && npm run lint && npm run typecheck && npm run build
```

`pre-commit install` enables ruff and oxlint on commit.

## Licence

[Apache License 2.0](LICENSE)
