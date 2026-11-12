# 0015: Forecast rounds, closed months and quarter grain

- **Status:** Accepted
- **Closes:** the closed-period part of D-03; E-03's open periods per round and the year-to-date-plus-forecast column
- **Amends:** ADR 0007 (rounds, entry forms), ADR 0011 (publishing), ADR 0014 (carry-over)
- **Date:** 2026-11-12

## Context

A forecast re-plans the rest of a fiscal year once part of it is booked. In a "6+6" forecast, January to June are actuals and July to December are planned again. Together they give the expected full year. Forecasts follow the year's budget (plan → forecast → plan → forecast). The open months start from the budget's values or the last forecast's, and the full-year forecast is the reference later rounds compare with.

A 6+6 is usually prepared in June, before June is booked completely. The controller refreshes the actuals as the month closes.

Many companies plan, or forecast, by quarter rather than by month. They still need monthly values for BI and for seasonality.

## Decision

### A round's grain: months or quarters

A round is planned by **month** (the default) or by **quarter**. The controller sets it when creating the round and can change it while the round isn't closed.

- Values are stored as months in either grain (ADR 0001), so changing the grain changes how the sheet is entered, not what is stored.
- In quarter grain the sheet shows Q1 to Q4 and the year. A quarter is three fiscal months: Q1 is the fiscal year's first three months.
  - A `monthly` line takes quarter totals, and each spreads over its three months by the line's basis: its profile, its reference or evenly, renormalised to the quarter. A quarter whose basis is zero in all three months spreads evenly, like the year fallback (ADR 0007).
  - The year forms show their quarters read-only. A year total spreads over the months as in month grain.

### Forecasts close months

A forecast round has **actuals through**: the last closed month, set by the controller. The setup proposes the latest month that holds synced actuals.

- **Closed months show actuals in the plan cells**, read-only and styled as actuals. They are read live from the actuals version, not copied into the round. A sync therefore refreshes them. The year column is actuals to date plus the forecast for the open months.
- **Grain limits:** in quarter grain only quarter ends can be closed (3+9, 6+6, 9+3), so a quarter is either all actuals or all forecast. An odd profile such as 7+5 needs month grain.
- **A year total typed in a forecast is the full year.** The rest, the typed total minus the line's actuals to date, spreads over the open months by the line's basis, renormalised to the open months. Clearing the year total clears the open months only.
- **The store refuses plan values in closed months:** a statement trigger on plan values refuses "the month is closed in this forecast", for every writer, the controller in setup included. Capacity planning writes credits and planned hours only into open months.
- **Moving actuals through later** closes months that may hold plan values:
  - in a round in setup, those values are cleared in the same save;
  - in an open round, the save is refused and names the cost centers, as for accounts not planned (ADR 0014).

  Moving it earlier opens months, which start blank.
- A forecast without actuals through has no closed months, and plans the whole year like a budget.
- **Workflow:** forecasts are submitted and approved per cost center like budgets (ADR 0008).

### Comparison columns in a forecast

- **Actuals of the round's year** stop at the closed month, even when a later sync has already brought the next month: the column is the year to date, labelled e.g. "YTD Jun 2026", or "YTD Q2 2026" in quarter grain. Prior-year actuals are the full prior year, as before.
- **A forecast as a version column** shows its full year in any round that compares with it: its actuals to date plus its forecast. The same holds when a line spreads like a forecast (`annual_reference`). This is the "actuals to date + forecast for the rest" column. A budget for next year that compares with the last forecast gets the expected full year as its reference.

### Refreshing actuals

The forecast's setup shows its actuals through, the latest synced month, and a **Refresh actuals** action. The action runs the actuals sync for the round's fiscal year, with the sync's preview and confirmations (ADR 0012, ADR 0014). Controllers may sync already, so the action needs no new right.

### New forecasts

The *New round* step for a forecast asks for the grain and actuals through. Its default source round is the latest forecast of the fiscal year, else the latest budget of the year. The *Plan values* checklist item is on by default and copies the source round's values month by month into the **open months only**. For a source of the same year the months don't move. The uplift applies as for budgets.

### Publishing a forecast

`fact_plan_cost_center` gets an **`origin`** column: `plan` for the forecast's own values, `actual` for the closed months. A publish writes the closed months' actuals as `actual` rows, frozen at that moment, for the cost centers in the publish scope (all planned cost centers, or the approved ones), on every account with actuals. The sum over a version is the full-year forecast, and BI filters `origin = 'plan'` for the planned months only. Budgets have only `plan` rows. `dim_version` gets `actuals_through`. The key is unchanged, because a month is either closed or open.

## Reasons

- **The grain is set per round:** a round is planned one way. FC I as "Q1 actual, Q2 actual, Q3 forecast, Q4 forecast" needs every line on the same columns. Mixed month and quarter columns on one sheet would leave totals and paste ranges ambiguous.
- **Months stay the stored grain:** one fact grain, one publish shape, seasonality kept. A quarter total spreads by the same bases a year total does, so the quarter grain reuses the month logic.
- **Actuals through is the controller's choice:** a month with some bookings isn't closed. Deriving closed months from where actuals exist would close June on its first booking. One setting per round is enough for a forecast and needs no close calendar per entity.
- **Actuals are read live:** a copy of actuals in each forecast would have to be kept in step with every sync. Reading them live makes the refresh a plain sync, and the frozen state lives where it is consumed: the output at publish.
- **The year total is the full year:** planners and controllers talk about the forecast as the full-year figure. Typing it and getting it back is what a sheet should do. A remainder that has to be computed first is error-prone.
- **The current year's actuals are cut at the closed month:** the forecast's actuals and its reference column then agree. A half-booked month beyond the cut would otherwise appear in the reference without being in the forecast.
- **`origin` in one table:** BI gets the full year with a plain sum and still tells forecast from actuals, without a second table or a join to actuals. The actuals are frozen as published, so the published forecast doesn't drift with later syncs.

## Consequences

- Migration 0012: `planning_round.grain` and `planning_round.actuals_through`, and the closed-month trigger `lock_closed_months` on `fact_cost_center`.
- The grid API returns the grain, actuals through and the number of closed months. Closed plan cells carry actuals and no row version. An edit names a month, a quarter, or neither (the year).
- Setup and creation take the grain and actuals through, and the setup returns the latest synced month. `GET /api/actuals/latest` serves the *New round* step.
- The planning page draws quarter columns and closed cells. The setup preview shows both.
- The output gets `origin` and `dim_version.actuals_through`. Both are added to existing output tables when missing (ADR 0011: output objects are created, not migrated).
- Open:
  - the capacity sheet doesn't show actual hours in a forecast's closed months, and stays monthly in quarter grain;
  - a rolling forecast (an automatic window) stays out of v1 (`docs/scope.md`).
