# 0003: Spreading and grid subtotals in Python, roll-ups over large slices in SQL

- **Status:** Accepted
- **Closes:** D-14
- **Date:** 2026-11-08

## Context

Spreading must tie exactly to the entered total (D-28), and the calculation rules live in the pure-Python `domain` package (T-11). The grid needs subtotals over the rows it shows, while reports and node headers need totals over subtrees it doesn't show.

## Decision

1. **Spreading runs in Python**, in `planlayer.domain.spreading`: one function splits a total over weighted cells, rounding by largest remainder in integer arithmetic.
   - It serves both axes, time and hierarchy, and both at once (node × year to leaf × month cells).
   - Weights come from equal shares, a seasonality profile, existing values, or reference values.
   - The result is written with one bulk upsert (0001).
2. **Grid subtotals are computed in Python** from the leaf rows the grid fetches, in `Decimal`.
3. **Totals over subtrees the grid doesn't show** (node headers, reports, invariant checks) are SQL roll-ups through the closure table (0002).
4. **Leaf values are the stored truth.** The spread itself (total, method, basis) is recorded with the change batch in the change log (D-23), for audit and to repeat it. It is not a live rule that recalculates on its own.

## Reasons

Measured on the large dataset (spikes S-01 and S-03, [spikes/fact_store](../../spikes/fact_store/README.md)):
- **Speed:** a spread over 14,880 cells takes 122 ms, about the cost of writing them. Grid subtotals in Python take 37 ms for a 60 × 12 grid, against 61 ms with `GROUP BY ROLLUP`.
- **Exactness:** the spread ties exactly in 2,000 random cases. Each part is within a cent of its exact share, and zero-weight cells stay at 0.
- **One implementation:** Python is the single place for the rounding rule, testable without a database. SQL roll-ups only sum stored values, which needs no rounding.

## Consequences

- The grid API (D-18) returns leaf rows with their hierarchy path, and the client or API layer computes the visible subtotals. Node totals outside the visible rows come from separate roll-up queries.
- What a spread does with an all-zero or negative reference is a parameter of the engine. The defaults per entry mode are settled in D-28.
