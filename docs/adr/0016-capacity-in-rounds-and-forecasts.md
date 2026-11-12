# 0016: Capacity planning in prepared rounds, forecasts and quarters

- **Status:** Accepted
- **Amends:** ADR 0013 (capacity planning); applies ADR 0014 (preparing a round) and ADR 0015 (forecasts, quarter grain) to the capacity sheet
- **Date:** 2026-11-12

## Context

Capacity planning (ADR 0013) was built for an open budget round. Preparing a round (ADR 0014) and forecasts and quarter grain (ADR 0015) changed how rounds are created and entered, on the cost center sheet only. A productive cost center's two sheets are one plan with one status, so they must behave alike. Credits are derived from hours, so a round that carries plan values without hours carries a cost plan without its credits.

## Decision

### The controller prepares capacity in setup

While a round is in `setup`, controllers may write its planned hours, plan rates and capacity lines, as they write its plan values (ADR 0014). Planners can't. The database locks already allowed this; the capacity sheet now does too.

### Planned hours carry with plan values

The *Plan values* item of a new round's checklist also copies the source round's planned hours, month by month, for the productive cost centers the new round carries (*Capacity*). In a forecast, only the open months are copied.

- The uplift applies to amounts, not to hours. A higher price is a planning assumption about rates.
- Credits are not copied. They are recomputed from the carried hours and the new round's plan rates in the same change batch, so a changed rate flows through.
- The round's creation record counts the hour cells copied next to the plan values.

### The capacity sheet follows the round

- **Forecast:** closed months show the synced actual hours, read only, without row versions, and capacity writes no hours into them. A year total typed is the full year: the open months take the total minus the actual hours to date, spread by the line's basis over the open months. A credit row's closed months show the cost center's actuals on the credit account.
- **Quarter grain:** the sheet shows Q1 to Q4. A quarter of hours spreads over its three months by the line's basis, renormalised to the quarter.
- **Comparison columns:** the sheet shows the round's comparison columns in hours. Prior-year actuals are the prior year's actual hours. The year's actuals are actual hours, cut at the closed months in a forecast. A version shows that round's planned hours for the cost center, and a forecast its full year (actual hours to date plus planned hours). Credit rows show the same columns as amounts on the credit account.

### Planned hours are published

A publish also writes `fact_plan_capacity`: one row per round × cost center × work center × activity type × month, measure `hours`, with the cost center's status and `origin` (`plan` or, in a forecast's closed months, `actual`). `dim_work_center` and `dim_activity_type` name the codes, and `v_plan_capacity` joins them. Credits stay in `fact_plan_cost_center` as amounts. The preview counts the hour rows; the file downloads keep the amounts only.

## Reasons

- **Hours with plan values:** carrying the cost plan without the hours behind its credits would leave a productive cost center's carried plan incomplete. One checklist item keeps the two halves together.
- **No uplift on hours:** an uplift models prices. Capacity in hours is a volume, and its price is the rate, which the round keeps per line.
- **Recomputed credits:** credits are derived values (ADR 0013). Recomputing them from carried hours and the new round's rates keeps them consistent with the sheet.
- **One behaviour for both sheets:** the cost center has one status and one plan. Closed months, the full-year total, quarters and comparisons mean the same on both sheets.
- **A table of hours:** BI reads capacity utilisation and rates from hours. Deriving hours back from credits would need the rates per line.

## Consequences

- `CarryOver.record` keeps `hours`; the setup's creation record shows it.
- The capacity sheet API returns grain, closed months and comparison columns instead of the fixed prior-year hours. An hours edit names a month, a quarter, or neither.
- New output objects: `fact_plan_capacity`, `dim_work_center`, `dim_activity_type`, `v_plan_capacity` (`docs/output.md`).
