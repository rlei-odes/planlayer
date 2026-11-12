# Scenario: the first forecast of 2026

A walkthrough on the standard test data (Nordwerk, `tools/gen_testdata`) for a "6+6" forecast of fiscal year 2026, planned by month and by quarter. Each step names what to do and what to check. It exercises [ADR 0015](../adr/0015-forecasts-and-quarters.md).

Starting point: Budget 2026 open with plan values on some cost centers, actuals January 2023 to June 2026. June is the latest synced month.

| Case | Where |
|---|---|
| A cost center with budget values to carry | DE1010 (accounts 5140, 5150) |
| A cost center without budget values | DE1280 *Facilities* |
| A year-only line, a seasonal line | *Depreciation*, *IT and software* |

## 1. Controller: create Forecast 6+6 2026

Log in as `controller@`, *Setup → New round*, kind *Forecast*.

1. The step proposes fiscal year 2026, *Actuals through* June 2026 ("6+6 · latest actuals"), code `F2026-6`, name *Forecast 6+6 2026*, and *Set up like* Budget 2026 (the year's last forecast, else its budget).
2. *Plan values* is ticked; the uplift example ends with "into the open months only".
3. *Planned by* *Quarter*: *Actuals through* offers only March, June and September. Choose May under *Month*, then switch to *Quarter*: it moves back to March. Set it back to *Month*, June.
4. Create. *Forms & columns → Round* shows the carried plan values and their cell count, *Planned by* and *Actuals through*, and below them: "6+6: January 2026 to June 2026 show actuals …, Latest synced actuals: June 2026".
5. The preview shows Jan–Jun tinted as actuals, the column *Forecast 2026*, and the note on the full year.

## 2. Controller: refresh actuals

Still on *Round*: *Refresh actuals 2026*.

- The preview line says how many actual cells the source delivers for 2026 and how many are stored. *Cancel* writes nothing.
- *Refresh now* runs the sync for 2026 only and reports the rows written. Sheets show the new actuals on their next load.
- If the sync needs attention (a problem or a match to confirm), the button stays inactive and the note points to *Master data → Sync*.

## 3. The forecast sheet, by month

*Planning*, round Forecast 6+6 2026.

- **DE1010:** January to June headed "Jan · Act" … "Jun · Act", tinted, read only, showing the actuals. July to December hold Budget 2026's values. *Forecast 2026* is actuals plus forecast; its tooltip says so.
- Type a year total on 5140, e.g. 50,000: the open months take 50,000 minus the actuals to date, spread like the line's badge says. The year column shows 50,000.
- A closed month takes no typing; a paste over it leaves it unchanged.
- **DE1280:** closed months show its actuals; the open months are empty, ready to plan.
- *Depreciation* (year only): the closed months show actuals, the open months are hatched.

## 4. Comparison columns

*Setup → Forms & columns → Comparison columns*: add *Actuals of the year*. Save.

- The sheet shows it as "YTD Jun 2026": January to June only, even after a sync brought July.
- In *Budget 2027*, add *Forecast 6+6 2026* as a version column: it shows 2026's full year, actuals to June plus the forecast.

## 5. By quarter

*Setup*: *Planned by* *Quarter*, save.

- The sheet shows *Q1 · Act*, *Q2 · Act*, *Q3*, *Q4*. Type a Q3 value on a monthly line: it spreads over July to September by the line's basis. Back to *Month*, the months hold what was spread.
- *Actuals through* can't move to July while planned by quarter.

## 6. Moving the closed months

While the round is in setup, set *Actuals through* to September and save: July to September now show actuals, and their carried values are gone. Set it back to June: those months are empty.

Open the round on the status page, then try September again: the save is refused and names the cost centers with values in July to September.

## 7. Publish

*Publish*, *Whole round*. The page says the closed months go out as actuals. Run it, then in the warehouse:

```sql
SELECT origin, count(*), sum(amount) FROM planlayer_out.fact_plan_cost_center
WHERE version_code = 'F2026-6' GROUP BY origin;
```

`actual` rows cover January to June for every planned cost center; `plan` rows cover July to December. `dim_version.actuals_through` is 2026-06-01.
