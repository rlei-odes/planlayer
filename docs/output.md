# Published plan data (`planlayer_out`)

Planlayer writes plan values to its output schema (default `planlayer_out`) only when the controller publishes a planning round ([ADR 0011](adr/0011-publishing.md)). BI tools and dbt read these objects like any other warehouse table; BI users get `SELECT` through the role from `deploy/sql/install.sql`.

Everything is keyed by the source's **codes**, never by Planlayer's internal ids, so plan data joins to warehouse dimensions directly. Amounts are natural-positive (revenue and costs both positive) until export sign rules are configured.

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
| `amount` | numeric(18,2) | plan value in group currency |
| `status` | text | the cost center's workflow status at publish: `not_started`, `in_progress`, `submitted`, `approved` |
| `published_at` | timestamptz | when the run wrote the row |
| `publish_run_id` | bigint | the run, as recorded in Planlayer |
| `origin` | text | `plan`: the round's own value; `actual`: a forecast's closed month, as the actuals stood at publish ([ADR 0015](adr/0015-forecasts-and-quarters.md)) |

Key: `version_code, period, entity_code, cost_center_code, account_code, measure`. A blank plan cell has no row; a zero is a row with `amount = 0`.

A forecast's rows cover the whole year: its closed months as `actual` rows (every account with actuals, for the cost centers in the publish scope), its open months as `plan` rows. The sum over a version is the full-year forecast; filter `origin = 'plan'` for the forecast months only. Budgets have only `plan` rows. Values are stored by month in every round, also in rounds planned by quarter.

Credits are rows like any other, natural-positive: activity credits from capacity planning on the activity types' accounts, and cost forwarding (ADR 0018) on each forwarding's two accounts, the charge on the receivers and the credit on the source. A cost center's net cost subtracts its credit accounts, until the export sign rules (E-12) publish credits with their sign.

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

## `dim_work_center`, `dim_activity_type`

Work centers with their `cost_center_code`, and activity types with the `account_code` their credits go to, as Planlayer holds them at the last publish.

## `v_plan_capacity`

`fact_plan_capacity` joined with the names of round, cost center, work center and activity type.

## `v_plan_cost_center`

The fact table joined with the names of round, cost center and account, for tools that want one flat view, with `origin` as its last column.

## Rebuilding

The output is derived from Planlayer's app store. `planlayer republish` rebuilds it from every round's last publish run, e.g. after restoring a backup.
