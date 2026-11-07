# Konsolidat's Frappe app (`konsol`) — notes

Source: https://github.com/grynn-in/konsol (public), cloned to `research/konsol/`. It is the application half of Konsolidat (see [konsolidat.md](konsolidat.md)): Frappe doctypes, the whitelisted API, the Excel add-in, and a separate Vue app (`close-ui/`) for the month-end close. The notes focus on how budget data gets entered.

## Budget data model (doctypes)

| Doctype | Grain | Fields |
|---|---|---|
| Budget Cycle | scenario × fiscal year | `deadline`, `status` Open/Locked, `locked_by/on`. Submittable: submit = lock, cancel = reopen. It is the only approval construct; there is no per-cost-center workflow. |
| Budget Sheet | cycle × entity × layer | `annual_total` (read-only), child table `lines`. |
| Budget Line | (child) account × budget dims | **Wide row**: `period_01` … `period_12` + `annual` (read-only, computed). |
| Budget Annual Input | scenario × entity × year × account × dims | `annual_amount` + `spread_profile_id` (free text, not a link). |
| Spread Profile | profile × period | `weight` per period (relative; normalised downstream). |

- The wide layout is deliberate ("mirrors how budgets are entered in Excel"). It is exploded to tall rows (one per period) when the cycle locks.
- **Zero means "not budgeted":** the explode step skips zero months, so a typed 0 and an empty cell end up the same. The conflict API, by contrast, does distinguish `None` from `0`.
- **Layer permissions** are a fixed map, layer → role (`base` → Budget Submitter / Entity Accountant, `challenge` → Budget Controller, `management` → Budget Manager, `board` → Budget Approver). System Manager can edit all layers.
- **Both input paths check the cycle lock** on save *and* on delete. Their code comments describe delete as a past hole in the lock: a row could be removed from a locked cycle instead of edited.

## Ways a user can enter budget data

1. **Excel, cell by cell:** `=K.EPMSAVE(amount, entity, year, period, account, scenario, layer, [dimensions], [hierarchy], [node])`.
   - Each recalculated formula posts one cell to `budget_cell_save`.
   - The client skips the call when the cached value is unchanged.
   - A refused write shows the server's reason in the cell instead of the amount.
   - Writes are allowed only at **leaf** hierarchy nodes ("budget write-back is only allowed at leaf nodes"), so there is no top-down entry on a group.
2. **Excel task pane:** sign-in, a report **template** picker (e.g. `pnl_monthly`, `trial_balance_long`) that lays out a sheet of `K.EPM` read formulas for an entity/year/period range, **Snapshot values** (freeze the formulas to values), and refresh of a sheet or the whole workbook.
3. **Frappe Desk form:** the Budget Sheet with an editable child grid (account, dims, 12 month columns, annual computed). It is the standard Frappe grid. Bulk upload/download is not enabled on it, and there is no custom paste handling.
4. **Annual amount + profile:** a Budget Annual Input row per account. The 12 monthly values are produced in dbt, not shown at entry time. A manual monthly `base` line for the same grain overrides it.
5. **API batch:** `budget_save_batch` takes a list of lines and reports per-item errors (permission denials are surfaced individually).

## Spreading

- Only **static profiles**: weight tables such as `EVEN` or `SEASONAL_RETAIL`, maintained by an admin.
- **Spreading in proportion to reference data** (last year's actuals, last year's plan, another version) **is not available**, and neither is spreading from a hierarchy node down to its leaves. A default profile per account group is planned (PRD), not built.
- Spreading happens after entry, in the warehouse model, so the planner doesn't see the monthly result when typing the annual figure.

## Concurrency

`budget_cell_save` takes `base_modified`. A stale value returns HTTP 409 with `your_amount`, `current_amount` and `current_modified`, and a locked cycle returns 423. The check is per sheet (entity × layer), so two people editing different accounts of the same sheet conflict. Rows are serialised with `SELECT … FOR UPDATE` on the sheet.

## Sign convention

Actuals are **debit-positive** (`period_net_amount = debit − credit`; "a negative total is a profit"), so revenue actuals are negative. The account master carries `normal_balance` (Debit/Credit), but its own model description says nothing consumes it.

Budgets are stored **as entered**, with no sign handling. The budgeting guide's sample enters revenue account 4100 as **+2,400,000**, while revenue actuals arrive negative. The variance model (`gold_variance_analysis`) then compares `actual − budget` directly, and flags revenue as favourable when `actual > budget`. With debit-positive actuals, more revenue is *more negative*, so that flag looks inverted unless revenue budgets are entered as negative numbers. Nothing in the guide asks for that, and there is no flip anywhere from `period_net_amount` to the variance. As far as the code shows, the sign convention is not enforced or translated between actuals and plan.

## Implications for Planlayer

| Topic | Takeaway | Backlog |
|---|---|---|
| Entry modes | Konsolidat offers cell formulas, a plain form grid and annual-plus-profile. None of them is a real planning grid with paste, fill and visible spreading. That is the gap Planlayer targets. | D-29 (new), S-04 |
| Spread basis | Proportional to a reference (prior-year actuals, prior plan, another version, current values) is missing in both reference projects. Make it first-class, together with its fallbacks. | D-28 (new), TD-14 |
| Visible spreading | Spread at entry time in the grid, so the planner sees the months immediately; store the leaf values. | D-14, D-28 |
| Zero vs. blank | Keep them distinct: a stored 0 is a plan value, and an absent row is "not planned". | D-18, TD-07 |
| Lock on every verb | Locks must cover insert, update **and** delete, in the database. | D-22 |
| Signs | One convention, enforced at the boundary (sync in / export out), with a test that revenue is favourable when it is above plan. | D-26, F-07 |
| Conflict granularity | Per cell, not per sheet. | D-19 |
