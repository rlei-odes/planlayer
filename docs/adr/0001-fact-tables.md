# 0001: Fact tables per planning area, one column per dimension

- **Status:** Accepted
- **Closes:** D-01
- **Date:** 2026-11-08

## Context

Plan and actual values must support fast grid reads with subtotals, bulk writes of thousands of cells, row-level security on member scopes (D-21), a unique grain enforced by the database (T-11), and tables that BI tools can read. Planning areas have different dimensions: cost center planning uses entity × cost center × account, while sales planning uses entity × country × product group. Every fact carries `version`, `layer` and `measure` (T-23).

## Decision

1. **One fact table per planning area** in `planlayer_app`, named after the area. It has one integer column per dimension of that area (`dim_<name>`), plus `version_id`, `layer_id`, `measure_id`, `period`, `amount numeric(18,2)` and `row_version`.
2. **Columns come from the dimension registry.** Configuring a planning area generates its table through a migration. Columns are added, never dropped automatically.
3. **The grain is a unique index** over all key columns, with `NULLS NOT DISTINCT`.
4. **Members** are rows of one member table per installation, with an integer surrogate key, the source code and a name. Fact tables reference members by key.
5. **Actuals are synced at the planning area's grain.** A sync aggregates source rows to the area's levels, e.g. SKU sales to product groups, and stores them in the same fact table under an actuals version.
6. **RLS policies** filter on the area's scope column with the scope as `dim_x = ANY (ARRAY(<scope subquery>))`, so the scope is evaluated once per query.
7. **Published output** (`planlayer_out`, D-12) is shaped separately for BI, with codes and names instead of keys.

## Reasons

Measured on 10.4M facts (spike S-01, [spikes/fact_store](../../spikes/fact_store/README.md)):
- **Grid reads:** a 60 cost centers × 12 months grid with subtotals takes 42 ms. A 12 countries × 40 product groups × 12 months grid at plan grain takes 11 ms.
- **Writes:** upserting 5,040 cells takes 108 ms, in one statement with `ON CONFLICT` on the grain index.
- **RLS:** a subtree grant costs about 8 % (46 ms instead of 42 ms) once each table holds a single planning area. In a shared table, rows of other areas force a NULL branch in the policy, and the query becomes nine times slower.
- **Sync grain:** syncing actuals at plan grain turns a 1.4 s grid into 11 ms. Planners compare plan with actuals at the plan's level, so lower detail isn't needed in the grid.
- **BI readability:** one column per dimension is a plain star-schema fact, and the unique grain is a database constraint rather than a convention.

## Consequences

- Adding a dimension to a planning area is a migration: a new column, a backfilled grain index, and updated RLS policies. The registry generates it, and E-03 has to cover it.
- Queries spanning planning areas (e.g. a P&L combining cost center and sales plans) union the area tables. The published output in `planlayer_out` is where such combinations live for BI.
- Lower-grain actuals (e.g. SKU sales) stay in the source. Drill-down below plan grain reads them through the adapter, not the grid.
- D-04 (planning area definition) gains the table name and the per-dimension plan level as part of the configuration. D-27 (sync) aggregates to that level.
