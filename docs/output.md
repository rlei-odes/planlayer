# Published plan data (`planlayer_out`)

Planlayer writes plan values to its output schema (default `planlayer_out`) only when the controller publishes a planning round ([ADR 0011](adr/0011-publishing.md)). BI tools and dbt read these objects like any other warehouse table; BI users get `SELECT` through the role from `deploy/sql/install.sql`.

Everything is keyed by the source's **codes**, never by Planlayer's internal ids, so plan data joins to warehouse dimensions directly. Amounts are natural-positive in `amount` (revenue, costs and credits all positive), and signed by the installation's convention in `amount_signed` ([ADR 0019](adr/0019-published-signs.md)).

## `fact_plan_cost_center`

One row per round × cost center × account × month × measure.

| Column | Type | Meaning |
|---|---|---|
| `version_code` | text | the planning round, e.g. `B2026` |
| `fiscal_year` | integer | the round's fiscal year (named after the calendar year it starts in) |
| `period` | date | first day of the month |
| `entity_code` | text | legal entity; `-` for cost centers without one |
| `cost_center_code` | text | cost center |
| `account_code` | text | account |
| `measure` | text | `amount` |
| `amount` | numeric(18,2) | plan value in group currency, natural-positive |
| `amount_signed` | numeric(18,2) | the same with the sign convention set on the *Settings* page: `ledger` (costs +, revenue and credits −) or `result` (revenue and credits +, costs −); empty in rows of runs before ADR 0019 |
| `status` | text | the cost center's workflow status at publish: `not_started`, `in_progress`, `submitted`, `approved` |
| `published_at` | timestamptz | when the run wrote the row |
| `publish_run_id` | bigint | the run, as recorded in Planlayer |
| `origin` | text | `plan`: the round's own value; `actual`: a forecast's closed month, as the actuals stood at publish ([ADR 0015](adr/0015-forecasts-and-quarters.md)) |

Key: `version_code, period, entity_code, cost_center_code, account_code, measure`. A blank plan cell has no row; a zero is a row with `amount = 0`.

A forecast's rows cover the whole year: its closed months as `actual` rows (every account with actuals, for the cost centers in the publish scope), its open months as `plan` rows. The sum over a version is the full-year forecast; filter `origin = 'plan'` for the forecast months only. Budgets have only `plan` rows. Values are stored by month in every round, also in rounds planned by quarter.

Credits are rows like any other: activity credits from capacity planning on the activity types' accounts, and cost forwarding (ADR 0018) on each forwarding's two accounts, the charge on the receivers and the credit on the source. Their accounts have the role `credit` in `dim_account`, so `sum(amount_signed)` over a cost center is its net cost (ledger) or its result (result).

A run replaces all rows of its round. With the scope "approved cost centers", only approved cost centers are present; with "whole round", every current value is, and `status` tells them apart.

## `fact_plan_capacity`

Planned hours of productive cost centers ([ADR 0016](adr/0016-capacity-in-rounds-and-forecasts.md)): one row per round × cost center × work center × activity type × month × measure.

| Column | Type | Meaning |
|---|---|---|
| `version_code`, `fiscal_year`, `period`, `entity_code`, `cost_center_code` | | as in `fact_plan_cost_center` |
| `work_center_code` | text | work center |
| `activity_type_code` | text | activity type |
| `measure` | text | `hours` |
| `amount` | numeric(18,2) | hours |
| `status`, `origin`, `published_at`, `publish_run_id` | | as in `fact_plan_cost_center`; `actual` rows are a forecast's closed months, the actual hours of the cost centers productive in the round |

Key: `version_code, period, entity_code, cost_center_code, work_center_code, activity_type_code, measure`. The credits that hours × rates give a cost center are in `fact_plan_cost_center`, as amounts on the activity type's account. The file downloads hold the amounts only.

## `fact_plan_workforce`

FTE and headcount at month end ([ADR 0020](adr/0020-workforce-planning.md)) of rounds that plan their workforce: one row per round × cost center × staff type × staff category × month × measure. Positions are not published: they carry names; the aggregate is what BI reports.

| Column | Type | Meaning |
|---|---|---|
| `version_code`, `fiscal_year`, `period`, `entity_code`, `cost_center_code` | | as in `fact_plan_cost_center` |
| `staff_type_code` | text | staff type, e.g. `EMP`, `FRL` |
| `staff_category_code` | text | staff category, e.g. a job family |
| `measure` | text | `fte` or `headcount` |
| `amount` | numeric(18,2) | the level at the month's end |
| `status`, `origin`, `published_at`, `publish_run_id` | | as in `fact_plan_cost_center`; `actual` rows are a forecast's closed months, HR's month-end figures |

Key: `version_code, period, entity_code, cost_center_code, staff_type_code, staff_category_code, measure`. Values are levels, not flows: a year is its last month, never the sum of its months. A month without a row is blank, not zero; a line whose people have left shows 0.

## `dim_version`

| Column | Meaning |
|---|---|
| `code`, `name`, `kind` | the round (`budget` or `forecast`) |
| `fiscal_year` | its fiscal year |
| `published_at`, `scope` | the last publish of the round and its scope (`approved` or `all`) |
| `actuals_through` | a forecast's last closed month (first day of the month); empty for budgets |

## `dim_cost_center`, `dim_account`

Members of the main hierarchy as Planlayer holds them at the last publish, including local renames and moves, groups included.

| Column | Meaning |
|---|---|
| `code`, `name` | the member |
| `parent_code`, `depth` | its place in the hierarchy (depth 0 is a root) |
| `level_1_code`, `level_1_name` … `level_6_code`, `level_6_name` | the path from the root down to the member |
| `entity_code` | cost centers: their entity |
| `account_type` | accounts: e.g. `revenue`, `cost` |
| `role` | leaf accounts: `cost`, `revenue` or `credit` (credited by capacity planning or a forwarding), which decides the sign of `amount_signed` |

## `dim_work_center`, `dim_activity_type`

Work centers with their `cost_center_code`, and activity types with the `account_code` their credits go to, as Planlayer holds them at the last publish.

## `dim_staff_type`, `dim_staff_category`

Staff types with `external` (external staff, e.g. freelancers), and staff categories, as Planlayer holds them at the last publish.

## `fact_plan_capex`

Investments of rounds that plan capex ([ADR 0028](adr/0028-capex-planning.md)): one row per investment. A large project has no row of its own; its lines carry its id and name.

| Column | Type | Meaning |
|---|---|---|
| `version_code`, `fiscal_year` | | as in `fact_plan_cost_center` |
| `investment_id` | integer | Planlayer's number of the investment, as on the *Investments* page |
| `investment_name` | text | |
| `project_id`, `project_name` | integer, text | the large project it is a line of; empty for a single investment |
| `entity_code`, `cost_center_code` | text | the cost center, and its entity (`-` without one) |
| `capex_type_code` | text | investment type; empty when not set |
| `priority` | smallint | 1 to 3; a line's is its project's |
| `start_month` | date | first day of the month it is taken into use; empty when not set |
| `amount` | numeric(18,2) | natural-positive; empty when not set |
| `confirmation` | text | `open`, `confirmed` or `declined`; a line's is its project's |
| `confirmed_by`, `confirmed_at` | text, timestamptz | who confirmed or declined, and when |
| `published_at`, `publish_run_id` | | as in `fact_plan_cost_center` |

Key: `version_code, investment_id`. With the scope "approved cost centers", a run publishes the confirmed investments only; with "whole round", every investment, declined ones included: filter on `confirmation`.

## `fact_plan_cash_out`

The cash-out schedule of the published investments: one row per investment and period with an amount. Periods are months, or a quarter's first month in rounds planned by quarter; the first month of the next fiscal year holds what is paid in that year as a whole. The rows of an investment sum to at most its amount; what is missing isn't scheduled yet.

| Column | Type | Meaning |
|---|---|---|
| `version_code`, `fiscal_year`, `investment_id` | | as in `fact_plan_capex` |
| `period` | date | first day of the period |
| `entity_code`, `cost_center_code`, `capex_type_code`, `confirmation` | | the investment's, repeated for grouping |
| `amount` | numeric(18,2) | cash out in the period |
| `published_at`, `publish_run_id` | | as in `fact_plan_cost_center` |

Key: `version_code, investment_id, period`.

## `dim_capex_type`

Investment types with `asset_account` (the balance sheet account an investment of the type is capitalised on), `useful_life` in years and `active`, as Planlayer holds them at the last publish.

## `v_plan_capex`, `v_plan_cash_out`

`fact_plan_capex` joined with the names of round, cost center and investment type (with asset account and useful life); `fact_plan_cash_out` joined with the same and the investment's and project's names.

## `v_plan_workforce`

`fact_plan_workforce` joined with the names of round, cost center, staff type (and `external`) and staff category.

## `v_plan_capacity`

`fact_plan_capacity` joined with the names of round, cost center, work center and activity type.

## `v_plan_cost_center`

The fact table joined with the names of round, cost center and account, for tools that want one flat view, ending with `origin`, `amount_signed` and the account's `role`.

## Rebuilding

The output is derived from Planlayer's app store. `planlayer republish` rebuilds it from every round's last publish run, e.g. after restoring a backup.
