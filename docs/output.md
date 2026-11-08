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

Key: `version_code, period, entity_code, cost_center_code, account_code, measure`. A blank plan cell has no row; a zero is a row with `amount = 0`.

A run replaces all rows of its round. With the scope "approved cost centers", only approved cost centers are present; with "whole round", every current value is, and `status` tells them apart.

## `dim_version`

| Column | Meaning |
|---|---|
| `code`, `name`, `kind` | the round (`budget` or `forecast`) |
| `fiscal_year` | its fiscal year |
| `published_at`, `scope` | the last publish of the round and its scope (`approved` or `all`) |

## `dim_cost_center`, `dim_account`

Members of the main hierarchy as Planlayer holds them at the last publish, including local renames and moves, groups included.

| Column | Meaning |
|---|---|
| `code`, `name` | the member |
| `parent_code`, `depth` | its place in the hierarchy (depth 0 is a root) |
| `level_1_code`, `level_1_name` … `level_6_code`, `level_6_name` | the path from the root down to the member |
| `entity_code` | cost centers: their entity |
| `account_type` | accounts: e.g. `revenue`, `cost` |

## `v_plan_cost_center`

The fact table joined with the names of round, cost center and account, for tools that want one flat view.

## Rebuilding

The output is derived from Planlayer's app store. `planlayer republish` rebuilds it from every round's last publish run, e.g. after restoring a backup.
