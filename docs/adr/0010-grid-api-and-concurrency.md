# 0010: Grid API and concurrent edits

- **Status:** Accepted
- **Closes:** D-18, D-19
- **Date:** 2026-11-08

## Context

The planning page shows one cost center at a time (T-25): its accounts × the fiscal year's months, with comparison columns. A cost center grid reads in about 2 ms on the large dataset (S-01). Several people can work on the same cost center: owner, deputies, controller (ADR 0009). Amounts travel as strings (T-12), and zero and blank are different values.

## Decision

### Reading

`GET /api/versions/{round}/cost-centers/{cc}/grid` returns the whole sheet in one response, without paging:

- **Header:** the round (code, name, fiscal year, status, deadline), the cost center, its workflow status, and `lock_reason`: why nothing can be edited (round not open, cost center not enabled, submitted or approved, read-only access), or `null`.
- **Columns:** the periods, and the comparison columns in order (kind and label).
- **Rows:** group rows with computed subtotals, then their accounts. Each account row carries its comparison values per month, its plan cells, and its entry-form facts: form, spread basis, whether the basis fell back to an even spread, and whether its months are visible and editable and its year total is editable.
- **Cells:** `value` (a decimal string, or `null` for blank) and `row_version` (or `null` when the cell has no row).

Editability already combines the lock and the entry form, so the client renders what the server says and decides nothing itself.

### Writing

`POST …/edits` takes a list of up to 20,000 edits. Each edit is an account, a month (or `null` for the year total, spread by the row's basis), a value (or `null` to blank the cell or the year), and `expected`: the 12 row versions of that row as the client last saw them.

- **All or nothing:** the edits are applied in one transaction. Any invalid value, rule violation or conflict rolls everything back. A paste or a fill-drag is sent as one request, so it saves completely or not at all.
- **Responses:** `200` with the updated grid, `409` with the list of conflicting cells, `422` for an invalid edit, `423` when the cost center is locked.

### Concurrency: optimistic, per cell

- Every plan cell has a `row_version`, raised by one on each update. The server locks the cost center's cells (`SELECT … FOR UPDATE`) and compares each cell an edit touches with the `expected` version. A cell that changed in the meantime, including one created or deleted by someone else, is a conflict.
- Two people editing **different cells** of the same sheet don't conflict. A year total touches all 12 months, so it conflicts with a change to any of them.
- Cells written earlier in the same request aren't compared again, so one paste may cover a cell twice.
- **Conflict UX:** nothing is saved. The page says how many cells someone else changed, reloads the grid with the current values, and the planner enters their change again.
- **No live push:** a planner sees others' changes when the grid reloads: after each save, on switching cost centers, and after a conflict.

## Reasons

- **Whole sheet, no paging:** sheets are small (one cost center, ~20–80 accounts, 12 months) and fast to read. One response keeps subtotals and editability consistent.
- **Per-cell versions** avoid the false conflicts a whole-sheet version causes between unrelated cells, while still catching every overwrite.
- **All or nothing** makes a paste behave like one action, and matches undo of the last change batch (E-05).
- **Server-decided editability:** the store refuses what a form or lock forbids, and the database enforces locks and scopes (ADR 0005, 0008, 0009). A client that rendered its own rules would only drift from them.
- **No live push in v1:** real-time co-editing is out of scope (scope.md). Conflicts are rare in per-cost-center planning, and they are caught exactly.

## Consequences

- E-05 builds undo of the last change batch on top of the batch id the change log already records, and copy with headers on the client.
- Editing group rows (spreading down the hierarchy, D-28) and percentage cells (D-05) extend the edit with new kinds; the conflict rule stays the same.
- F-04 generates the TypeScript types of these responses from the OpenAPI schema.
