# Planlayer v1 scope

Planlayer is an open-source, self-hosted web app for corporate budgeting and forecasting. Planners enter plan values in an Excel-like grid, next to their actuals. The data lives in plain Postgres tables that any BI tool can read.

## Who v1 is for

A mid-sized company whose actuals and master data already sit in a Postgres warehouse, and whose planning today runs on Excel files sent around by email.

- **Admin / controller:** connects the source, defines what is planned and by whom, runs the cycle.
- **Planners:** cost center owners and sales managers, who see and edit only their own area.
- **Approvers:** review, compare versions, approve and lock.

## v1 does

| Area | In v1 |
|---|---|
| Planning areas | **Cost center planning** (cost center × account × month) and **sales planning** (country × product group × month, amount and quantity). The plan grain is configurable per area, so planning can happen at an aggregate level. |
| Data in | Master data, hierarchies and actuals read from the customer's **Postgres**. For each dataset Planlayer needs, the admin points to a view, table or query and maps its columns. The data is synced into Planlayer's schema on demand or on a schedule, with a reconciliation report. Cost centers are enabled per planning round, and their owners get planning rights by default. |
| Data out | Plan values published by the controller to a dedicated output schema in the customer's Postgres (typically next to the warehouse's gold layer), as documented, BI-readable tables and views. |
| Grid | One planning unit (e.g. cost center) at a time. Edit, paste from Excel/LibreOffice, fill-drag, keyboard navigation, computed subtotals, actuals and comparison versions shown alongside. |
| Spreading | Top-down from totals, over time (year → months) and down the hierarchy (node → leaves): equal, seasonality profile, in proportion to existing values, or in proportion to a reference (prior-year actuals, another version). Results tie exactly to the entered total. |
| Percentage mode | A line entered as a percentage of one reference line (e.g. COGS = 62% of sales). |
| Versions | Budget, forecast and scenario versions; copy with period shift and uplift. |
| Workflow | Submit → approve → lock per version and cost center. Locks are enforced by the database. A status page rolls progress up by department. |
| Audit | Every change records actor, time, old and new value. Cell comments. |
| Access | OIDC or LDAPS login, roles, and edit rights scoped to hierarchy members (e.g. "cost centers under Production"). |
| Setup | Admin pages for connection, sign rules, fiscal calendar, seasonality profiles, roles. A yearly planning setup composes what is planned and in what form: full-year only, monthly, or a full-year value spread evenly, by a seasonality profile or by a reference. |
| Hosting | One container, connected to the customer's existing Postgres, where Planlayer keeps two schemas of its own: app data, and published output. They can be in the same database or in separate ones. A scheduled backup of the app data is built in. Single company per installation. |

## Fixed assumptions

- **One currency:** all values are in group currency. Conversion happens in the source views; Planlayer stores no currency per value.
- **Natural signs:** revenue and costs are stored as positive numbers, and the account type supplies the sign. Planlayer converts at sync and export as configured.
- **Month grain:** months are the lowest time level, aggregated to quarters and years. One fiscal year definition per installation, with a configurable start month (default January).
- **Hierarchy changes restate:** values always roll up under the current hierarchy.
- **Single tenant:** one company per installation, no multi-tenant hosting.
## v1 does not do

These are excluded because each one grows the product into a different one, and established tools already cover them well:

- Statutory **consolidation**, **FX translation**, **intercompany** elimination
- Cost **allocations**
- A general **formula engine** or driver models beyond percentage-of-one-line
- **Balance sheet** and cash-flow planning
- **Headcount** / position planning
- Rolling forecasts (as an automatic window)
- Sources other than Postgres (the adapter layer is built to allow them later)
- Real-time co-editing or presence
- Multi-tenant SaaS operation
- An Excel add-in (the grid accepts paste from Excel instead)

## Candidates for after v1

- **Planning features:** budget layers beyond `base` (challenge / management / board), rolling forecast, price × volume drivers, headcount planning as a module of the yearly setup, simple formulas.
- **Sources:** non-Postgres adapters (Snowflake, BigQuery, Databricks, Parquet/Delta/Iceberg).
- **Review support:** an "attention" list of open items, and variance commentary thresholds.

## Licence

Apache License 2.0.

## How to use this page

A feature request outside the "v1 does" table goes to "Candidates for after v1" or gets declined. Changing this page is a decision, not a side effect of implementation.
