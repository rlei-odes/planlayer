# Spikes S-01, S-02, S-03: fact store, hierarchy roll-up, spreading

Throwaway code. It measures against the `large` Nordwerk dataset: 10.4M fact rows, 1,240 cost centers, 8,000 SKUs, 2021-01 to 2026-06.

**Setup:** Postgres 18 in the dev compose container with default settings (`shared_buffers` 128 MB), on the development laptop. Times are medians of warm runs. Every comparison asserts that both variants return identical results.

```
cd tools/gen_testdata && uv run gen-testdata --db postgresql://postgres:postgres@localhost:5432/dwh \
    --variant large --bronze-schema large_bronze --gold-schema large_gold   # schemas must exist
cd spikes/fact_store
uv run load.py            # schema "spike": members, hierarchies, closure, fact_wide, fact_eav
uv run s01_bench.py       # S-01 grid queries, upsert, RLS
uv run s01_followup.py    # S-01 plan-grain sync, RLS scope as ARRAY(...)
uv run s01_area_tables.py # S-01 one table per planning area under RLS
uv run s02_rollup.py      # S-02 closure vs recursive CTE, checked per slice
```

## S-01: fact table layout (D-01)

| | One column per dimension | Generic (fact + member rows) |
|---|---|---|
| Storage for 10.4M facts | 1.4 GB | 4.1 GB (31M member rows) |
| Load | 32 s + 30 s indexes | 75 s + 23 s indexes |
| Q1: 60 cost centers × 12 months, department subtotals | 61 ms | 571 ms |
| Q1b: 1 cost center × accounts × 12 months, group subtotals | 2 ms | 17 ms |
| Q2: countries × 40 product groups × 12 months, from SKU rows | 1,374 ms | 1,510 ms |
| Q3: upsert of 5,040 cells (update path) | 108 ms | 3,058 ms |
| Q1 under RLS (subtree grant via closure) | 362 ms | 1,370 ms |
| Q2 under RLS | 1,390 ms | 12,833 ms |
| Unique fact grain as a constraint | yes (unique index, `NULLS NOT DISTINCT`) | no |

Follow-ups on the column-per-dimension layout:

| | |
|---|---|
| Q2 with actuals synced at the plan grain (product group, 63k rows instead of 2.4M) | **11 ms** (from 1,388) |
| Q1 in a table holding only the cost center planning area, no RLS | 42 ms |
| The same under RLS, scope as `= ANY (ARRAY(subquery))` | **46 ms** |
| Q1, leaf rows from SQL with subtotals in Python (`Decimal`) | 37 ms (ROLLUP: 61 ms) |

**What drove the RLS cost:** with all planning areas in one table, the policy needs a `cost_center_id IS NULL` branch so sales rows stay visible. That branch turns the scan into a bitmap OR over 883k unrelated rows. With one table per planning area, the branch disappears. Written as `ARRAY(...)`, the scope becomes an init plan evaluated once per query, and RLS costs about 8 %.

## S-02: hierarchy roll-up (D-02)

Every slice (node × account × period) of a full year, checked against sums computed in Python from leaf values and the parent map:

| Case | Slices | Closure table | Recursive CTE | Python |
|---|---|---|---|---|
| Cost centers (ragged tree) × accounts | 1,039,986 | 2.8 s | 6.0 s | 7.8 s |
| Function hierarchy (alternate) × accounts | 1,026,906 | 2.6 s | 5.4 s | 6.8 s |
| Accounts × cost centers | 1,188,026 | 3.1 s | 6.4 s | 7.4 s |

- All three methods agree on every slice, and the root totals of all three hierarchies are equal.
- One node (Division Pumps, 464 leaves, all accounts) × 12 months takes about 1 s with either method. The cost is in reading the 1.4M leaf rows, not in the hierarchy.
- Closure size is 41k rows for all hierarchies, with a maximum depth of 3, and a full rebuild takes 20 ms.

## S-03: spreading (D-14, D-28)

The engine is `backend/src/planlayer/domain/spreading.py`, with tests in `backend/tests/test_spreading.py`. It uses largest-remainder rounding in integer arithmetic:
- **Exact ties:** the parts sum exactly to the total for 2,000 random cases. Each part is within one cent of its exact share, and zero-weight cells stay at exactly 0.
- **Speed:** a node total spread over 1,240 cost centers × 12 months (14,880 cells) in proportion to reference values takes 122 ms.
