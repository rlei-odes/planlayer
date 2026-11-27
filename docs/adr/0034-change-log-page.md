# 0034: The change log page

- **Status:** Accepted
- **Amends:** ADR 0005 (the change logs), ADR 0010 (undo)
- **Date:** 2026-11-27

## Context

The audit triggers record every change of plan values, planned hours and FTE or headcount in
`change_log`, `capacity_change_log` and `workforce_change_log` (ADR 0005), with the actor and the
request's change batch. Undo reads them (ADR 0010); nothing else showed them. Controllers want to
see who changed what in a round, without reading thousands of single cells: one carry-over or
recalculation writes a whole round's cells at once.

## Decision

### The page

Controllers get a *Log* page under Plan, after Status, for the current round.

- **One entry per change batch,** newest first, 50 a page: when, who, the sheets (costs,
  capacity, workforce), what wrote the cells, the cost centers (one by name, more as codes and
  a count), the number of cells and how many were blanked.
- **Entered or calculated:** a batch is *calculated* when all its cells are written by the app
  from other inputs (personnel costs, cost forwarding, capacity credits) or by the system user;
  any typed cell makes it *entered*. Tabs show all, entered or calculated, with counts.
- **Filters:** sheet, user, cost center. A cost center filter narrows the entries and their
  lines to it.
- **An entry opens to its lines:** cost center, line, the months changed, and before and after:
  the changed months' sum for amounts and hours, the last changed month for FTE and headcount.
  A blank stays blank. A batch of more than 500 lines shows the first 500.

### Row-level security on the change logs

The three change logs get forced row-level security. A reader sees the changes of the cost
centers whose values they read, by the same rule as the values: `cost_center_access` for amounts
and the workforce, `capacity_access` for hours, and small groups of personnel costs hidden from
whoever may not read their pay (ADR 0022). The audit triggers insert every row (`WITH CHECK
(true)`). No one updates or deletes a change log row.

**Undo reads its own batch with system rights,** after its own checks (the user's batch, on a
cost center they may write). Row-level security then never hides a cell of a batch from its undo.

## Reasons

- **Batches, not cells:** a batch is one decision by one person (a save, a paste, an undo, a
  carry-over). A list of cells buries the one edit that matters under a recalculation of 7,000.
- **Calculated changes kept, not hidden:** a forwarding or personnel recalculation moves the
  values a planner sees. The tab separates them; the log still shows that and when they moved.
- **The values' rule for the logs:** a change shows an amount as surely as the value does. With
  any other rule, the log would show what the sheet hides, small groups of personnel costs in
  particular.
- **System rights only for undo's read:** undo already decides whether the user may undo the
  batch. Reading it whole keeps undo working whatever the policy on the log; the writes still run
  as the user, through the lock trigger and the values' policies.

## Consequences

- Migration 0004: row-level security and the policies on the three change logs.
- `GET /api/versions/{code}/changes` (filters, cursor) and `GET /api/versions/{code}/changes/{batch}`.
- Code and tests that read a change log outside a request do it as a user, as for the values.
- Open: line changes on the capacity sheet (`capacity_line_log`) and positions
  (`workforce_position_log`) are not on the page; a line's history for planners.
