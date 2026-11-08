# 0002: Hierarchies as parent-child edges with a closure table

- **Status:** Accepted
- **Closes:** D-02
- **Date:** 2026-11-08

## Context

Members roll up through hierarchies that can be ragged (leaves at different depths), and a dimension can have several hierarchies over the same leaves (e.g. cost centers by organisation and by function). Roll-ups restate under the current hierarchy (T-19). Roll-ups must tie per slice, not only at node totals (F-07).

## Decision

1. **Named hierarchies per dimension.** Alternate hierarchies are in v1.
2. **Each hierarchy is stored as parent-child edges**, one row per member with its parent. The source delivers them parent-child (D-11).
3. **A closure table** holds `(hierarchy, ancestor, descendant, depth)` for every member, including itself. It is rebuilt in full after each sync that changes the hierarchy.
4. **Roll-ups join facts to the closure table** on the descendant and group by the ancestor. The grid computes subtotals for the leaves it shows itself (0003).
5. **Members are leaves or groups** (`is_group`). Plan values are stored on leaves only; a value entered on a group is spread to its leaves (D-28).
6. **No validity dates in the closure.** It reflects the current hierarchy (T-19). Members keep their validity dates, so an as-of roll-up can be added later.
7. **The sync rejects cycles** and reports orphan parents before writing (D-27).

## Reasons

Measured on the large dataset (spike S-02, [spikes/fact_store](../../spikes/fact_store/README.md)):
- **Correctness:** roll-ups through the closure table matched an independent calculation on every one of ~1M slices, for the ragged tree, the alternate hierarchy and the account tree.
- **Speed:** the closure table is about twice as fast as recursive queries at full scale. It is 41k rows for all hierarchies, and rebuilds in 20 ms.
- **Simplicity:** a rebuild after each sync is simpler and safer than maintaining the closure incrementally.

## Consequences

- Totals for a large subtree over all its accounts (e.g. a whole division, 1.4M leaf rows a year) take about 1 s, a cost dominated by reading leaf rows. Grids show nodes at plan grain and within a user's scope, so this is a report-level cost. If node totals over large subtrees become a grid need, they get an aggregate table maintained on write.
- Invariant checks (F-07) use the closure table: no cycles, a single root per hierarchy, every member assigned, and roll-ups tying per slice.
