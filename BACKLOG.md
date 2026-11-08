# Planlayer backlog

Working backlog for the architecture phase and the v1 build. Source brief: [planning-app-brief.md](planning-app-brief.md).
Decision records go in `docs/adr/NNNN-title.md`. Each `D-` item is done when its ADR is merged.

---

## Decisions already taken

| # | Decision | Why |
|---|---|---|
| T-1 | **Open source, self-hosted, single tenant.** One company per install. No SaaS or multi-tenant concerns. | Keeps auth, RLS and ops simple. `docker compose up` is the product's front door. |
| T-2 | **Python backend.** | Spreading, rolling forecasts and later driver logic are numeric work. Python keeps that path open. Frontend types are generated from the OpenAPI schema so the two sides stay in sync. |
| T-3 | **Live connection to the customer's database for actuals and master data, plus export of plan values back.** Postgres first. | This is the core pitch: no CSV shuffling, and plan values land where the BI tools already read. |
| T-4 | **Source/target access goes through a pluggable adapter layer.** SQLAlchemy for SQL databases; other formats (Parquet, Delta/Iceberg, Snowflake, BigQuery…) are added later as further adapters. | Other warehouses and lakes become an extension, not a rewrite. |
| T-5 | **Test data models a mid-size manufacturer** (see section 4). | It covers both v1 areas (cost centers and sales by product × country) and naturally brings in multiple entities and currencies. |
| T-7 | **Amounts are stored natural-positive.** Revenue and costs are both entered and stored as positive numbers; the account type supplies the sign for net results and for favourable/unfavourable variances. The sign rules of the source and the target are configured on a setup page. Planlayer converts on sync in and on export out, and an invariant test checks that revenue above plan counts as favourable. | It matches what planners type, keeps percentage mode and spreading simple, and puts conversion in one place, the system boundary. |
| T-8 | **Licence: Apache 2.0.** | Permissive, widely accepted by companies, and it includes an explicit patent grant. The grid component candidates (Glide Data Grid, RevoGrid core) are MIT, which is compatible. Commercial grids (AG Grid Enterprise, Handsontable) are excluded. |
| T-6 | **All values are in the group (main) currency.** Actuals arrive already converted; plans are entered in group currency. Planlayer stores no currency per fact and does no conversion. | Keeps FX entirely out of scope. Conversion belongs in the source views, where the warehouse already holds the rates. |
| T-9 | **Frontend: React + TypeScript + Vite,** on Node 24 LTS with npm. The grid component is RevoGrid (ADR 0006). | Glide Data Grid is React-only and RevoGrid has a React wrapper, so React fits both candidates. It also has the largest contributor pool. |
| T-10 | **Backend stack:** Python 3.13, FastAPI + Pydantic v2, SQLAlchemy 2.x, Alembic, psycopg 3 with a connection pool, uv, pytest. Endpoints are **synchronous**. **SQLAlchemy Core** for fact tables, grid queries, spreading and adapters; the **ORM** only for setup and config tables. | Sync code is simpler to write, test and debug, and a planning app's load doesn't need async. Bulk writes and generated `dim_*` columns (D-01) are awkward through the ORM. |
| T-11 | **Layering:** calculation rules (spreading, rounding, sign conversion, fiscal periods, percentage mode) live in a pure-Python `domain` package with no I/O. Rules that must never be bypassed (locks, audit, unique fact grain) are enforced in the database. The server computes spreads; the grid shows the result. | The rule-heavy code stays easy to test in isolation, and a bug in the API can't write to a locked version or skip the audit. A single implementation of spreading means totals tie exactly. |
| T-12 | **Amounts are `numeric` in the database, `Decimal` in Python, and strings in the API.** JavaScript numbers are used for display only. | Floating point can't represent cents exactly; strings carry the exact value across JSON. |
| T-13 | **CI from the first commit of code:** GitHub Actions running ruff, mypy, pytest against a real Postgres service, and the frontend lint and typecheck. | Invariants enforced in the database (T-11) can only be tested against a real Postgres. |
| T-14 | **Planlayer runs as one container against the customer's existing Postgres, in two schemas of its own, reached through up to three connections.** <ul><li>**App store:** `planlayer_app` holds everything the app works with: config, members and actuals synced from the source, plan values in every status, workflow, audit and comments.</li><li>**Source:** the customer's contract views (D-11), read only.</li><li>**Output:** `planlayer_out` holds the plan data the controller has published (T-21), as BI-readable tables and views.</li></ul>Each connection may point at the same database or a different one. The typical warehouse setup is `planlayer_app` in its own `planlayer` database, with the source views and `planlayer_out` in the `dwh` database. **No query joins across connections:** the source is synced into the app store (D-09), and the output is written by publishing (D-12). Schema names are configurable. Planlayer ships a **scheduled backup of `planlayer_app`**. `planlayer_out` is derived, and is rebuilt by republishing. | Nothing extra to operate: one container and its connection strings. `planlayer_out` sits next to bronze/silver/gold, so BI tools and dbt read plan data like any other warehouse table. Plan data is primary data that can't be rebuilt from the ERP, so the app store gets its own backup even when the warehouse backups are weak. |
| T-15 | **Postgres 18 or newer** (the current major release) for the app store and for development and CI. | Planlayer is new, so it doesn't need to carry older versions. The source and output adapters only use plain SQL, so the customer's views are not affected. |
| T-16 | **Sources are mapped, not prescribed.** Planlayer defines the logical datasets it needs (cost centers, accounts, hierarchies, actuals, …) with required and optional fields. For each dataset the admin points to a view or table, or enters a query, then maps its columns to the fields. The mapping is validated and previewed before the first sync. | Customers' warehouses differ, and further source types will follow (T-4). The mapping is the one place where a customer's format meets Planlayer's model. |
| T-17 | **Master data is imported, then curated in Planlayer.** For example, cost centers (id, name, description, owner, …) are synced from the source. Per planning round, the controller enables or disables cost centers for planning. The **owner** attribute grants that person the right to plan their cost centers by default. The admin can add further grants. | The source decides what exists. The controller decides what is planned and by whom, without changing the source. |
| T-18 | **One fiscal year definition per installation.** Its start month is configurable, defaulting to January. | One group, one calendar. A configurable start month covers groups whose fiscal year isn't the calendar year. |
| T-19 | **Hierarchy changes restate (v1).** Plan and actuals always roll up under the current hierarchy. A moved cost center takes all its values, past and future, to its new parent. | It's what planners expect, and it's the simplest correct model. As-of roll-up can be added later, because members keep their validity dates. |
| T-20 | **Approval happens per cost center.** A status page shows the state of every cost center and rolls it up by department and any hierarchy node. | Cost centers are what owners plan and approvers sign off. A roll-up shows the controller where the round stands. |
| T-21 | **Publishing to `planlayer_out` is an explicit action by the controller.** Nothing is published automatically. | Controllers decide when plan data becomes visible to BI, and what reaches it is never a half-finished draft by accident. |
| T-22 | **Login: OIDC and LDAPS,** taken over from the sister repo `lancy-next`. Early iterations use a fake development login, behind a setting that production refuses to start with. | Both cover the identity providers companies run. Reusing a proven implementation saves building it twice, and a fake login keeps early iterations moving. |
| T-23 | **`layer` and `measure` are in the fact key from day one.** v1 uses only the `base` layer, and the `amount` and `quantity` measures. | Adding either column later means migrating every plan row. |
| T-24 | **Planning is set up per fiscal year from modules.** On a setup page, the controller defines for a fiscal year *what* is planned (planning areas, e.g. cost centers, sales; later headcount) and *in what form* per area or account group: full-year values only; monthly values; a full-year value spread evenly; a full-year value spread by a seasonality profile; or a full-year value spread by a reference (e.g. prior-year actuals). The form decides which cells are editable and how a typed value turns into stored months. Planning modules share one framework (setup, grid, spreading, workflow), so a module such as headcount planning is added without a new architecture. | Companies plan different things in different detail, and the detail changes from year to year. A per-year composition keeps that configuration, not code. |
| T-25 | **The planning page shows one planning unit at a time:** typically one cost center, with its accounts × months. The planner switches between the units they may plan. Overviews across units are separate pages (the status page, T-20; reports). | It's how cost center owners plan. It keeps grids small: a cost center grid reads in about 2 ms on the large dataset (S-01, Q1b). |

**Implied by T-3/T-4/T-14:** the **app store** (the `planlayer_app` schema, holding plan facts, versions, workflow, change log and permissions) is a separate thing from **source/target adapters** (the customer's views and the published output). The app store can use Postgres-only features (triggers, RLS). The adapters must not rely on them. Because the source and output are reached through adapters, they can sit in the same database as the app store, a different database, or later a lake.

**Publishing across databases:** when the output is in a different database, a publish can't be one transaction. Publishing is therefore idempotent:
- replace the version's rows in `planlayer_out` in a single transaction;
- then record the publish run in `planlayer_app`;
- a failed run is simply repeated.

**Database roles (T-14):**
- **Planlayer's own role:** owns `planlayer_app` and `planlayer_out`, and has `SELECT` only on the source views.
- **BI users:** `SELECT` on `planlayer_out` only.
- **Nobody outside Planlayer** writes to either schema.

---

## 1. Discovery

- [x] **R-1** Read the Konsolidat schema, the budget-layer model and the dbt assertion suite. Notes: [docs/research/konsolidat.md](docs/research/konsolidat.md).
- [x] **R-2** Read the Budget & Forecast for Claude Code schema: version locking, roll-forecast, change log, import dry-run. Notes: [docs/research/budget-forecast.md](docs/research/budget-forecast.md).
- [x] **R-7** Read Konsolidat's Frappe app (`konsol`): budget doctypes, entry modes, spreading, concurrency, signs. Notes: [docs/research/konsol-app.md](docs/research/konsol-app.md).
- [ ] **R-3** Check Plannifico activity. Note any concepts worth keeping (top-down/bottom-up mechanics).
- [ ] **R-4** Interview 3–5 controllers / FP&A people about their current Excel process. Use a fixed question list: planning areas, grain, cycle length, number of planners, how approvals work, where the data ends up.
- [x] **R-5** One-page scope statement: [docs/scope.md](docs/scope.md).
- [x] **R-6** Licence: Apache 2.0 (T-8).
- [x] **R-8** Apache 2.0 `LICENSE` file in the repo (https://github.com/rlei-odes/planlayer).

## 2. Architecture decisions (ADRs)

Each item lists the options and how to decide. Items marked ⚙ are decided by a spike from section 3.

### Data model
- [x] **D-01 Fact table dimension encoding.** Decided: [ADR 0001](docs/adr/0001-fact-tables.md). One fact table per planning area, one column per dimension generated from the registry, grain as a unique index, actuals synced at plan grain.
- [x] **D-02 Hierarchy storage and roll-up.** Decided: [ADR 0002](docs/adr/0002-hierarchies.md). Named hierarchies with parent-child edges, alternate hierarchies in v1, a closure table rebuilt on sync, values on leaves only.
- [ ] **D-03 Time model.** One fiscal year definition per installation with a configurable start month (T-18). A fiscal year is named after the calendar year in which it starts (`planlayer.domain.periods`); periods are first-of-month dates. Open: period as first day of month vs. a period dimension table (fiscal year, fiscal period, calendar month), and whether day grain is ever needed (the brief says optional; recommendation: month only in v1). **Closed periods:** derived from where actuals exist (Budget & Forecast) or stored as an explicit close status per entity (Konsolidat). Recommendation: explicit, because roll-forecast and the open-for-input periods depend on it.
- [x] **D-04 Planning area definition and the yearly setup.** Decided: [ADR 0007](docs/adr/0007-planning-rounds.md). Planning rounds (version × fiscal year) composed of modules; entry forms per account group with per-account override (monthly, annual, annual even / profile / reference); sheets show accounts with comparison or plan values plus planner-added accounts, minus accounts the controller hides for the round; spreading fallbacks set.
- [ ] **D-05 Percentage-mode cells.** Storage: `input_type` + `ref_line` + stored percentage, with the amount derived on write vs. on read. Recalculation order when the reference line changes. Only one level of reference in v1.
- [x] **D-06 Measures and units.** Decided: T-23 (`measure` in the fact key; v1 has amount and quantity).
- [x] **D-07 Currency handling.** Decided: T-6 (group currency only).
- [x] **D-08 Layers in v1 or later.** Decided: T-23 (`layer` in the fact key, only `base` in v1). The model for later layers: each role edits its own layer, the effective value is the sum, and no "final" row is stored.
- [x] **D-26 Sign convention.** Decided: T-7 (natural-positive, converted at sync and export, configured in E-12, checked by F-07).
- [ ] **D-28 Spread basis catalogue.** What a planner can spread a total by, on two axes: **time** (year/quarter → months) and **hierarchy** (group node → leaves). Candidate bases: equal; a **seasonality profile** (see below); in proportion to **existing values in the target**; in proportion to a **reference** (prior-year actuals, prior-year plan, another version, chosen per spread); and copy the reference + uplift %. Also define the fallbacks: what happens when the reference is all zero or missing for some leaves (fall back to equal, block, or ask), how negatives in the reference are handled, and the rounding rule (exact tie). Neither reference project spreads by reference data, so this is a differentiator.
  **Seasonality profiles** are named, user-definable sets of values covering the fiscal year (one per month):
  - **Entry:** either as percentages (validated to sum to 100%) or as any relative numbers (e.g. units sold per month, or 8/8/10/…), which Planlayer normalises to shares. The profile stores what was entered plus its input type, so it can be shown and edited as typed. The normalised shares are derived.
  - **Values:** zero is allowed (e.g. a factory shutdown in August); negative values are not. A profile of all zeros is invalid.
  - **Derived from history:** a profile can be created from a reference ("prior-year actuals of cost center X", "average of the last 3 years") and saved as a fixed, named profile, so it no longer moves when actuals are restated.
  - **Decided in ADR 0007:** profiles are installation-wide; entry forms choose them per account group; spreading fallbacks (negative reference = 0, empty reference = even spread, marked in the grid).
  - **Open questions:**
    - **Scope:** settled (installation-wide).
    - **Defaults:** can a default profile be attached to an account group or a dimension member (e.g. revenue lines use "Sales seasonality")?
    - **Sub-ranges:** re-normalised; built into the engine (ADR 0003).
    - **Engine defaults:** the engine (`planlayer.domain.spreading`) takes the policy as parameters: a negative reference value is an error or counts as 0; an all-zero reference is an error or falls back to equal. Decide the default per entry mode. In a single node × months spread by reference, a leaf without reference values (e.g. a cost center that didn't exist last year) receives 0. Decide whether that needs a warning or a per-leaf fallback.
- [ ] **D-29 Entry modes for v1.** The entry form per line comes from the yearly setup (T-24); the grid shows one planning unit at a time (T-25). Which ways of entering data the grid supports: typing in month cells; typing a **year (or quarter) total** that spreads immediately and visibly using D-28; **paste** of a block from Excel/LibreOffice; fill-drag; and a **spread dialog** on a selected range or total ("spread 1.2M over Jan–Dec by prior-year actuals"). Also decide whether entry in a percentage cell (D-05) and an "uplift selection by x%" action are in v1.

### Integration
- [x] **D-09 Live query vs. sync into the app store.** Decided by T-14: master data and actuals are **synced** into `planlayer_app`, on demand and on a schedule. The grid only ever queries the app store. Reasons:
  - the source may be in another database, and the grid joins actuals and plan in one query;
  - the speed of customer views is unknown;
  - restated actuals must not silently change locked versions or the basis of past spreads.

  "Live" means "connected and refreshable", not "queried per cell". What a sync does is set by D-27.
- [x] **D-10 Adapter interface.** Decided: [ADR 0004](docs/adr/0004-adapters.md). Sources are relations or queries; reads stream Arrow batches cast to the contract schema from the field mapping; targets replace a partition atomically and idempotently.
- [x] **D-11 Logical datasets and source mapping (T-16).** Decided: [ADR 0012](docs/adr/0012-datasets-and-master-data.md). Datasets entities, cost centers, accounts and actuals; one query per dataset with named columns, typed by the dataset's contract; hierarchies by parent column or level columns; read-only preview before saving. Still to write: `docs/data-contract.md` for admins.
- [x] **D-12 Write-back / export shape.** Decided: [ADR 0011](docs/adr/0011-publishing.md). Scope chosen per run (approved only by default, or the whole round); one row per cost center × account × month with codes, status and publish time; a run replaces the round's rows in `planlayer_out` after a preview of rows added, changed and removed, and is refused if the result changed since the preview; the same rows as CSV or Parquet downloads.
- [ ] **D-13 Master data changes between syncs.** Roll-up restates under the current tree (T-19). Still to define, for a member that is renamed, moved, new, or no longer delivered by the source:
  - what happens to plan values keyed on a member that disappeared (kept and flagged as orphaned, not deleted);
  - how the sync report shows each kind of change to the controller;
  - whether a new member is enabled for planning automatically or waits for the controller (T-17): decided in [ADR 0007](docs/adr/0007-planning-rounds.md) for running rounds; a new cost center waits for the controller.
- [x] **D-31 Master data in Planlayer's tables.** Decided: [ADR 0012](docs/adr/0012-datasets-and-master-data.md). Master data lives in Planlayer's tables, fed by sync or entered by hand on the same page; manual members the source later delivers are matched and confirmed, their manual data kept as a shadow record; synced members can be renamed and moved locally, and the override survives syncs.
- [ ] **D-27 Sync discipline.** Done in [ADR 0012](docs/adr/0012-datasets-and-master-data.md): resolve everything before writing, list every unknown code, idempotent re-runs, a preview (dry run), confirmation of matches, a sync-run record with actor and counts. Open: no silent overwrite of changed actuals in closed periods without an explicit flag, and a source hash per run.

### Application
- [x] **D-14 Where totals and spreading run.** Decided: [ADR 0003](docs/adr/0003-totals-and-spreading.md). Spreading and grid subtotals in Python, roll-ups over unseen subtrees in SQL through the closure table; the spread is recorded in the change log.
- [x] **D-15 Python stack.** Decided: T-10.
- [x] **D-16 Frontend stack.** Decided: T-9.
- [x] **D-17 Grid component.** Decided: [ADR 0006](docs/adr/0006-grid-component.md). RevoGrid with `applyOnClose` and `range`; edits go through the planning model; undo and copy shaping are built in the app.
- [x] **D-18 Grid API shape.** Decided: [ADR 0010](docs/adr/0010-grid-api-and-concurrency.md). The whole cost center sheet in one response, with editability per row decided by the server; edits as a list applied all or nothing.
- [x] **D-19 Concurrency.** Decided: [ADR 0010](docs/adr/0010-grid-api-and-concurrency.md). Optimistic per cell with `row_version`; on a conflict nothing is saved and the grid reloads; no live push.
- [x] **D-20 Auth.** Decided: T-22 (OIDC and LDAPS from `lancy-next`; fake development login first).
- [x] **D-21 Permissions model.** Decided: [ADR 0009](docs/adr/0009-roles-and-scopes.md). Roles admin, controller and viewer as flags; planners from the matched owner (e-mail or login), the controller's override and deputies on cost centers or nodes; approvers read below their node; forced RLS over a per-user access view; hashed session tokens in a cookie.
- [x] **D-22 Workflow.** Decided: [ADR 0008](docs/adr/0008-workflow.md). One-step approval per round × cost center (not started → in progress → submitted → approved, reject and reopen with comment); approvers assigned to hierarchy nodes, nearest one above wins, controller as fallback; round status setup / open / closed; the lock trigger reads the status.
- [x] **D-23 Audit / change log.** Decided: [ADR 0005](docs/adr/0005-request-context-rls-audit.md). Statement-level triggers with transition tables; actor and batch from the per-transaction context. Retention and partitioning are set once real volumes are known.
- [ ] **D-24 Background jobs.** Syncs, exports and large spreads need a job runner. Options: in-process scheduler (APScheduler), a Postgres-backed queue (procrastinate, pgqueuer), or Celery/RQ + Redis. Recommendation: Postgres-backed, so there's no extra service in compose.
- [ ] **D-25 Deployment shape.** The app store location is decided (T-14). Still to define:
  - **The container image:** the API serves the built frontend, background jobs run in-process (D-24), images are published to GHCR, and configuration comes from env vars.
  - **What the customer's DBA must grant:** create the two schemas (in one database or two), triggers and RLS policies in `planlayer_app`, and `SELECT` on the source views. Write this as an install checklist plus a SQL script the DBA can review and run.
  - **Connection settings:** three URLs (app store, source, output); source and output default to the app store URL when unset.
  - **Migrations:** whether they run on start or as a separate `planlayer migrate` command, and how an upgrade is rolled back.
- [ ] **D-30 Backup and restore.** The scheduled backup of `planlayer_app` from T-14. `planlayer_out` is not backed up, because a "republish all" command rebuilds it from the app store. Decide:
  - the mechanism (`pg_dump --schema` from inside the container);
  - the targets (a mounted volume, S3-compatible storage);
  - the schedule and retention;
  - a tested restore command into an empty schema, followed by a republish;
  - whether the admin UI shows the last successful backup.
  Restore must also work into a different database (moving an installation).

## 3. Spikes

Throwaway code in `spikes/`, timeboxed. Each spike ends with numbers and a recommendation in the matching ADR.

- [x] **S-01 Fact table + pivot benchmark.** Build schema variants (a) and (b)/(c) from D-01. Load the large test dataset (TD-11: ~1M and ~10M rows). Measure: a 60 cost centers × 12 months grid with subtotals, a 12 countries × 40 product groups × 12 months grid, a 5,000-cell bulk upsert, and the same queries with RLS enabled. Timebox: 3 days.
- [x] **S-02 Hierarchy roll-up.** Recursive CTE vs. closure table on the ragged cost center tree and the account tree. Include an alternate hierarchy. Measure correctness against golden totals (TD-14) and speed. Timebox: 1 day.
- [x] **S-03 Spreading engine.** Implement the D-28 bases (equal, profile, proportional to target values, proportional to a reference version/period range) on both axes (time and hierarchy), plus the rounding rule (the sum must tie exactly to the entered total). Run it against the golden cases in TD-14 in both SQL and Python. Timebox: 2 days.
- [x] **S-04 Grid component shoot-out.** Results in [spikes/s04_grid](spikes/s04_grid/README.md). Same mock cell-block API behind Glide and RevoGrid. Test: paste of 2,000+ cells from Excel (including `1.234,56`-style locale numbers), fill-drag, keyboard flow (Enter/Tab/arrows), frozen row headers, editable subtotal rows, a percentage cell showing both % and amount, read-only/locked cell styling, 50k-cell scroll performance. Score on a fixed sheet. Timebox: 4 days.
- [x] **S-05 Adapter prototype.** SQLAlchemy Postgres adapter that reads members and actuals from the simulated warehouse (TD-10) into Arrow and writes a plan table back. Then a 2-hour check: can a DuckDB-over-Parquet adapter implement the same interface unchanged? Validates D-10. Timebox: 2 days.
- [x] **S-06 RLS + session context.** Prove per-request `SET LOCAL app.user_id` with a SQLAlchemy connection pool and RLS policies on member scopes, and that the lock trigger and audit trigger see the same context. Timebox: 1 day.

## 4. Simulated test data

All data is produced by one **deterministic, seeded generator** ([tools/gen_testdata/](tools/gen_testdata/README.md), Python; its README documents the company, the variants and the special cases). Same seed means same output. It writes:
- (a) a simulated customer warehouse into the `dwh` database (T-14): ERP-style raw tables in a `bronze` schema, and the contract views in a `gold` schema;
- (b) the same data as CSV/Parquet, for adapter tests.

### The fictional company: "Nordwerk Industrietechnik"
Mid-size manufacturer of industrial pumps and valves, ~400 employees.

- [x] **TD-01 Legal entities:** 4 entities (DE parent, CH, PL, US) with local currencies EUR, CHF, PLN, USD. Group currency is EUR. The raw warehouse schema (TD-10) keeps local-currency GL amounts plus a monthly rate table; the contract views convert to EUR, so Planlayer only ever sees EUR. Fiscal year January–December (T-18). The `small` variant is also generated with an April start, to test the configurable start month.
- [x] **TD-02 Cost center hierarchy:** ~60 leaf cost centers, 4 levels (company → division → department → cost center). **Ragged** (some branches only 2–3 deep). Includes: one cost center **created mid-year**, one **closed** at year end, one **moved** to a different department between years (reorg), one department **renamed**, and an **alternate hierarchy** (cost centers by function: production / sales / admin) over the same leaves. Every cost center carries an **owner** (T-17); a few have none, and one owner can't be matched to any user. Members carry validity dates so both D-13 options can be tested.
- [x] **TD-03 Account hierarchy:** ~150 leaf accounts in a P&L-shaped tree (revenue, COGS, personnel, material, travel, IT, depreciation, other). Includes ~10 aggregated planning lines (for "plan at aggregate level"). Includes some accounts with a negative sign convention (revenue as negative in GL).
- [x] **TD-04 Product hierarchy:** ~400 SKUs → ~40 product groups → 5 product lines. Sales are planned at product-group level.
- [x] **TD-05 Countries / regions:** ~12 sales countries in 4 regions. Products × countries is **sparse** (not every product sells everywhere).
- [ ] **TD-06 Customers (optional for v1):** ~300 customers with key-account flags, for testing a high-cardinality dimension.
- [x] **TD-07 Actuals:** 3 full years + current year to date, monthly, by entity × cost center × account, and sales by entity × country × product × measure (quantity, revenue). Realistic patterns: seasonality (Q4 peak, August dip), growth trend, one-off spikes, a few negative corrections, zeros vs. missing values kept distinct.
- [ ] **TD-08 Historical plan versions:** last year's Budget plus two Forecasts, so plan-vs-actual and version comparisons have data from day one.
- [x] **TD-09 Users and permissions** (`planlayer seed-dev-users`): 1 admin/controller, 8 cost center owners (scoped by the owner attribute, one of them additionally granted a second, disjoint subtree), 3 sales managers (scoped by region), 1 CFO approver, 1 read-only auditor.
- [x] **TD-10 Warehouse schema as a customer would have it:** an ERP-flavoured raw schema (GL lines with document numbers, cost center master with validity dates, German/English column names mixed) **plus** the contract-conforming views on top. GL lines are ledger-signed (debit − credit), and there are two variants of the actuals view, one in ledger signs and one in natural signs, so both E-12 sign settings are tested against the same data. This tests that admins can map their data via views, and gives S-05 a realistic source.
- [x] **TD-11 Scale variants:** `small` (1 entity, ~10 cost centers, 20 accounts, 1 year; for unit tests and demos), `standard` (as above), `large` (×20 cost centers and SKUs, 5 years, ≥10M fact rows; for S-01 and load tests). Two date modes: **fixed** (an anchored calendar, for golden tests) and **relative** (actuals run to last month, so the demo never goes stale).
- [ ] **TD-17 Seasonality profiles:** a set covering the edge cases: even; retail (Q4 peak); manufacturing with an August shutdown (a zero month); one entered as percentages summing to exactly 100; one entered as raw unit counts; one derived from prior-year actuals; and invalid ones (percentages summing to 99.9 or 100.1, a negative value, all zeros, 11 values instead of 12).
- [ ] **TD-16 Documented demo defects.** The `standard` dataset contains a deliberate, listed set of process problems for the demo and smoke tests to find: a cost center with no submitted plan, a plan above the prior year by >50%, unexplained variances, a forecast not rolled after a close, and a version past its start date that isn't approved. The list lives in the generator's README, and the smoke test asserts exactly these are flagged.
- [ ] **TD-12 Dirty-data fixtures** (each a small, separate dataset with a known expected error): actuals on unmapped accounts; a duplicate member code; a circular parent reference; an orphan parent code; source-provided subtotals that **don't** reconcile with leaves; Unicode/umlaut names and codes with leading zeros (`0420`); a period outside the open range; mixed decimal separators in CSV.
- [ ] **TD-13 Grid input fixtures:** clipboard payloads as copied from Excel and LibreOffice (TSV, locale-formatted numbers, empty cells, trailing newline, a 2,000-cell block) for S-04 and later end-to-end tests.
- [ ] **TD-14 Golden expected results:** hand-checked expected outputs for totals per hierarchy node, every spread method and basis (including rounding remainders, spreading onto all-zero leaves, a reference with gaps or negative months, and spreading by prior-year actuals for a cost center that didn't exist last year; seasonality profiles from TD-17, including a profile applied to a partial-year range), version copy with shift + uplift, percentage-mode recalculation, and the roll-forecast operation. These are the acceptance tests for S-02, S-03 and the v1 engine.

## 5. Foundations

- [x] **F-01** Repo layout: `backend/` (uv project, package `planlayer` with `api`, `domain`, `store`, `adapters`), `frontend/` (Vite, npm), `deploy/`, `docs/`. `tools/` and `spikes/` are created with their first content. pre-commit runs ruff and oxlint. Agent instructions in `CLAUDE.md`.
- [x] **F-02** `docker compose` for development: the app and one Postgres server with two databases, mirroring the typical setup from T-14. `planlayer` holds `planlayer_app`; `dwh` holds the simulated warehouse (seeded from TD-10) and `planlayer_out`. The database roles from T-14 are applied, so development runs under the same permissions as an installation. CI also runs the single-database variant (all three connections on one database).
- [x] **F-03** CI (`.github/workflows/ci.yml`): backend lint, format, mypy and pytest against a Postgres 18 service; the install script plus migrate and serve as the unprivileged app role across two databases; frontend lint, typecheck and build; image build. Ticked after the first green run on GitHub. Tests against the `small` dataset are added with the generator.
- [x] **F-04** TypeScript types generated from the OpenAPI schema: `planlayer api-types` (run as `npm run gen:api`) writes one type per API model to `frontend/src/schema.ts`, which `api.ts` names for the pages. A backend test fails when the committed file is out of date. The generator is in the backend because `openapi-typescript` needs the TypeScript 5 compiler API, which TypeScript 7 doesn't ship.
- [x] **F-05** Alembic baseline for the app store (revision `0001`, the installation row with the fiscal year start month; constraint naming convention; configurable schema via schema translation; `planlayer migrate` refuses to run without the DBA-created schema). Naming in `planlayer_out`: [ADR 0011](docs/adr/0011-publishing.md).
- [x] **F-06** ADR template and `docs/adr/` README.
- [ ] **F-07** Invariant suite: SQL assertions that return violating rows, run in CI and available to admins as a health check. Covers: unique fact grain, signs (revenue above plan is favourable, and a sync → export round trip returns the source signs unchanged), roll-ups tie to leaves **per slice** (not only per node total), no hierarchy cycles, every member assigned in each hierarchy, spreads tie exactly, no writes in locked versions, no plan values outside open periods. Each assertion gets a **must-flag fixture** (from TD-12) that has to make it fail, which proves the check catches the defect (Konsolidat's `.must_flag` pattern).
- [x] **F-08** `.gitignore` in place: it ignores `research/` (cloned reference repos), `docs_local/` (personal notes) and the usual Python/Node/data/editor artefacts.
- [ ] **F-09** Row-level security on the `large` dataset: sync it into an app store and measure grid reads, edits and the status page per role, since the access view is evaluated per statement (ADR 0009). On `standard`, a grid read takes ~30 ms for every role.
- [ ] **F-10** Confirm CI is green with database tests running as the unprivileged role `planlayer_test` (created by the tests; needs a CI database user that may create roles).

## 6. v1 epics (refined after section 2 is done)

- [ ] **E-01** Connections & sync: configure a source; map each dataset to a view, table or query and its columns to fields (T-16), with validation and preview; sync members and actuals; show a reconciliation report.
- [ ] **E-12** Setup pages (admin): one place for the settings a new installation needs before planning starts. It covers:
  - **Sign rules (T-7):** how the source delivers signs (natural or ledger) and how the export target expects them. The flip is defined per account type or account group, with per-account overrides. A preview shows sample accounts with their raw source value, the stored value and the exported value.
  - **Fiscal calendar:** the fiscal year's start month (T-18).
  - **Source mapping** (T-16), linked from E-01.
  - **Seasonality profiles** (D-28).
  - **Default comparison versions.**
  - **Layer and role mapping** (D-08, D-21).
  - **Validation:** the page warns about accounts with no type, since those can't be signed.
- [ ] **E-02** Dimensions & hierarchies: browse synced members, plus manual members/hierarchies for app-only dimensions.
- [ ] **E-03** Yearly planning setup (T-24): a modular page where the controller (or admin) composes a fiscal year's planning round from modules and sets each area's scope, grain, entry form per account group, open periods and visible comparison versions.
  - **Live preview:** next to the settings, a mock-up of the sheet the planner will get with the chosen options (columns, editable and read-only cells, hidden months, comparison columns), updating as options change.
  - **Three levels, each usable end to end:**
    - **Simple:** plan only, no actuals, full year only.
    - **Medium:** plan only, with seasonality profiles and the full cost center hierarchies.
    - **Full:** adds source queries that pull in actuals (E-01) as comparisons and spread references.
  - Built: rounds, starting points, entry forms and exceptions, comparisons, hidden accounts, seasonality profiles, the live preview (suggested order, step 11). Still open:
    - [ ] Hiding an account offers to clear its remaining plan values first, as an audited change batch (ADR 0007); today the save is refused and names the cost centers.
    - [ ] Open periods per round: which months planners may still edit (e.g. a forecast after six closed months); ties in with D-03's closed periods.
    - [ ] Restricting a module to account groups, so a cost center's "add account" offers only the module's accounts (ADR 0007).
    - [ ] Enabling and disabling cost centers on the setup page too (today on the status page).
- [ ] **E-04** Versions: create, copy with shift + uplift, compare.
- [ ] **E-05** Planning grid (RevoGrid, ADR 0006) for one planning unit at a time (T-25), with a unit switcher: edit, paste, fill-drag, subtotals, keyboard flow, percentage mode, and cells editable as the entry form allows. Undo of the last change batch; copy with row and column headers.
- [ ] **E-06** Top-down spreading from totals (proportional / equal / profile) and spread profiles.
- [ ] **E-07** Workflow: submit → approve → lock per version × cost center, with database-enforced locks, and the status page rolled up by department and hierarchy node (T-20). The controller's publish action (T-21).
- [ ] **E-08** Change log and cell comments.
- [ ] **E-09** OIDC and LDAPS login (T-22), mapping an identity to a user by login or e-mail, and roles from directory groups. Users, roles, scopes, responsibilities and the development login are built (ADR 0009).
- [ ] **E-10** Publishing ([ADR 0011](docs/adr/0011-publishing.md)). Built: downloads, preview, run, run history, republish, [docs/output.md](docs/output.md). Still open:
    - [ ] Export sign rules (E-12): until then values are published natural-positive.
    - [ ] Sales planning's output table, with its module.
    - [ ] A link from the setup page to publishing per round.
- [ ] **E-13** UI/UX design phase: with the main flows on screen (setup, plan, approve, publish), iterate on the frontend while the backend stays mostly as it is. It starts from the setup page's design tokens (`frontend/src/setup.css`). Known: in the planner preview the December column is cut off on narrower screens. Covers: navigation, visual language, the planning grid's look and keyboard flow, empty and error states, the status and setup pages. Done with real users' walkthroughs where possible (R-4).
- [ ] **E-11** Self-host packaging: container image on GHCR, the DBA install script and checklist (D-25), upgrade/migration procedure, the scheduled backup and restore (D-30), install docs.

### Cost center accounting (after v1)

German terms in parentheses, as controllers know them.

- [ ] **K-1 Productive cost centers and activity output (Leistungen).** A productive cost center outputs activity and is credited for it. Its sheet shows the credits at the top, as negative values while costs are positive, then a total cost line, and at the end the coverage difference (Deckungsdifferenz): costs plus credits. Activity types (Leistungsarten): directly productive (credited to COGS), indirect (e.g. for internal R&D projects), machine hours, personnel hours. Fits T-7: credits are stored natural-positive with their own account type, and shown negative.
- [ ] **K-2 Capacity planning (Auslastungsplanung)** (optional). Per productive cost center, its work centers are listed and their hours planned; hours × the cost center's hourly rate per activity type give the activity output, written to the cost center (K-1). A cost center driven by manual labour plans the hours the team works instead.
- [ ] **K-3 Cost forwarding (Leistungsverrechnung).** Service cost centers collect costs and pass them on to consuming cost centers, by percentage shares or by factors (e.g. buildings by the square metres each consumer occupies). Two methods: pass on all costs by the factors; or set a plan rate per unit (e.g. per m²) and pass on only rate × quantity, leaving a difference between costs and costs passed on. There can be multiple units for one cost center with different plan rates.
- [ ] **K-4 Usage factors (Auslastungsfaktoren)** (much later). When demand drops in a later round (e.g. machining_2 at 70% capacity), the activity output scales down, and each cost type gets a variability percentage: electricity scales 1:1 with machine hours, salaries and depreciation don't.
- [ ] **K-5 Capex planning** as a module (T-24): planners add planned investments; later their depreciation feeds the cost center plans.

**Later (not v1):** layers beyond `base`, rolling forecast, price × volume drivers, headcount planning as a module (T-24), simple formulas, non-Postgres adapters, balance sheet, an "attention" list (everything that needs a person: missing submissions, unexplained variances, unrolled forecast, unapproved version), variance commentary thresholds, and a default spread profile per account group.

---

## Suggested order

1. ✓ F-01, F-02, F-03, F-06 (repo skeleton, compose, CI, ADR template).
2. ✓ TD-01…TD-05, TD-07, TD-10, TD-11 (the generator's core).
3. ✓ S-01, S-02, S-03 → D-01, D-02, D-14 closed (ADRs 0001–0003). The spreading engine is the first `domain` module.
4. ✓ S-04 → D-17 closed (RevoGrid). ✓ S-05 → D-10 closed. ✓ S-06 → D-23 closed, D-21/D-22 enforcement decided. D-25 before F-02 is extended for production.
5. ✓ Thin end-to-end slice: `planlayer sync` (one entity's actuals, all members and hierarchies, refusing unresolved codes); `planlayer create-version`; the planning page (RevoGrid, one cost center, prior-year actuals beside plan); per-cell version check with all-or-nothing saves; year totals spread by prior-year actuals; plan changes audited. Not yet in it: login and RLS policies (E-09), locks and workflow (E-07), the mapping page and sign rules (E-01, E-12), editing group rows, adding accounts without prior-year values, undo and copy with headers (E-05), the yearly setup (E-03).
6. ✓ D-04 (ADR 0007), D-22 (ADR 0008).
7. ✓ Rounds and workflow in the store (ADR 0007, ADR 0008): planning rounds with modules, enabled cost centers, entry forms per account group and account, comparison columns, seasonality profiles, sheet and hidden accounts, copy-from-round; workflow status and events, approvers per node; the lock trigger reading round, enabled cost center, status and hidden accounts. The grid returns per row its entry form, spread basis, fallback and editable cells; the planning page renders them, adds accounts and submits; the status page rolls up per node, approves (also in bulk), rejects, reopens, opens and closes the round, and enables cost centers. Not yet in it: the setup page (E-03) and clearing values before hiding an account, who may take which action (D-21), restricting a module to account groups, notifications.
8. ✓ D-21 (ADR 0009) and the development login: users with admin, controller and viewer roles; owners matched by e-mail or login, overrides and deputies on the responsibilities page, approvers per node; forced RLS on the fact table; every request as the logged-in user; status page actions per user ("waiting for me"); users page; TD-09 seed. Not yet in it: OIDC and LDAPS (E-09), roles from directory groups.
9. ✓ D-18, D-19 (ADR 0010), D-12 (ADR 0011).
10. ✓ D-11, D-31 (ADR 0012) and the master-data page: source queries with named columns and preview (admin), sync preview with confirmation of matches and run, the member tree with manual members and local overrides (controller). Parts of E-01 and E-02.
11. ✓ E-03 setup page: new rounds (set up like an earlier one), deadline, starting points Simple / Medium / Full, default entry form and exceptions per account group or account, comparison columns in order, hidden accounts, seasonality profiles; a draft saved all or nothing; beside it a live preview of the planner's sheet with example values, in the design language E-13 starts from. Open items are listed under E-03.
12. ✓ E-10 first cut: CSV and Parquet downloads; preview of rows added, changed and removed per cost center and row; a run that replaces the round's rows in `planlayer_out` and refuses if anything changed since the preview; run history; `planlayer republish`; tables and view documented in [docs/output.md](docs/output.md). Open items are listed under E-10.
13. E-13 UI/UX design phase over the main flows, backend mostly frozen.
14. Remaining ADRs (D-13, D-24, D-25, D-27, D-28, D-29, D-30) and F-items, then epics E-01 → E-05 → E-06 → E-09 → E-11 → the rest.
