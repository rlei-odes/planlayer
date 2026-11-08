# 0007: Planning rounds, modules and entry forms

- **Status:** Accepted
- **Closes:** D-04
- **Date:** 2026-11-08

## Context

The controller sets up each fiscal year's planning from modules (T-24): what is planned (cost centers, sales; later headcount) and in what form. Planners work on one planning unit at a time (T-25). Values are always stored as monthly leaf values (ADR 0001, ADR 0003). Cost centers are enabled per round, and their owners plan them by default (T-17).

## Decision

### Objects

| Object | What it holds |
|---|---|
| **Planning round** | One plan version (budget or forecast) for one fiscal year: name, deadline, status (`setup` → `open` → `closed`, ADR 0008), and the round it was copied from. A fiscal year can have several rounds (a budget, then forecasts). |
| **Round module** | A planning module enabled in the round: `cost_center` and `sales` in v1, later `headcount`. Each module stores into its planning area's fact table (ADR 0001) and has its own settings below. |
| **Enabled members** | The planning units of a module in the round, e.g. the cost centers to plan, each enabled or disabled. All cost centers valid in the fiscal year when the round is created start enabled, and the controller disables the rest. A cost center that first arrives with a later sync isn't planned until the controller enables it; the status page lists it as new. |
| **Entry form** | How a line is entered, set per module as a default, per **account group**, and optionally per single account. The most specific setting wins. |
| **Comparison columns** | The reference columns shown next to the plan, per module, in order: prior-year actuals (default), another version (e.g. the last budget), or the current year's actuals to date. |
| **Seasonality profiles** | Named, installation-wide profiles (D-28, TD-17), entered as percentages or relative numbers, chosen by entry forms. |
| **Sheet accounts** | Accounts a planner added to a cost center's sheet in a round. |
| **Hidden accounts** | Accounts the controller excludes from a module in a round, e.g. an account being phased out that still has actuals. |

### Entry forms

| Form | Planner types | Stored | Months in the grid |
|---|---|---|---|
| `monthly` | months; a year total spreads by the line's basis | as typed | editable |
| `annual` (full year only) | the year total | spread evenly | hidden |
| `annual_even` | the year total | spread evenly | shown, read-only |
| `annual_profile` | the year total | spread by the chosen seasonality profile | shown, read-only |
| `annual_reference` | the year total | spread by the chosen reference (prior-year actuals by default, or another version) | shown, read-only |

The spread basis of a `monthly` line is the module's default reference (prior-year actuals) unless the line's entry form names another. The engine's fallbacks are the spreading defaults below.

### Which accounts a sheet shows

A cost center's sheet shows the accounts of the module's account groups that have comparison values or plan values for that cost center, plus the sheet accounts the planner added. The planner can add any account of the module's account groups.

Hidden accounts are the exception:
- They appear on no sheet, even with comparison values, and planners can't add them. The store refuses edits to them.
- An account can only be hidden while it has no plan values in the round. The setup page shows which cost centers still have values, and offers to clear them (an audited change batch), so no invisible values reach the published plan.
- Hiding applies to one round. A copied round inherits the hidden accounts, and the controller can unhide them.

### Defaults

- **New rounds:** a new round copies its setup (modules, entry forms, comparisons, disabled members) from a chosen earlier round, or starts from the module defaults: every group `monthly`, prior-year actuals as the only comparison.
- **Spreading fallbacks** (D-28): a negative reference value counts as 0. A line whose reference is all zero or missing is spread evenly, and the grid marks the line ("spread evenly: no reference values").
- **Changing an entry form during a round** changes how new input is entered. Stored values stay until a line is entered again.

## Reasons

- **Account groups** are how controllers think about plannable detail ("travel only as a year total"). An override per account covers exceptions without making every account a setting.
- **Storing months for every form** keeps one fact grain and one publish shape. Full-year-only lines still give BI monthly values.
- **Short sheets:** comparison values, plan values and manual additions cover what a cost center actually uses. Showing all ~150 accounts would bury the relevant 20.
- **New cost centers wait for the controller:** what is planned in a running round stays the controller's decision; the status page keeps a new cost center from being overlooked.
- **Hiding:** accounts are phased out while their actuals stay in history. Hiding keeps them off the sheets without touching the source.
- **Copying the previous round's setup** makes a new year's preparation a review, not a rebuild.

## Consequences

- The grid API (D-18) returns per row its entry form, editable columns, spread basis, and whether a fallback was used. The planning page (E-05) renders and enforces that. The store refuses edits a form doesn't allow.
- New tables in `planlayer_app`: `planning_round`, `round_module`, `round_member`, `entry_form`, `comparison`, `seasonality_profile`, `sheet_account`, `hidden_account`. The current single `version.fiscal_year` becomes the round's.
- The yearly setup page (E-03) edits these objects, with copy-from-round. Seasonality profiles get their page in E-12.
- Headcount planning (later) is a new module with its own planning area and entry forms. It plugs into rounds, enabled members, comparisons and workflow without changes to them.
