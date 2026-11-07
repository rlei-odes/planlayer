# Konsolidat — notes

Source: https://github.com/grynn-in/konsolidat (MIT), cloned to `research/konsolidat/`.
This repo is the **data stack**: dbt models, ClickHouse DDL, Cube schemas, ERP extractors, docs and PRDs. The Frappe application (`konsol`), which holds the budget doctypes, workflow and API, lives in a separate repo and is not included. Statements about the app side below come from Konsolidat's docs and PRDs, not from code.

## Architecture

- **Pipeline:** ERP (D365 F&O, ERPNext) → Airbyte → ClickHouse (bronze → silver → gold via dbt) → Cube SQL API → Excel.
- **Frappe** (MariaDB + Redis) provides auth, RBAC, workflow, audit and the admin UI. It writes budget input back to ClickHouse staging tables. The ERP stays read-only.
- **Excel is the planning UI:** `=K.EPM()` reads values and `EPMSAVE()` writes them. Period keywords such as `Q1`, `H2` and `FY` are resolved server-side.
- **Weight:** ClickHouse, Airbyte (on k8s via abctl), dbt, Cube, Dagster, Frappe, MariaDB and Redis.

## Dimensions

- Dimensions are declared once in config (`vars.dimensions` in `dbt_project.yml`: `name`, `source_column`, `label`, `in_budget`).
- Macros (`dim_select`, `dim_group_by`, `dim_join_on`, …) expand them into **one `dim_*` column per dimension** on every wide fact table.
- `in_budget: true` selects the dimensions that are part of the budget grain. Not every analytical dimension is planned.
- Adding a dimension means a config change plus a full rebuild. Columns are added and never dropped automatically. In the app, a `Dimension`/`Measure`/`Fact Table` registry generates the DDL and the dbt sources (Draft/Published/Inactive lifecycle).
- This is option (c) of our D-01 (columns generated from a registry), proven in practice on a columnar store.

## Budget model

- **Grain:** scenario × entity × fiscal_year × fiscal_period × account × budget dims × **layer**. Periods are the integers 1–12 plus a `fiscal_periods` calendar table with `end_date`.
- **Two input methods land in one fact (`gold_spread_budget`):**
  - an annual amount plus a spread profile, spread into 12 rows;
  - manual monthly values (treated as an "identity spread").
  - A manual `base` row overrides an annual spread for the same grain, and a test asserts the grain is unique afterwards.
- **Spread profiles:** relative weights, normalised to sum to 1 per profile. Tests use a **0.01 tolerance** rather than an exact tie, so rounding remainders are not handled. Planned next: a default profile per account group (revenue = seasonal, payroll = even).
- **Layers:** `base` (submitter), `challenge` (controller), `management`, `board` (approver).
  - Each role edits only its own layer, and everyone can see all layers.
  - Effective value = the sum of the layers; there is never a stored "final" row.
  - Reads sum all layers unless a single layer is requested.
- **Budget Cycle** (scenario × fiscal year): `Open` → `Locked`. Locking syncs every sheet to ClickHouse and refuses further edits; cancelling a cycle withdraws it so it can be amended.
  - A Budget Sheet is one per (cycle, entity, layer); a Budget Line is one per (account, dims) with 12 period cells.
  - **Only locked cycles reach the warehouse**, so open drafts never show up in BI.
- **Forecasts use the same structure as budgets,** distinguished only by the scenario type.

## Hierarchies

- **Storage:** a `reporting_hierarchies` table: `hierarchy_name`, `dimension`, member, parent, `is_group`, level, path. **Several named hierarchies per dimension** are allowed (alternate roll-ups).
- **Members are dated tranches** (`member_effective_from/to`). A rename or a move is a second tranche of the same code.
- **The closure table** (`gold_reporting_hierarchy_closure`) is built with a recursive CTE. Each ancestor link carries a validity window (the intersection of the tranche windows). Depth is capped at 20.
- **Roll-ups resolve the tree as of each period's end date.** A leaf that moved is counted under the parent it had in that period, and an ended group gets nothing afterwards. This is the "as-of" answer to reorgs. The alternative is restating history under the current tree.
- **Leaves and groups are distinct** (`is_group`). Input goes to leaves; groups are read.
- **Reconciliation outputs are models:** `gold_unmapped_dimension_values` and `gold_unassigned_hierarchy_members`.

## Workflow, concurrency, forecasting (PRDs; mostly not started)

- **Approval chain (planned):** Draft → Submitted → Dept Manager → Controller → CFO Approved (locked), with Reject/Resubmit and an email notification at each hand-off.
- **Cell locking (planned):**
  - Optimistic locking via `base_modified`, with a 409 response carrying the current value.
  - Optional pessimistic 5-minute locks.
  - Their own open issue: the lock covers a whole sheet, so edits to different cells conflict with each other. **Lesson: lock per cell, not per document.**
- **Rolling forecast (planned):**
  - an explicit per-entity `period_close_status` (open/closed), which they deliberately do not infer from GL postings;
  - a forward window of N periods splicing actuals for closed periods with forecast for open ones;
  - tests that the window is anchored and that no period appears twice.
- **Driver planning (planned):** price × volume per period, with the amount computed and a test asserting `amount = price × volume`.

## Testing approach

- Around 140 SQL assertions in `dbt_project/tests/`. Each returns the violating rows, and zero rows means pass. Severities are error or warn.
- **Fixtures are paired with tests** (`test_fixtures/`). A fixture named `<test>.must_flag.sql` **must make its test fail**, which proves the assertion actually catches the defect. Header directives (`-- gate: expect <test> pass|flag`) are run by `scripts/fixture_gate.py`.
- Roll-up ties are checked **per slice** (node × entity × period × account × every dimension), not on node totals alone. Totals-only checks pass on wrong roll-ups when amounts net out.
- **Invariants worth adopting:** a unique fact grain, roll-up ties, no circular hierarchy references, closure windows, spread sums to the annual figure, spread has 12 periods, and favourable-flag logic per account type.
- **Demo data is a connector** (`demo_data`) that runs through the same pipeline as a real ERP, so the demo doubles as an integration smoke test.

## Implications for Planlayer

| Topic | Takeaway | Backlog |
|---|---|---|
| Dimension encoding | Generated `dim_*` columns from a registry works and is BI-friendly. Make it the leading candidate in S-01. | D-01 |
| Planned vs. analytical dimensions | A per-planning-area subset of dimensions (their `in_budget`). | D-04 |
| Layers | Strong case for `layer` in the fact key from day one, with a per-role edit right per layer. | D-08 |
| Alternate + dated hierarchies | Named hierarchies per dimension; dated member tranches; as-of roll-up vs. restatement is an explicit decision. | D-02, D-13 |
| Spread rounding | They tolerate 0.01 drift. We want an exact tie (largest-remainder rounding). | D-14, TD-14 |
| Concurrency | Per-cell optimistic versioning. | D-19 |
| Closed periods | An explicit close status per entity is more robust than inferring it from data. | D-03 |
| Export timing | Only locked/approved data reaches the target, so drafts don't leak into BI. | D-12 |
| Test method | SQL invariant suite + must-flag fixtures. | F-07 (new), TD-12, TD-14 |
| Demo via adapter | The simulated warehouse is read through the real adapter, not loaded around it. | TD-10, S-05 |
| Stack weight | Confirms the brief: a Postgres-only app with no separate ETL/semantic layer is the differentiator. | — |
