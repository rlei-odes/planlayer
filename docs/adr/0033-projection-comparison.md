# 0033: Projection comparison columns

- **Status:** Accepted
- **Amends:** ADR 0007 (comparison columns), ADR 0015 (comparison columns in a forecast)
- **Date:** 2026-11-27

## Context

Many companies don't run forecasts. A budget for next year is prepared in the autumn, when the
current year has nine or ten months booked. Prior-year actuals are then a year old, and the
current year's actuals to date are not a full year. Planners compare with a quick
extrapolation instead: nine months of actuals scaled up to twelve.

## Decision

A round's comparison columns can include a **projection**: actuals of a year, counted through a
month, extrapolated to the full year. Its label is "Projection 2026".

- **Which year:** a forecast with closed months projects its own year from them, through its
  actuals through (ADR 0015). Any other round, a budget in particular, projects the prior fiscal
  year: the year the budget is prepared in.
- **Through which month:** in a budget, the controller sets the month on the column
  (`comparison.through`, a month of the prior fiscal year). The setup proposes the latest month
  with synced actuals. Without a month, the column counts through the latest actuals.
  A round set up like another moves the month by the years between them.
- **The months:** the counted months show their actuals. The months after continue them:
  - amounts and hours at their monthly average. The rest of the year is spread evenly to the
    cent, so the year is the counted months times twelve over their number;
  - FTE and headcount at the last counted month's level.

  A blank counted month counts as zero in the average. A line without actuals in any counted
  month stays blank.
- The column's tooltip says through which month it counts and how the months after are filled.

## Reasons

- **Months, not only a year total:** comparison columns hold twelve months on every sheet
  (cost centers, capacity, workforce). Filling the months after keeps the year a plain sum and
  lets quarters and month columns compare.
- **Average for flows, last level for stocks:** an amount booked over nine months continues at
  its pace. A headcount averaged over the year to date undercounts a team that grew. Scaling a
  headcount total by twelve over nine is meaningless.
- **The controller sets the month in a budget:** the latest synced month is often only partly
  booked. Extrapolating it would carry its gap into every month after. A forecast already has
  that choice in its actuals through, so it gets no second one.

## Consequences

- Migration 0003: `comparison.through` and the kind `projection` in `ck_comparison_kind`.
- Linear extrapolation stretches one-off bookings. Insurance billed in January raises every
  month after, and a December bonus is missing. The column is a rough reference, and its
  tooltip says how it is made.
