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
- [ ] **D-03 Time model.** Closed periods decided in [ADR 0015](docs/adr/0015-forecasts-and-quarters.md): per forecast round, the controller's *actuals through*; rounds are planned by month or by quarter, values stored by month. One fiscal year definition per installation with a configurable start month (T-18). A fiscal year is named after the calendar year in which it starts (`planlayer.domain.periods`); periods are first-of-month dates. Open: period as first day of month vs. a period dimension table (fiscal year, fiscal period, calendar month), and whether day grain is ever needed (the brief says optional; recommendation: month only in v1). **Closed periods:** derived from where actuals exist (Budget & Forecast) or stored as an explicit close status per entity (Konsolidat). Recommendation: explicit, because roll-forecast and the open-for-input periods depend on it.
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
  - **Built (setup page):** create, edit and delete. An edit applies to the next spread and stored months stay; delete is refused while a round uses the profile. A new profile can start from the working days (Monday to Friday) or calendar days of a fiscal year, then be adjusted by hand (ADR 0007).
  - **Built (planning sheet):** a line's own spread. Planners and the controller click a line's badge to choose evenly, prior-year actuals or a profile for that cost center × account; it applies to the year totals typed from then on (ADR 0007).
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
  - what happens to plan values keyed on a member that disappeared (kept and flagged as orphaned, not deleted): members only grow; one the source no longer delivers keeps its values and place and is marked since when ([ADR 0014](docs/adr/0014-preparing-a-round.md));
  - how the sync report shows each kind of change to the controller;
  - whether a new member is enabled for planning automatically or waits for the controller (T-17): decided in [ADR 0007](docs/adr/0007-planning-rounds.md) for running rounds; a new cost center waits for the controller.
- [x] **D-31 Master data in Planlayer's tables.** Decided: [ADR 0012](docs/adr/0012-datasets-and-master-data.md). Master data lives in Planlayer's tables, fed by sync or entered by hand on the same page; manual members the source later delivers are matched and confirmed, their manual data kept as a shadow record; synced members can be renamed and moved locally, and the override survives syncs.
- [ ] **D-27 Sync discipline.** Done in [ADR 0012](docs/adr/0012-datasets-and-master-data.md): resolve everything before writing, list every unknown code, idempotent re-runs, a preview (dry run), confirmation of matches, a sync-run record with actor and counts. [ADR 0014](docs/adr/0014-preparing-a-round.md): actuals replaced only for a window of fiscal years, stored against delivered cells per year in the preview. Open: a flag before changed actuals inside the window's closed months are overwritten, and a source hash per run.

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
- [x] **TD-18 Capacity data** (ADR 0013): four activity types (machine hours, measurement and quality control, repair work, engineering hours), each crediting its own account; 32 work centers on 27 production and engineering cost centers with hourly rates (a few output two activity types); monthly actual hours with the production season (August shutdown) and noise. The credit accounts are booked only by the activity credits (TD-21).
- [x] **TD-22 Cost forwarding in the actuals** (ADR 0018): the group *Internal cost forwarding* (8800 tooling, 8810 buildings, 8820 IT) is booked only by forwarding documents that balance: Tooling passes on its month's balance 40/25/20/15 % to Machining, Assembly Line 1 and 2 and Painting, after the building and IT charges it receives; Facilities charges two buildings by m² (95 and 150 a year); IT charges PCs (1,500 a year) and CAD licences (6,000 a year), leaving the rest on IT.
- [x] **TD-23 Forwardings in a round** (`planlayer seed-dev-forwarding --round CODE`, development only): the same forwardings as the controller would enter them, plus a fixed plant management fee, on accounts `ALOC-<code>` and `ALOC-<code>-CR` in that group; 8800–8820 marked not planned; sources without a plan of their own get last year's actuals as year totals first. Forwardings the round has are left alone.
- [x] **TD-21 Credit postings in the actuals:** the ledger books the activity credits, actual hours × rate on 8900–8930 per work center, credit-side (`kontoart` `L`, flipped to positive by `v_gl_actuals_natural`), so a forecast's closed months show credits (ADR 0016: they come from the ledger only).
- [x] **TD-19 Plan values in progress** (`planlayer seed-dev-plans`, development only): ten cost centers of a round planned by their owners as year totals near last year's actuals, five in progress, three submitted, two approved by their approver. Cost centers that already have plan values or a status are left alone.
- [x] **TD-24 HR data** (ADR 0020): five job families, a personnel list (~460 people at the anchor) with staff types, contract percentages, entry and exit dates (turnover over the years, exits announced up to four months ahead), and month-end FTE and headcount per cost center, staff type and family; gold views `v_job_families`, `v_staff`, `v_workforce_actuals`.
- [x] **TD-25 Positions in a round** (`planlayer seed-dev-workforce --round CODE`, development only): the round's workforce mode set to positions, HR's people taken as positions for every planned cost center, and, entered by the owners, a replacement for every leaver announced after HR's latest month plus six new positions. Cost centers that have plan positions are left alone.
- [ ] **TD-17 Seasonality profiles:** a set covering the edge cases: even; retail (Q4 peak); manufacturing with an August shutdown (a zero month); one entered as percentages summing to exactly 100; one entered as raw unit counts; one derived from prior-year actuals; and invalid ones (percentages summing to 99.9 or 100.1, a negative value, all zeros, 11 values instead of 12).
- [x] **TD-20 Next-year scenario** ([docs/scenarios/next-round-2027.md](docs/scenarios/next-round-2027.md)): a cost center created ahead of the next fiscal year (DE1290), one closed at the end of the current year (DE1100), an account without bookings (6950), and the walkthrough for preparing Budget 2027 from Budget 2026.
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
- [x] **F-10** Confirm CI is green with database tests running as the unprivileged role `planlayer_test` (created by the tests; needs a CI database user that may create roles).

## 6. v1 epics (refined after section 2 is done)

- [ ] **E-01** Connections & sync: configure a source; map each dataset to a view, table or query and its columns to fields (T-16), with validation and preview; sync members and actuals; show a reconciliation report.
    - [x] A running sync shows what it writes and for how long; each run records its duration per step, also in the server log.
    - [ ] Real progress for long syncs (current step, rows written), once a sync can run as a background job (D-24).
    - [x] Actuals and work center actual hours are synced for a window of fiscal years (default: what the setup and open rounds compare with); older years stay. The sync adds the window's filter to the query, on the column mapped to period; any day of a month counts as the month. The preview compares stored and delivered cells per year and warns when a year would be emptied ([ADR 0014](docs/adr/0014-preparing-a-round.md)).
    - [x] Sources show unsaved changes and the filter the sync adds; field types in plain words. Sync picks years from a list with the cells stored per year; changed members show what changed (was → becomes).
    - [x] The system user writes syncs, so an admin without cost centers can sync; the run records who asked.
    - [x] Members no longer delivered keep their place in the hierarchy and are marked since when.
    - [x] Sources grouped by the module that needs them; "Work center actual hours".
    - [ ] The Members page shows *no longer delivered* (`missing_since`) next to each member.
- [ ] **E-12** Setup pages (admin): one place for the settings a new installation needs before planning starts. It covers:
  - **Sign rules (T-7):** how the source delivers signs (natural or ledger) and how the export target expects them. The flip is defined per account type or account group, with per-account overrides. A preview shows sample accounts with their raw source value, the stored value and the exported value.
    - [x] Export side: the *Settings* page (admin) chooses the convention of `amount_signed`, by account role ([ADR 0019](docs/adr/0019-published-signs.md)).
    - [ ] Source side: actuals delivered in ledger signs, converted at sync (today the source view delivers natural signs).
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
    - [x] Accounts not planned replace hidden accounts: shown read only where they have values, counted in totals ([ADR 0014](docs/adr/0014-preparing-a-round.md)). In a round in setup, marking one clears its values in the same save.
    - [ ] In an open round, marking an account not planned (or assigning a set) still refuses while values exist: offer to clear them as an audited change batch.
    - [x] Open periods per round: a forecast's *actuals through* closes the months before it; they show actuals, read only, refused by the store ([ADR 0015](docs/adr/0015-forecasts-and-quarters.md)). Rounds planned by quarter close whole quarters.
    - [x] Rounds planned by quarter: Q1–Q4 on the sheet, a quarter spreads over its months by the line's basis (ADR 0015).
    - [x] *Refresh actuals* on a forecast's setup: the actuals sync for its fiscal year, with the sync's preview.
    - [x] The capacity sheet follows the round ([ADR 0016](docs/adr/0016-capacity-in-rounds-and-forecasts.md)): prepared by the controller in setup, actual hours in a forecast's closed months, a full-year total, quarters, the round's comparison columns.
    - [x] A walkthrough of `docs/scenarios/forecast-2026.md` with the user (quarter run; publish not yet walked).
    - [ ] Setup page redesign: in quarter grain, the entry-form defaults in step 3 speak of months ("Monthly" form, "planners type each month"): grey them out or present them as quarters.
    - [x] Account sets per round, assigned to cost center nodes or cost centers (nearest wins): sheets list the set's accounts, "add account" offers only them, the store refuses values outside ([ADR 0014](docs/adr/0014-preparing-a-round.md)).
    - [x] The setup page's *Cost centers* tab: planned, account set, productive, prior-year actuals and the source round's plan per cost center; new and no longer delivered marked.
    - [x] New rounds carry over by a checklist from a chosen round (default: the latest of the same kind), plan values optionally with an uplift, as one change batch; controllers prepare a round in setup without moving its workflow status.
    - [ ] Uplift per account group (today one percentage for all carried values).
    - [x] Carry planned hours (capacity) with the values: no uplift, credits recomputed from the new round's rates (ADR 0016).
    - [ ] Planned hours in the file downloads of the Publish page (today: amounts only; hours go to `fact_plan_capacity`).
    - [x] A line whose reference has no values reads "even" with no warning; its tooltip says why.
    - [x] Creating a round is a step of its own; the round then shows how it was created (from which round, by whom, what it carried, plan values with uplift and cell count).
    - [x] A note per comparison column, shown to planners as the column's tooltip; actuals columns say how far their actuals go when the year is partial.
    - [x] A comparison column of actuals for the year to date plus the forecast for the rest: a forecast compared as a version shows its full year (ADR 0015).
    - [ ] An account set's own accounts and groups could be edited from the cost center tab too; today the Accounts tab holds the matrix.
- [ ] **E-04** Versions: create, copy with shift + uplift, compare.
- [ ] **E-05** Planning grid (RevoGrid, ADR 0006) for one planning unit at a time (T-25), with a unit switcher: edit, paste, fill-drag, subtotals, keyboard flow, percentage mode, and cells editable as the entry form allows. Built: editing, paste from Excel and LibreOffice (the trailing newline they copy is dropped, so it never blanks the cell below), one copied value pasted over a selection fills it, fill-drag, subtotals, keyboard flow (E-13).
    - [x] Undo and redo of change batches, several steps per visit to a sheet, refused when someone else changed a cell since ([ADR 0010](docs/adr/0010-grid-api-and-concurrency.md)).
    - [x] A line's own spread, chosen from the line's badge (ADR 0007).
    - [ ] Copy with row and column headers.
    - [ ] A full-year view of a cost center: one value per account, no months, for planners who plan year totals only.
    - [ ] Reach the spread menu from the keyboard: the badge sits inside the grid, which keeps Tab to itself.
    - [ ] Decide whether a new round copies line spreads from the round it is set up like (today it starts without them, like sheet accounts).
- [ ] **E-06** Top-down spreading from totals (proportional / equal / profile) and spread profiles.
- [ ] **E-07** Workflow: submit → approve → lock per version × cost center, with database-enforced locks, and the status page rolled up by department and hierarchy node (T-20). The controller's publish action (T-21).
- [ ] **E-08** Change log and cell comments.
    - [ ] Mark cells with comments by a corner triangle, as spreadsheets do (a CSS class per cell through `cellProperties`); the same mark can flag cells someone else changed since the sheet was loaded.
- [ ] **E-09** OIDC and LDAPS login (T-22), mapping an identity to a user by login or e-mail, and roles from directory groups. Users, roles, scopes, responsibilities and the development login are built (ADR 0009).
- [ ] **E-10** Publishing ([ADR 0011](docs/adr/0011-publishing.md)). Built: downloads, preview, run, run history, republish, [docs/output.md](docs/output.md). Still open:
    - [x] Published signs: `amount_signed` by account role with an installation-wide convention (ledger or result), and `dim_account.role` ([ADR 0019](docs/adr/0019-published-signs.md)).
    - [ ] Sign overrides per account group or account, if a customer needs them.
    - [ ] Sales planning's output table, with its module.
    - [x] Setup and publishing per round: both follow the app's current round (E-13), one click apart in the sidebar.
- [ ] **E-13** UI/UX design phase: with the main flows on screen (setup, plan, approve, publish), iterate on the frontend while the backend stays mostly as it is. Covers: navigation, visual language, the planning grid's look and keyboard flow, empty and error states, the status and setup pages. Done with real users' walkthroughs where possible (R-4). The look is the setup page's, carried app-wide, light only (tokens in `frontend/src/styles/theme.css`, so a dark palette stays a change in one place).
    - [x] App shell: sidebar grouped by role (Plan; Round; Admin), collapsible and collapsed by default below 1360 px; top bar with the current round and the user; login page; "server not reachable" and "no round yet" states.
    - [x] One current round for Planning, Status, Setup and Publish, chosen in the top bar and remembered per browser.
    - [x] Planner preview fits beside the settings, all twelve months included: example values in thousands, short form badges when narrow.
    - [x] One dropdown for the whole app (`Select.tsx`): a styled list instead of the browser's popup, with search for long lists, status dots, hints and indented trees; keyboard as in a native select. No native select remains.
    - [x] Planning grid in the preview's look, roomy rows: typed-in cells as inputs, calculated cells tinted, year-only months hatched, comparisons muted, entry-form badge per row with ⚠ for the fallback; the cost center switcher as the page title; empty states for a sheet without accounts and for a user without cost centers.
    - [x] The grid's keyboard flow checked against Excel habits: typing over a cell edits it, Enter saves and moves down, Tab saves and moves right, Escape cancels, Delete blanks (not 0); calculated cells open no editor.
    - [x] Status page: counts per status, progress, filters with counts, a status bar per hierarchy node, actions per row, comments for reject and reopen.
    - [x] Master data page: sources as cards with column pickers, the sync preview as tiles, the member tree with a filter and inline parent pickers.
    - [x] Responsibilities and Users pages: planner overrides, deputies and approvers as chips; roles as switches.
    - [ ] Number display for amounts: a choice of how plan and comparison amounts are shown, in the grid, the status and publish pages and the setup preview.
        - Scales: full, in thousands, in millions; and decimals shown or hidden. Display only: values are still entered, stored and published exactly (T-12), and a typed value means the unit shown, or the full amount (to decide).
        - Applies to amounts only; not to percentages (D-05), factors, quantities, or headcount in later modules, which keep their own format.
        - Regional settings for display and entry: thousands and decimal separator (e.g. 1,234.56 / 1.234,56 / 1'234.56), so paste and typing follow the same convention.
        - To decide: an installation default (setup page, E-12) with a per-user override, and where the choice sits on the planning page.
    - [ ] Real users' walkthroughs (R-4).
- [ ] **E-11** Self-host packaging: container image on GHCR, the DBA install script and checklist (D-25), upgrade/migration procedure, the scheduled backup and restore (D-30), install docs.
    - [ ] A guided installation in the app for the admin: connections, the first sync, the first users and roles (an HR user among them), and the encryption key for pay (W-18), generated in the app and shown once for HR to keep in a password manager, with a check that it was saved before going on.

### Cost center accounting (after v1)

German terms in parentheses, as controllers know them.

- [ ] **K-1 Productive cost centers and activity output (Leistungen).** The controller marks a cost center as productive; that unlocks capacity planning (K-2) for it. A productive cost center outputs activity and is credited for it. Its sheet shows the credits at the top, as negative values while costs are positive, then a total cost line, and at the end the coverage difference (Deckungsdifferenz): costs plus credits. Activity types (Leistungsarten): directly productive (credited to COGS), indirect (e.g. for internal R&D projects), machine hours, personnel hours. Fits T-7: credits are stored natural-positive with their own account type, and shown negative.
  - [x] Productive cost centers per round, marked on the setup page and copied with the round ([ADR 0013](docs/adr/0013-capacity-planning.md)).
  - [x] Activity types with the account they credit, from the source or entered on the master-data page.
  - [x] Credits stored on the cost center and shown there read only, marked "From capacity".
  - [ ] Credits shown negative at the top of the sheet, a total cost line, and the coverage difference at the end.
  - [ ] Kinds of activity types (directly productive to COGS, indirect, personnel hours), if they need different handling.
- [ ] **K-2 Capacity planning (Auslastungsplanung)** (optional). Per productive cost center, its work centers are listed and their hours planned; hours × the cost center's hourly rate per activity type give the activity output, written to the cost center (K-1). A cost center driven by manual labour plans the hours the team works instead.
  - [x] Source datasets (ADR 0012) for work centers per cost center, their rates per activity type, and actual hours (the comparison and spread reference for planned hours); work centers editable on the master-data page ([ADR 0013](docs/adr/0013-capacity-planning.md)).
  - [x] Its own page in the sidebar, before cost center planning; both pages keep the chosen cost center.
  - [x] The cost center planning sheet of a productive cost center shows what capacity planning produces (the credits of K-1).
  - [x] Plan rates per round on the sheet; lines inactive per round (controller); new work centers added by planners on the sheet; year totals spread like last year's hours, evenly or by a profile.
  - [x] Capacity planners: a responsibility on cost centers or nodes, enforced by row-level security.
  - [x] Undo and redo on the capacity sheet (as on the cost sheet, ADR 0010), hours and line changes alike.
  - [x] Lines of work centers added by hand spread evenly by default.
  - [x] Planned hours in the published output: `fact_plan_capacity`, with work center and activity type dimensions ([ADR 0016](docs/adr/0016-capacity-in-rounds-and-forecasts.md)).
  - [x] The capacity sheet follows the round: setup preparation, carried hours, forecasts, quarters, comparison columns (ADR 0016).
  - [ ] The variable rate component: UI for a second rate, credited to the activity type's variable account (the tables hold it).
  - [ ] Rates entered on the master-data page, for installations without a rates source (today a line without a source rate is added on the sheet with its rate).
  - [ ] Clearing a cost center's capacity before it stops being productive (today the setup save is refused and names it).
  - [ ] Reaching a line's spread menu from the keyboard (as on the cost sheet, E-05).
- [ ] **K-3 Cost forwarding (Leistungsverrechnung).** Service cost centers collect costs and pass them on to consuming cost centers, by percentage shares or by factors (e.g. buildings by the square metres each consumer occupies). Built after K-2 on the same approach (source data, its own page, effect on the sheet). Two methods: pass on all costs by the factors; or set a plan rate per unit (e.g. per m²) and pass on only rate × quantity, leaving a difference between costs and costs passed on. There can be multiple units for one cost center with different plan rates.
  - [x] Forwardings per round: all costs (what is left on the source) by percentages or portions, a fixed amount, or rate × quantity; own charge and credit accounts in a group the controller picks; the *Forwarding* page; charges and credits read only on both sides' sheets, credits shown negative; recomputed on save and, for full forwarding, on every change of the source; carried with a new round ([ADR 0018](docs/adr/0018-cost-forwarding.md)).
  - [ ] Factor quantities from a source dataset (e.g. m² per cost center from facility management), and from the workforce plan (W-6).
  - [ ] Quantities that vary by month (today a year quantity, a twelfth a month).
  - [ ] A change log and undo for forwarding definitions (today `changed_by` / `changed_at` only; the values they write are audited and in the change log).
  - [ ] Publishing: warn when a full forwarding's source is still being planned, so the published charges may move.
  - [ ] Reciprocal forwarding (A to B and B to A by shares), solved iteratively; today a receiver by shares passes nothing on.
  - [x] A cost center no longer planned in the round leaves its forwardings: the setup save removes its charges and credits first and recomputes the rest; planned again, it gets them back.
  - [ ] Planners see a forwarding's other side in more detail (e.g. the receivers' list from the source's credit line).
- [ ] **K-4 Usage factors (Auslastungsfaktoren)** (much later). When demand drops in a later round (e.g. machining_2 at 70% capacity), the activity output scales down, and each cost type gets a variability percentage: electricity scales 1:1 with machine hours, salaries and depreciation don't.
- [ ] **K-5 Capex planning** as a module (T-24): planners add planned investments; later their depreciation feeds the cost center plans.

### Workforce planning

FTE and headcount per cost center, staff type and category, as month-end levels, typed or rolled up from positions ([ADR 0020](docs/adr/0020-workforce-planning.md)).

- [x] **W-1 Module and modes:** `workforce` in the round with one mode (FTE, headcount, both, positions), chosen in setup; a mode change clears the round's workforce in setup and is refused in an open round with values; carried with a new round (positions still there in the new year, lines; typed values with *Plan values*).
- [x] **W-2 Sheet:** the *Workforce* page (Plan, after Capacity): lines per staff type × category, year and quarters as their last month, a typed year or quarter filling its months, comparison columns, a forecast's closed months as HR's actuals, undo; same status, rights, locks and audit as the cost center's other sheets.
- [x] **W-3 Positions:** title, name, a number (HR's, or Planlayer's `N00001` …), type, category, entry and leave date, contract % (optional for external types), plan position and replacement flags, description; month-end snapshot roll-up stored in `fact_workforce`; HR's number, name, entry date and flags kept, removing them for controllers only; the build-up (heads at the year's start and end, entering, new, replacements, leaving per month); *Add staff from HR* per cost center and for the round in setup; a log of every change.
- [x] **W-4 Data:** staff types (seeded, editable, *external* flag) and categories as master data; datasets `staff_types`, `staff_categories`, `staff`, `workforce_actuals`.
- [x] **W-5 Publishing:** `fact_plan_workforce`, `dim_staff_type`, `dim_staff_category`, `v_plan_workforce`, no names.
- [ ] **W-6 Factors from the workforce:** a category carries per-head factors (e.g. one IT workplace per office worker, a CAD licence per engineer) that feed cost forwarding's quantities (K-3) by month.
- [x] **W-7 Personnel costs from positions** ([ADR 0022](docs/adr/0022-personnel-costs.md)): monthly salary per position (HR's kept apart from the planned one), pro rata by days from entry to leave, typed amounts for staff types such as freelancers or hourly paid; yearly extras as salaries per year or % of base pay, spread over the year; raises from a month on their own account, also after approval; long-service awards by years since entry; a yearly bonus spread over the months worked; contributions per person and month up to a ceiling; rules per round, optionally per entity, carried with a new round; totals written to the personnel accounts of the cost center charged, read only on the sheet.
  - [ ] Rules limited to staff categories (e.g. a raise for staff under a collective agreement only).
  - [ ] Contributions on typed amounts (people paid by the hour are employees; today a typed amount bears nothing).
  - [ ] Salary bands per category as the starting salary of a plan position (today it starts at 0).
  - [ ] A transfer during the year splits the yearly bonus by months (today it stays with the sending half), and the receiving half's long-service award counts from the person's first entry, not the transfer.
  - [ ] Undo on the pay of positions (the changes are in `position_pay_log` with the change batch).
  - [ ] Paste of salaries from Excel into the pay table.
  - [ ] **HR's own salary upload,** next to the admin's `staff` dataset: HR downloads a template (number, name, cost center, monthly salary; CSV, Excel or Parquet), fills it, and uploads it on the Personnel costs page, without an admin. The upload is matched to the positions by number; a preview shows rows matched, salaries changed, and rows that match no position or whose cost center differs, before anything is written. Decide whether an upload sets HR's salary or the planned one, and how it relates to a synced `monthly_salary`.
- [ ] **W-8 Modes per cost center:** a round default with exceptions on the *Cost centers* tab (e.g. positions for the larger departments only).
- [ ] **W-9 A replacement names the leaver** it replaces (today a free description), so the build-up can show open gaps: leavers without a replacement.
- [ ] **W-10 Contract changes during the year** (e.g. 100 % to 80 % from September) and long absences (counted in headcount, not in FTE).
- [ ] **W-11 Undo on the positions list** (today undo covers typed values; positions are logged in `workforce_position_log` with the change batch).
- [ ] **W-12 Positions in the file downloads** of the Publish page are left out on purpose; decide whether a controller may download them with names. Pay is never in them (ADR 0022).
- [ ] **W-13 Status page:** FTE and headcount per node next to the plan status, and a comparison with last year.
- [ ] **W-14 Paste of a position list** from Excel into the positions table.
- [ ] **W-15 HR changes after taking:** a round holds HR's list as it was taken. Show where a position differs from HR's latest sync (a new leave date, another cost center, someone new) and offer to apply it.
- [ ] **W-17 Moving a person to another cost center** from a date, as one step on a position.
  - [x] Proposed by the sending cost center, accepted or declined by the receiving one (or a controller) as a task on *Home*; nothing moves until accepted. A whole position moves with its year; a transfer during the year ends the original the day before the chosen month (marked *transferred to*) and gives the receiving cost center the person from the first of it, with the same number. The build-up names *moving in* and *moving out* ([ADR 0021](docs/adr/0021-position-transfers-and-home.md)).
  - [ ] Undo an accepted transfer (restore the original), and change its date on either side.
  - [ ] W-15 offers "apply as a transfer from <date>" when HR's sync shows a position under another cost center; personnel costs (W-7) split at the date.
  - [ ] E-mail the receiving side when a transfer is proposed, and the sender when it is decided.
- [x] **W-18 Confidential personnel data** ([ADR 0022](docs/adr/0022-personnel-costs.md)): the HR role; pay read by HR and by the owner the source names (not deputies, approvers, viewers, controllers or an owner set by override), enforced by row-level security through `pay_access`, logs included; small groups (fewer positions on a cost center and personnel account than the round's minimum, default 3, set by HR) hidden from other readers, listed for HR on *Home*, and blocking closing and publishing until HR charges the positions elsewhere; HR's review before approval; totals only in `planlayer_out`.
  - [ ] Encryption at rest of pay columns (`pgcrypto`, key outside the database) against DBAs, backups and dumps; the key is set up in the guided installation (E-11), and the docs tell HR to keep it in a password manager.
  - [ ] HR grants or withholds pay rights on a cost center (e.g. HR alone plans a sensitive one, or delegates it).
  - [ ] A log of role changes (who gave whom the HR role), shown to HR.
  - [ ] Actuals on the personnel accounts of a small cost center come from the ledger as they are; decide whether to mask them like plan values.
  - [ ] The admin's source preview of the `staff` dataset shows `monthly_salary`; hide pay columns in the preview.
  - [ ] Free text (descriptions, notes, item lists) can still carry pay whatever the rules say: say so in the user docs.
- [ ] **W-16 Number prefix:** Planlayer's position numbers start with `N`; if a customer's HR numbers do too, make the prefix a setting.

### MCP access (after v1)

An MCP (Model Context Protocol) server, so a user can connect the LLM client of their choice (e.g. Claude, ChatGPT, a local model) and ask it to work with Planlayer for them. The REST API stays the app's API; MCP tools are thin wrappers over the same domain functions, run in `unit_of_work` as the token's user, so RLS, locks, the lock trigger, audit and change batches apply exactly as in the UI. MCP is another client of the same rules, never a way around them.

- [ ] **M-1 Decision (ADR): transport, hosting and login.**
  - **Transport:** Streamable HTTP at `/mcp` in the same container, built on the official Python MCP SDK (pinned). No separate service; stdio isn't needed for a server people reach over the network.
  - **Login, two stages:** (a) personal access tokens, which a user creates on a page of their own: read-only or read-write, with an expiry, revocable, stored hashed like sessions (ADR 0009); (b) OAuth 2.1 as the MCP authorization spec describes, with Planlayer as the resource server and the company identity provider from E-09 as the authorization server.
  - **Switch:** an admin can turn MCP access off for the installation.
- [ ] **M-2 Read tools.**
  - **My to-dos:** cost centers I still have to plan or submit, approvals waiting for me, deadlines, and what is overdue. The same facts as the status page's filters.
  - **A sheet:** a cost center's accounts × months with plan, comparisons and group subtotals.
  - **An account across my cost centers.**
  - **Rounds:** status and deadline.
- [ ] **M-3 Options per line:** each line's entry form and what may be entered: months, or a year total that spreads, and by what basis. Taken from the grid's row facts; the server decides, as for the UI.
- [ ] **M-4 Write tools:** plan values by month or as a year total.
  - **Conflicts:** the conflict rule of ADR 0010 holds. A write names the cell versions it read, or is refused when the cells changed since.
  - **Batches:** one tool call is one change batch, so it can be undone, and an undo tool takes back the last batch made through MCP.
  - **Marking:** write tools are marked as changing data, so clients ask the user before calling them.
- [ ] **M-5 Plan PDF tool:** request the PDF of my cost center(s) (P-3); the result carries the file or a link to it.
- [ ] **M-6 Safety.**
  - Values and names returned to the model are data, never instructions (the same rule as for source data).
  - Read-only tokens; rate limits.
  - The change log records that a batch came through MCP.
  - The admin sees which tokens exist and can revoke them.
  - To decide: whether workflow actions (submit, approve, reject) are offered as tools at all.
  - **No pay through MCP:** personnel costs (ADR 0022) are left out entirely, as a precaution against data leaks: no tools read or write pay per position, HR's salaries, reviews or rules, whatever the token's user may read in the UI. Totals on personnel accounts reach a sheet tool like any other plan value, with small groups hidden as in the UI.
- [ ] **M-7 Docs:** connecting common MCP clients, the tools and what each may do, and the data-protection note that the plan data a user reads goes to the LLM provider they connected.
- [ ] **M-8 Tests:** tools against the test database as the unprivileged role, plus a check with the MCP Inspector.

### Plan reports (after v1)

- [ ] **P-1 Decision (ADR): rendering and storage.**
  - **Rendering:** an HTML/CSS template rendered to PDF in the container (e.g. WeasyPrint: reuses the design tokens, needs Pango in the image), against a drawing library (ReportLab) or Typst. Fonts are bundled (self-hosted, no CDN).
  - **Storage:** generated PDFs in the app store or on a volume, with retention.
- [ ] **P-2 The cost center report.**
  - **Header:** round, cost center, owner, status, approver and approval time.
  - **Body:** accounts × months and year with the round's comparison columns and group subtotals; each line's entry form.
  - **Later:** comments (E-08).
  - **Summary report:** per hierarchy node and for the round, with the status overview.
- [ ] **P-3 On demand:** a PDF for one cost center or a selection, from the planning and status pages and through MCP (M-5); planners get their own, controllers and viewers any they may read.
- [ ] **P-4 On round close:** closing a round generates a set (one PDF per enabled cost center plus the round summary) and keeps it with the round. Reopening and closing again makes a new set; earlier sets stay. Runs as a background job (D-24).
- [ ] **P-5 Number display** follows the installation's setting (E-13).
- [ ] **P-6 Tests:** the report's text content checked against a fixed round (golden), and the close trigger generating exactly one set.

### Line notes

Notes on a line of a sheet (cost center × account, or work center × activity type on the capacity sheet), for the whole fiscal year rather than a month. They hold to-dos and explanations of why a value is what it is, and carry forward from round to round while open. Built: [ADR 0017](docs/adr/0017-notes-and-item-lists.md).

- [x] **N-1 Decision (ADR): the note model.** Threads keyed by line, remembering the round last written in; everyone who reads the line writes notes (RLS through `cost_center_access` / `capacity_access`); audited in `annotation_log`. E-08's cell comments are not built: a note belongs to the line.
- [x] **N-2 Writing and resolving:** threads with replies; the line's marker shows the open count; resolve and reopen record who and when.
- [x] **N-3 Mentions:** `@` a user who reads the line; a mention can assign the thread as a task. *My notes* lists tasks, mentions and own threads; the sidebar counts open tasks.
- [x] **N-4 Carried forward:** open threads show in later rounds with the earlier round's name as a pill; writing to them makes them the current round's. Resolved threads stay in their round.
- [x] **N-5 Tests:** RLS, mentions limited to readers, tasks, carry-forward, gridnote rights, capacity lines (`tests/test_notes.py`).
- [x] **N-6 Gridnote:** one per line, rows of a text and an amount with their sum; *Insert total* writes it as the line's year value.
- [ ] Notes on account groups and hierarchy nodes (with a marker once rows can collapse).
- [ ] E-mail notifications for mentions and tasks.
- [ ] Notes in MCP's to-dos (M-2).

### Item lists

The items behind a line's value, kept outside rounds: invoices, licences, contracts. Built: [ADR 0017](docs/adr/0017-notes-and-item-lists.md).

- [x] **L-1 Decision (ADR): lists.** One list per cost center × account; items are quantity × unit price (quantity 1 by default), computed by the database; visibility private / cost center / public, changed only by the owner. The total reaches the plan only by *Save & apply total*; the marker warns when they differ.
- [x] **L-3 Editing:** a large overlay from the line's marker; *My item lists* lists every list the user can see.
- [x] **L-5 Tests:** totals, visibility and rights (`tests/test_notes.py`).
- [ ] **L-2 Setup:** item lists as an entry form, where the line's year value always equals the list total (allowed per account group in the yearly setup).
- [ ] **L-4 History:** snapshots of a list per submitted round, and comparing two states item by item.
- [ ] Optional fields per item: a renewal date or contract end.
- [ ] Starting a list from *My item lists* (today from the line on the sheet).

### Sales Module

Should support both Sales as well as Order intake planning, using the same methodology.
Has additional dimension **order type** which can be different types of products or services, provided by different departments and having different margins. Examples include sales of machines, spare parts, inhouse repairs, onsite service work, pefurbishment.
Sales output could optionally be used to feed into Income Statement, or management p&l/segment reporting (controller defines account to be mapped)

### Order Backlog and Opportunities Module

An optional module, fed by its own data source from the erp/crm. Concrete order backlog with customers, their countries, planned sales dates and values. Sales opportunities with customers, expected closing date, and probability. Both form the basis of the sales planning. Optional plan orders can be added, or the sums can be fed into the sales planning grid, where they get modified. A tooltip could show the original value from the OBOM, a conditional coloring could show where the plan value is below OBOM-level.

### User documentation

What the user docs (controllers, planners, admins) must say. Collected here as it comes up; written with E-11's install docs.

- [ ] **Pasting columns in Firefox.** A column copied from a spreadsheet fills down the cells it is pasted into (receivers' quantities on the forwarding page; the planning grid). Firefox with `dom.event.clipboardevents.enabled = false` in `about:config`, as privacy-hardened profiles set it (arkenfox, LibreWolf), gives pages no paste events: the forwarding table still fills down by splitting what lands in the cell, but a paste can't be told apart from other input elsewhere. Chromium-based browsers paste as expected. Say which browsers are tested and that this setting must stay at its default for full paste support.
- [ ] **Cost forwarding for controllers:** the three methods; a receiver by shares passes nothing on; the `ALOC-` accounts and putting the ERP's actual forwarding account in the same group, marked not planned; what planners see. Based on [docs/scenarios/cost-forwarding-2026.md](docs/scenarios/cost-forwarding-2026.md).

### Various

**Later (not v1):** layers beyond `base`, rolling forecast, price × volume drivers, simple formulas, non-Postgres adapters, balance sheet, an "attention" list (everything that needs a person: missing submissions, unexplained variances, unrolled forecast, unapproved version), variance commentary thresholds, and a default spread profile per account group.

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
15. Building out cost center planning: ✓ seasonality profiles (edit, delete, working-day and calendar-day presets), ✓ a line's own spread on the sheet, ✓ first cut of K-1 and K-2 (productive cost centers, capacity planning, [ADR 0013](docs/adr/0013-capacity-planning.md)); open items under K-1 and K-2; ✓ first cut of K-3 (cost forwarding, [ADR 0018](docs/adr/0018-cost-forwarding.md)); open items under K-1, K-2 and K-3.
16. ✓ First cut of workforce planning ([ADR 0020](docs/adr/0020-workforce-planning.md)): FTE, headcount or positions per round; open items under W-6 to W-17.
17. ✓ Transfers of positions between cost centers and the *Home* start page ([ADR 0021](docs/adr/0021-position-transfers-and-home.md)).
