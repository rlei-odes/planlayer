# Scenario: preparing Budget 2027

A walkthrough on the standard test data (Nordwerk, `tools/gen_testdata`) for preparing the next planning round: the admin keeps the source working across years, then the controller sets up Budget 2027 from Budget 2026. Each step names what to do and what to check. It exercises [ADR 0014](../adr/0014-preparing-a-round.md).

Starting point: round B2026 open, actuals January 2023 to June 2026. The warehouse already holds the next year's master data changes:

| In the source | What it is |
|---|---|
| DE1290 *Additive Manufacturing* (Pump Production) | a cost center created ahead of 2027 (valid from 2027-01-01), without actuals |
| DE1100 *Shift Management* | closed at the end of 2026 (valid to 2026-12-31); it has actuals |
| 6950 *Cloud subscriptions* (IT and software) | a new account nobody has booked on |

The other cases are decisions the controller makes in the UI, on existing data.

| Case | Where |
|---|---|
| A cost center that is no longer budgeted, although it still exists | PL1080 *Training Centre PL* |
| A new cost center without prior-year values | DE1290 *Additive Manufacturing* |
| Accounts phased out that still carry actuals and budget values | 6670, 6680, 6690 (*Travel expenses 08–10*) |
| A new account that should appear on sheets | 6950 *Cloud subscriptions* |
| A cost center that plans only the accounts relevant to it | DE1240 *Finance*: personnel, travel, IT, other operating expenses; no material, freight, supplies or facilities |
| Manufacturing cost centers plan their own set | Pump Production and Valve Production |

## 1. Admin: source queries

Log in as `admin@`, open *Master data → Sources*.

1. The cards are grouped by what needs them: *Master data*, *Cost center planning* (actuals), *Capacity planning* (activity types, work centers, rates, *work center actual hours*).
2. Open *Actuals*. The query is year-independent. Change it to filter in the warehouse:
   `SELECT period, entity_code, cost_center_code, account_code, amount FROM gold.v_gl_actuals_natural WHERE period BETWEEN :period_from AND :period_to`
   *Preview as synced* shows rows (the preview reads every year). Save.
3. Do the same for *Work center actual hours* (`gold.v_capacity_actuals`).
4. Optional, to see the safety net: change the actuals query to `… WHERE extract(year FROM period) = 2026`, open *Sync*, set the years to 2025–2026 and preview. The table shows 2025 as stored but not delivered, with a warning that the sync would remove those cells. Restore the query.

## 2. Admin or controller: sync

*Master data → Sync*, leave the years empty, *Preview sync*.

- The window is 2025–2026 (the open round B2026 and the year before), with stored and delivered cells per year equal.
- New: cost center DE1290, account 6950. Changed: cost center DE1100 (its end date).
- Run the sync. Years before 2025 are not touched.

## 3. Controller: create Budget 2027

Log in as `controller@`, *Setup → New round*.

1. Kind *Budget*, fiscal year 2027, code `B2027`. *Set up like* proposes Budget 2026, the latest budget.
2. The checklist carries entry forms and comparisons, adds Budget 2026 as a comparison column, and carries the cost center scope, accounts, capacity and sheets. Tick *Plan values* and set the uplift, e.g. 3 %: the example shows 1,000 becoming 1,030.
3. Create. The page switches to Budget 2027, in status *setup*.

Check: DE1100 *Shift Management* is not part of the round (not valid in 2027).

## 4. Controller: cost centers

*Cost centers* tab.

- DE1290 is marked *new*: planned, without figures in either column.
- Filter *New* shows it alone; *No longer delivered* is empty unless the source dropped a member.
- Switch PL1080 *Training Centre PL* off: the round no longer plans it.

## 5. Controller: accounts and account sets

*Accounts* tab.

1. Switch 6670, 6680 and 6690 off (not planned). The columns show what they carried: Actual 2026 and Budget 2026.
2. Add the sets *Administration* (other operating income, personnel, travel, IT, facilities, other operating expenses, depreciation, plus 6950), *Accounting* (personnel, travel, IT, other operating expenses, plus 6950) and *Manufacturing* (direct material, inbound freight, personnel, supplies, travel, IT, facilities, depreciation). Tick account groups; their accounts show as included through the group.
3. On the *Cost centers* tab, assign *Administration* to the node *Corporate*, *Accounting* to DE1240 *Finance*, and *Manufacturing* to *Pump Production* and *Valve Production*. Cost centers below show the inherited set, e.g. "Administration · from DIV_CORP".
4. Save. The values carried for the not planned accounts, and for accounts outside each cost center's set, are cleared in the same save.

## 6. Controller: the sheets

*Planning*, round Budget 2027.

- **DE1240 Finance:** personnel, travel, IT and other operating expenses are listed, with or without values, and 6950 appears empty, ready to plan. Accounts outside *Accounting* that carry actuals or Budget 2026 values (e.g. other operating income) show read-only, marked *Not planned*, with their comparisons; group totals include them. *Add account* offers only the set's accounts.
- **Travel expenses 08–10:** read-only, *Not planned*, with Actual 2026 and Budget 2026; no plan values.
- **DE1290 Additive Manufacturing:** the *Manufacturing* accounts, all empty: a sheet to start from although it has no history.
- **Plan values:** carried lines show Budget 2026 + 3 % in 2027's months; the status stays *Not started*.
- The controller can still edit while the round is in setup; planners see it read-only until it is opened on the status page.

## 7. Open the round

*Status → Open round*. A planner (e.g. `anna.keller@`) now edits their sheets; the first value moves their cost center to *In progress*.

## Cleaning up

To run the scenario again, delete round B2027 and its rows (development only), restore the sources' queries if changed, and re-run `uv run gen-testdata --variant standard` if the warehouse was changed by hand.
