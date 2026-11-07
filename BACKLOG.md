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
| T-9 | **Frontend: React + TypeScript + Vite.** The grid component (Glide Data Grid or RevoGrid) is still chosen by S-04. | Glide Data Grid is React-only and RevoGrid has a React wrapper, so React fits both candidates. It also has the largest contributor pool. |
| T-10 | **Backend stack:** FastAPI + Pydantic v2, SQLAlchemy 2.x, Alembic, psycopg 3 with a connection pool, uv, pytest. Endpoints are **synchronous**. **SQLAlchemy Core** for fact tables, grid queries, spreading and adapters; the **ORM** only for setup and config tables. | Sync code is simpler to write, test and debug, and a planning app's load doesn't need async. Bulk writes and generated `dim_*` columns (D-01) are awkward through the ORM. |
| T-11 | **Layering:** calculation rules (spreading, rounding, sign conversion, fiscal periods, percentage mode) live in a pure-Python `domain` package with no I/O. Rules that must never be bypassed (locks, audit, unique fact grain) are enforced in the database. The server computes spreads; the grid shows the result. | The rule-heavy code stays easy to test in isolation, and a bug in the API can't write to a locked version or skip the audit. A single implementation of spreading means totals tie exactly. |
| T-12 | **Amounts are `numeric` in the database, `Decimal` in Python, and strings in the API.** JavaScript numbers are used for display only. | Floating point can't represent cents exactly; strings carry the exact value across JSON. |
| T-13 | **CI from the first commit of code:** GitHub Actions running ruff, mypy, pytest against a real Postgres service, and the frontend lint and typecheck. | Invariants enforced in the database (T-11) can only be tested against a real Postgres. |
| T-14 | **Planlayer runs as one container against the customer's existing Postgres, in two schemas of its own, reached through up to three connections.** <ul><li>**App store:** `planlayer_app` holds everything the app works with: config, members and actuals synced from the source, plan values in every status, workflow, audit and comments.</li><li>**Source:** the customer's contract views (D-11), read only.</li><li>**Output:** `planlayer_out` holds the published plan data (approved or locked versions) as BI-readable tables and views.</li></ul>Each connection may point at the same database or a different one. The typical warehouse setup is `planlayer_app` in its own `planlayer` database, with the source views and `planlayer_out` in the `dwh` database. **No query joins across connections:** the source is synced into the app store (D-09), and the output is written by publishing (D-12). Schema names are configurable. Planlayer ships a **scheduled backup of `planlayer_app`**. `planlayer_out` is derived, and is rebuilt by republishing. | Nothing extra to operate: one container and its connection strings. `planlayer_out` sits next to bronze/silver/gold, so BI tools and dbt read plan data like any other warehouse table. Plan data is primary data that can't be rebuilt from the ERP, so the app store gets its own backup even when the warehouse backups are weak. |

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
- [ ] **D-01 ⚙ Fact table dimension encoding.** Options: (a) generic `member_key`, (b) one FK column per dimension, fixed in the schema, (c) columns generated from a dimension registry plus migrations (the Konsolidat pattern). Criteria: pivot query speed at 1M/10M rows, RLS expressibility, how readable it is for BI tools, cost of adding a dimension. Konsolidat runs (c) in production (`dim_*` columns generated from config, added but never auto-dropped), which makes it the leading candidate. *Spike S-01.*
- [ ] **D-02 ⚙ Hierarchy storage and roll-up.** Parent–child only (recursive CTE) vs. a precomputed closure table / materialized paths vs. level columns. Also decide whether **alternate hierarchies** (same members, different roll-up) are in v1. Konsolidat's model is a reference: named hierarchies per dimension, an explicit `is_group` flag (input on leaves only), and a closure table built by recursive CTE. *Spike S-02.*
- [ ] **D-03 Time model.** Period as first day of month vs. a period dimension table. Fiscal years that don't start in January. Whether day grain is ever needed (the brief says optional; recommendation: month only in v1). **Closed periods:** derived from where actuals exist (Budget & Forecast) or stored as an explicit close status per entity (Konsolidat). Recommendation: explicit, because roll-forecast and the open-for-input periods depend on it.
- [ ] **D-04 Planning area definition.** What an admin configures: dimensions in scope, plan grain level per dimension, the periods and versions open for input, and which actuals versions are shown alongside. This is the central config object, so its shape should be settled early.
- [ ] **D-05 Percentage-mode cells.** Storage: `input_type` + `ref_line` + stored percentage, with the amount derived on write vs. on read. Recalculation order when the reference line changes. Only one level of reference in v1.
- [ ] **D-06 Measures and units.** Amount only, or also quantity/price (needed for sales: volume × price is the obvious next step). Recommendation: store `measure` as a dimension from day one, even if v1 has only amount and quantity.
- [x] **D-07 Currency handling.** Decided: T-6 (group currency only).
- [ ] **D-08 Layers in v1 or later.** The brief puts them in "later". Decide whether `layer_id` is in the fact primary key from day one (cheap now, expensive to add later) even if only `base` is used. Recommendation: yes. Konsolidat's layers (each role edits its own layer, effective = sum, no stored final row) are the cleanest model for top-down adjustments.
- [x] **D-26 Sign convention.** Decided: T-7 (natural-positive, converted at sync and export, configured in E-12, checked by F-07).
- [ ] **D-28 Spread basis catalogue.** What a planner can spread a total by, on two axes: **time** (year/quarter → months) and **hierarchy** (group node → leaves). Candidate bases: equal; a **seasonality profile** (see below); in proportion to **existing values in the target**; in proportion to a **reference** (prior-year actuals, prior-year plan, another version, chosen per spread); and copy the reference + uplift %. Also define the fallbacks: what happens when the reference is all zero or missing for some leaves (fall back to equal, block, or ask), how negatives in the reference are handled, and the rounding rule (exact tie). Neither reference project spreads by reference data, so this is a differentiator.
  **Seasonality profiles** are named, user-definable sets of values covering the fiscal year (one per month):
  - **Entry:** either as percentages (validated to sum to 100%) or as any relative numbers (e.g. units sold per month, or 8/8/10/…), which Planlayer normalises to shares. The profile stores what was entered plus its input type, so it can be shown and edited as typed. The normalised shares are derived.
  - **Values:** zero is allowed (e.g. a factory shutdown in August); negative values are not. A profile of all zeros is invalid.
  - **Derived from history:** a profile can be created from a reference ("prior-year actuals of cost center X", "average of the last 3 years") and saved as a fixed, named profile, so it no longer moves when actuals are restated.
  - **Open questions:**
    - **Scope:** are profiles global, per planning area, or both?
    - **Defaults:** can a default profile be attached to an account group or a dimension member (e.g. revenue lines use "Sales seasonality")?
    - **Sub-ranges:** when spreading over only part of the year (e.g. Apr–Dec), are the profile's shares for those months re-normalised? Recommendation: yes.
- [ ] **D-29 Entry modes for v1.** Which ways of entering data the grid supports: typing in month cells; typing a **year (or quarter) total** that spreads immediately and visibly using D-28; **paste** of a block from Excel/LibreOffice; fill-drag; and a **spread dialog** on a selected range or total ("spread 1.2M over Jan–Dec by prior-year actuals"). Also decide whether entry in a percentage cell (D-05) and an "uplift selection by x%" action are in v1.

### Integration
- [x] **D-09 Live query vs. sync into the app store.** Decided by T-14: master data and actuals are **synced** into `planlayer_app`, on demand and on a schedule. The grid only ever queries the app store. Reasons:
  - the source may be in another database, and the grid joins actuals and plan in one query;
  - the speed of customer views is unknown;
  - restated actuals must not silently change locked versions or the basis of past spreads.

  "Live" means "connected and refreshable", not "queried per cell". What a sync does is set by D-27.
- [ ] **D-10 Adapter interface.** Define the contract every source/target adapter implements: `list_members(dim)`, `read_actuals(filter) -> Arrow table`, `write_plan(table, mode)`, `test_connection()`, schema introspection. Choose the interchange format (recommendation: Apache Arrow, so lake/DuckDB/Polars adapters fit later without conversion layers).
- [ ] **D-11 Data contract.** Required columns and shapes for each source view: members (code, name, parent_code, sort_order, valid_from/valid_to?), actuals (period, one code column per dimension, measure, amount in group currency per T-6). Accounts must carry an account type (at least revenue/cost) so the T-7 sign rules can be applied; the amount may arrive in either sign convention, as configured in E-12. Decide how hierarchies arrive (parent–child is recommended; the app computes all sums, and source sums are used only for reconciliation). Publish it as `docs/data-contract.md` plus a JSON schema the app validates against.
- [ ] **D-12 Write-back / export shape.** The target is `planlayer_out` (T-14), so Planlayer owns the target DDL. What lands in it: a wide table (one column per dimension) or long; full replace per version vs. incremental upsert; triggered on approve/lock, on a schedule, or manually. Konsolidat exports only locked cycles, so drafts never reach BI. Decide whether drafts are exported at all, or exported with their status visible.
- [ ] **D-13 Master data changes between syncs.** A member is renamed, moved to a new parent, deleted, or new. What happens to existing plan facts keyed on it (orphan handling, re-parent rules)? The core choice is between **as-of roll-up** (members as dated tranches, so each period rolls up under the tree valid at its end date, as Konsolidat does) and **restatement** (always the current tree, which is simpler and is what most planners expect for the plan year). It may differ between actuals and plan.
- [ ] **D-27 Sync discipline.** Adopt Budget & Forecast's import rules for every sync: resolve everything before writing anything, list every unmapped member, idempotent re-runs, no silent overwrite of changed actuals without an explicit flag, dry-run, and a sync-run record with source hash/row counts and actor.

### Application
- [ ] **D-14 ⚙ Where totals and spreading run.** Spreading rules are implemented once, in the Python `domain` package (T-11). Open: whether grid subtotals and large spreads are computed in SQL (`GROUP BY ROLLUP`, `INSERT … SELECT`) for speed, with the Python implementation as the reference the SQL must match. Spread results must tie **exactly** to the entered total (largest-remainder rounding); Konsolidat only tolerates 0.01 drift. Also decide whether the spread input (total + method/profile) is kept alongside the leaf rows so it can be re-spread later. *Spike S-01/S-03.*
- [x] **D-15 Python stack.** Decided: T-10.
- [x] **D-16 Frontend stack.** Decided: T-9.
- [ ] **D-17 ⚙ Grid component.** Glide Data Grid vs. RevoGrid. *Spike S-04.*
- [ ] **D-18 Grid API shape.** Cell-block request (row dims, column dims, filters, version(s), period range) → response (headers, cell values, cell metadata: editable, input type, locked, has comment). Edit request = list of `(coordinates, value, input_type)` plus a block version for conflict detection. Paging/virtualisation strategy for large blocks. Keep **zero and blank distinct** end to end: a stored 0 is a plan value, and no row means "not planned". Konsolidat conflates the two.
- [ ] **D-19 Concurrency.** Optimistic locking per cell vs. per block, conflict UX, and whether other planners' saves are pushed live (websocket/SSE) or picked up on refresh. Recommendation for v1: optimistic per cell, refresh-on-save, no live push. Konsolidat's whole-sheet `modified` check causes conflicts between unrelated cells; versioning per fact row avoids that.
- [ ] **D-20 Auth.** Local users with passwords vs. OIDC only vs. both. Self-hosted companies usually have an IdP (Entra ID, Google, Keycloak), so OIDC should be first-class.
- [ ] **D-21 Permissions model.** Roles (admin, planner, approver, viewer) × member scopes (e.g. planner may edit cost centers under node X). Enforcement: Postgres RLS in the app store, mirrored in the API. Decide how RLS gets the current user (session variable set per request).
- [ ] **D-22 Workflow granularity.** Status per version vs. per version × planning unit (cost center). The brief wants the latter. Define the state machine, who may make each transition, and what "locked" blocks at database level (trigger). Reference: Budget & Forecast's `guard_line()` trigger, which rejects writes to locked versions and outside the version's period range and names the approver in the error. The lock must cover INSERT, UPDATE **and** DELETE; Konsolidat's code records delete as a past hole in its lock.
- [ ] **D-23 Audit / change log.** Trigger-based (catches every write, including bulk) vs. app-written (can carry a reason and batch id). Recommendation: trigger-based, with the actor and batch id passed via session variables.
- [ ] **D-24 Background jobs.** Syncs, exports and large spreads need a job runner. Options: in-process scheduler (APScheduler), a Postgres-backed queue (procrastinate, pgqueuer), or Celery/RQ + Redis. Recommendation: Postgres-backed, so there's no extra service in compose.
- [ ] **D-25 Deployment shape.** The app store location is decided (T-14). Still to define:
  - **The container image:** the API serves the built frontend, background jobs run in-process (D-24), images are published to GHCR, and configuration comes from env vars.
  - **What the customer's DBA must grant:** create the two schemas (in one database or two), triggers and RLS policies in `planlayer_app`, and `SELECT` on the source views. Write this as an install checklist plus a SQL script the DBA can review and run.
  - **Connection settings:** three URLs (app store, source, output); source and output default to the app store URL when unset.
  - **Migrations:** whether they run on start or as a separate `planlayer migrate` command, and how an upgrade is rolled back.
  - **Minimum supported Postgres version.**
- [ ] **D-30 Backup and restore.** The scheduled backup of `planlayer_app` from T-14. `planlayer_out` is not backed up, because a "republish all" command rebuilds it from the app store. Decide:
  - the mechanism (`pg_dump --schema` from inside the container);
  - the targets (a mounted volume, S3-compatible storage);
  - the schedule and retention;
  - a tested restore command into an empty schema, followed by a republish;
  - whether the admin UI shows the last successful backup.
  Restore must also work into a different database (moving an installation).

## 3. Spikes

Throwaway code in `spikes/`, timeboxed. Each spike ends with numbers and a recommendation in the matching ADR.

- [ ] **S-01 Fact table + pivot benchmark.** Build schema variants (a) and (b)/(c) from D-01. Load the large test dataset (TD-11: ~1M and ~10M rows). Measure: a 60 cost centers × 12 months grid with subtotals, a 12 countries × 40 product groups × 12 months grid, a 5,000-cell bulk upsert, and the same queries with RLS enabled. Timebox: 3 days.
- [ ] **S-02 Hierarchy roll-up.** Recursive CTE vs. closure table on the ragged cost center tree and the account tree. Include an alternate hierarchy. Measure correctness against golden totals (TD-14) and speed. Timebox: 1 day.
- [ ] **S-03 Spreading engine.** Implement the D-28 bases (equal, profile, proportional to target values, proportional to a reference version/period range) on both axes (time and hierarchy), plus the rounding rule (the sum must tie exactly to the entered total). Run it against the golden cases in TD-14 in both SQL and Python. Timebox: 2 days.
- [ ] **S-04 Grid component shoot-out.** Same mock cell-block API behind Glide and RevoGrid. Test: paste of 2,000+ cells from Excel (including `1.234,56`-style locale numbers), fill-drag, keyboard flow (Enter/Tab/arrows), frozen row headers, editable subtotal rows, a percentage cell showing both % and amount, read-only/locked cell styling, 50k-cell scroll performance. Score on a fixed sheet. Timebox: 4 days.
- [ ] **S-05 Adapter prototype.** SQLAlchemy Postgres adapter that reads members and actuals from the simulated warehouse (TD-10) into Arrow and writes a plan table back. Then a 2-hour check: can a DuckDB-over-Parquet adapter implement the same interface unchanged? Validates D-10. Timebox: 2 days.
- [ ] **S-06 RLS + session context.** Prove per-request `SET LOCAL app.user_id` with a SQLAlchemy connection pool and RLS policies on member scopes, and that the lock trigger and audit trigger see the same context. Timebox: 1 day.

## 4. Simulated test data

All data is produced by one **deterministic, seeded generator** (`tools/gen_testdata/`, Python). Same seed means same output. It writes:
- (a) a simulated customer warehouse into the `dwh` database (T-14): ERP-style raw tables in a `bronze` schema, and the contract views in a `gold` schema;
- (b) the same data as CSV/Parquet, for adapter tests.

### The fictional company: "Nordwerk Industrietechnik"
Mid-size manufacturer of industrial pumps and valves, ~400 employees.

- [ ] **TD-01 Legal entities:** 4 entities (DE parent, CH, PL, US) with local currencies EUR, CHF, PLN, USD. Group currency is EUR. The raw warehouse schema (TD-10) keeps local-currency GL amounts plus a monthly rate table; the contract views convert to EUR, so Planlayer only ever sees EUR. Fiscal year = calendar year for 3 entities, April–March for the US entity (exercises D-03).
- [ ] **TD-02 Cost center hierarchy:** ~60 leaf cost centers, 4 levels (company → division → department → cost center). **Ragged** (some branches only 2–3 deep). Includes: one cost center **created mid-year**, one **closed** at year end, one **moved** to a different department between years (reorg), one department **renamed**, and an **alternate hierarchy** (cost centers by function: production / sales / admin) over the same leaves. Members carry validity dates so both D-13 options can be tested.
- [ ] **TD-03 Account hierarchy:** ~150 leaf accounts in a P&L-shaped tree (revenue, COGS, personnel, material, travel, IT, depreciation, other). Includes ~10 aggregated planning lines (for "plan at aggregate level"). Includes some accounts with a negative sign convention (revenue as negative in GL).
- [ ] **TD-04 Product hierarchy:** ~400 SKUs → ~40 product groups → 5 product lines. Sales are planned at product-group level.
- [ ] **TD-05 Countries / regions:** ~12 sales countries in 4 regions. Products × countries is **sparse** (not every product sells everywhere).
- [ ] **TD-06 Customers (optional for v1):** ~300 customers with key-account flags, for testing a high-cardinality dimension.
- [ ] **TD-07 Actuals:** 3 full years + current year to date, monthly, by entity × cost center × account, and sales by entity × country × product × measure (quantity, revenue). Realistic patterns: seasonality (Q4 peak, August dip), growth trend, one-off spikes, a few negative corrections, zeros vs. missing values kept distinct.
- [ ] **TD-08 Historical plan versions:** last year's Budget plus two Forecasts, so plan-vs-actual and version comparisons have data from day one.
- [ ] **TD-09 Users and permissions:** 1 admin/controller, 8 cost center owners (each scoped to a subtree, one with two disjoint subtrees), 3 sales managers (scoped by region), 1 CFO approver, 1 read-only auditor.
- [ ] **TD-10 Warehouse schema as a customer would have it:** an ERP-flavoured raw schema (GL lines with document numbers, cost center master with validity dates, German/English column names mixed) **plus** the contract-conforming views on top. GL lines are ledger-signed (debit − credit), and there are two variants of the actuals view, one in ledger signs and one in natural signs, so both E-12 sign settings are tested against the same data. This tests that admins can map their data via views, and gives S-05 a realistic source.
- [ ] **TD-11 Scale variants:** `small` (1 entity, ~10 cost centers, 20 accounts, 1 year; for unit tests and demos), `standard` (as above), `large` (×20 cost centers and SKUs, 5 years, ≥10M fact rows; for S-01 and load tests). Two date modes: **fixed** (an anchored calendar, for golden tests) and **relative** (actuals run to last month, so the demo never goes stale).
- [ ] **TD-17 Seasonality profiles:** a set covering the edge cases: even; retail (Q4 peak); manufacturing with an August shutdown (a zero month); one entered as percentages summing to exactly 100; one entered as raw unit counts; one derived from prior-year actuals; and invalid ones (percentages summing to 99.9 or 100.1, a negative value, all zeros, 11 values instead of 12).
- [ ] **TD-16 Documented demo defects.** The `standard` dataset contains a deliberate, listed set of process problems for the demo and smoke tests to find: a cost center with no submitted plan, a plan above the prior year by >50%, unexplained variances, a forecast not rolled after a close, and a version past its start date that isn't approved. The list lives in the generator's README, and the smoke test asserts exactly these are flagged.
- [ ] **TD-12 Dirty-data fixtures** (each a small, separate dataset with a known expected error): actuals on unmapped accounts; a duplicate member code; a circular parent reference; an orphan parent code; source-provided subtotals that **don't** reconcile with leaves; Unicode/umlaut names and codes with leading zeros (`0420`); a period outside the open range; mixed decimal separators in CSV.
- [ ] **TD-13 Grid input fixtures:** clipboard payloads as copied from Excel and LibreOffice (TSV, locale-formatted numbers, empty cells, trailing newline, a 2,000-cell block) for S-04 and later end-to-end tests.
- [ ] **TD-14 Golden expected results:** hand-checked expected outputs for totals per hierarchy node, every spread method and basis (including rounding remainders, spreading onto all-zero leaves, a reference with gaps or negative months, and spreading by prior-year actuals for a cost center that didn't exist last year; seasonality profiles from TD-17, including a profile applied to a partial-year range), version copy with shift + uplift, percentage-mode recalculation, and the roll-forecast operation. These are the acceptance tests for S-02, S-03 and the v1 engine.

## 5. Foundations

- [ ] **F-01** Repo layout (`backend/`, `frontend/`, `tools/`, `spikes/`, `docs/`), uv workspace, pre-commit (ruff, mypy, prettier, eslint).
- [ ] **F-02** `docker compose` for development: the app and one Postgres server with two databases, mirroring the typical setup from T-14. `planlayer` holds `planlayer_app`; `dwh` holds the simulated warehouse (seeded from TD-10) and `planlayer_out`. The database roles from T-14 are applied, so development runs under the same permissions as an installation. CI also runs the single-database variant (all three connections on one database).
- [ ] **F-03** CI: lint, type-check, unit tests against the `small` dataset, integration tests against Postgres in a container.
- [ ] **F-04** OpenAPI → TypeScript client generation wired into the build.
- [ ] **F-05** Alembic baseline for the app store. Decide the naming conventions for BI-readable tables and views.
- [ ] **F-06** ADR template and `docs/` skeleton.
- [ ] **F-07** Invariant suite: SQL assertions that return violating rows, run in CI and available to admins as a health check. Covers: unique fact grain, signs (revenue above plan is favourable, and a sync → export round trip returns the source signs unchanged), roll-ups tie to leaves **per slice** (not only per node total), no hierarchy cycles, every member assigned in each hierarchy, spreads tie exactly, no writes in locked versions, no plan values outside open periods. Each assertion gets a **must-flag fixture** (from TD-12) that has to make it fail, which proves the check catches the defect (Konsolidat's `.must_flag` pattern).
- [x] **F-08** `.gitignore` in place: it ignores `research/` (cloned reference repos), `docs_local/` (personal notes) and the usual Python/Node/data/editor artefacts.

## 6. v1 epics (refined after section 2 is done)

- [ ] **E-01** Connections & sync: configure a source, validate it against the data contract, sync members and actuals, show a reconciliation report.
- [ ] **E-12** Setup pages (admin): one place for the settings a new installation needs before planning starts. It covers:
  - **Sign rules (T-7):** how the source delivers signs (natural or ledger) and how the export target expects them. The flip is defined per account type or account group, with per-account overrides. A preview shows sample accounts with their raw source value, the stored value and the exported value.
  - **Fiscal calendar** (D-03).
  - **Seasonality profiles** (D-28).
  - **Default comparison versions.**
  - **Layer and role mapping** (D-08, D-21).
  - **Validation:** the page warns about accounts with no type, since those can't be signed.
- [ ] **E-02** Dimensions & hierarchies: browse synced members, plus manual members/hierarchies for app-only dimensions.
- [ ] **E-03** Planning areas: configure scope, grain, open periods and visible comparison versions.
- [ ] **E-04** Versions: create, copy with shift + uplift, compare.
- [ ] **E-05** Planning grid: edit, paste, fill-drag, subtotals, keyboard flow, percentage mode.
- [ ] **E-06** Top-down spreading from totals (proportional / equal / profile) and spread profiles.
- [ ] **E-07** Workflow: submit → approve → lock per version × planning unit, with database-enforced locks.
- [ ] **E-08** Change log and cell comments.
- [ ] **E-09** Users, OIDC login, roles and member-scoped permissions.
- [ ] **E-10** Publishing plan values to `planlayer_out` (D-12), with documented BI-readable tables and views.
- [ ] **E-11** Self-host packaging: container image on GHCR, the DBA install script and checklist (D-25), upgrade/migration procedure, the scheduled backup and restore (D-30), install docs.

**Later (not v1):** layers beyond `base`, rolling forecast, price × volume drivers, headcount template, simple formulas, non-Postgres adapters, balance sheet, an "attention" list (everything that needs a person: missing submissions, unexplained variances, unrolled forecast, unapproved version), variance commentary thresholds, and a default spread profile per account group.

---

## Suggested order

1. F-01, F-02, F-03, F-06 (repo skeleton, compose, CI, ADR template) — everything after builds on them.
2. TD-01…TD-05, TD-07, TD-11 (the generator's core) — the spikes depend on it.
3. S-01, S-02, S-03 → close D-01, D-02, D-14. S-03's spreading code becomes the first `domain` module.
4. S-04 → close D-17. S-05 → close D-10. S-06 → close D-21, D-23. D-25 before F-02 is extended for production.
5. A thin end-to-end slice: sync one entity's cost centers, accounts and actuals; one budget version; the grid with prior-year actuals beside plan; edit and save a cell with the per-cell version check; enter a year total and spread it by prior-year actuals.
6. Remaining ADRs and F-items, then epics in order E-01 → E-05 → E-06 → E-09 → E-07 → the rest.
