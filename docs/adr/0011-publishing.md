# 0011: Publishing plan values

- **Status:** Accepted
- **Closes:** D-12; the `planlayer_out` naming part of F-05
- **Date:** 2026-11-08
- **Amended by:** [0015](0015-forecasts-and-quarters.md): a forecast's closed months are published as its actuals, marked by an `origin` column

## Context

Plan values reach BI only through an explicit publish by the controller (T-21). Planlayer owns the output schema `planlayer_out`, which may sit in another database than the app store, so a publish can't be one transaction across both (T-14). Values are stored natural-positive and converted to the target's signs on the way out (T-7). Workflow status lives per round × cost center (ADR 0008).

## Decision

### What a run publishes

- A run publishes one planning round's plan values. Actuals aren't published: they are already in the warehouse.
- **Scope, chosen per run:** the approved cost centers only (the default), or the whole round.
- Either scope covers only the **cost centers the round plans**. Values on a cost center switched off in the round stay in the app store, read only, but are not published, and a forecast publishes no actuals for it. A forecast that should carry its actuals plans it, with zeros if nothing else.
- A run **replaces the round's rows** in the output with exactly the selected set. A cost center that was published while approved and has since been reopened disappears from the output in an approved-only run; the preview shows that.
- Signs are converted with the export sign rules (E-12). Until those exist, values are published natural-positive.

### Output tables (`planlayer_out`)

Tables and views are snake_case with stable, documented column names, keyed by **codes**, never by Planlayer's internal ids:

| Object | Content |
|---|---|
| `fact_plan_cost_center` | one row per round × cost center × account × month × measure: `version_code`, `fiscal_year`, `period` (first day of month), `entity_code`, `cost_center_code`, `account_code`, `measure`, `amount` (`numeric(18,2)`), `status` (the cost center's workflow status at publish), `published_at`, `publish_run_id` |
| `dim_version` | code, name, kind, fiscal year, last publish time and scope |
| `dim_cost_center`, `dim_account` | code, name, parent code, and level columns of the main hierarchy; accounts also carry their type |
| `v_plan_cost_center` | the fact joined with the dimension names, for BI tools that want one flat view |

The documentation of these objects is published as `docs/output.md` with the first implementation.

### Preview, then run

- **Preview (dry run):** computes the rows the run would write and compares them, by key, with what the output holds for the round now. It shows rows added, changed (old → new amount), removed and unchanged, per cost center and in total. Nothing is written.
- **Run:** recomputes the same rows. If they differ from the preview (someone saved in between), the run is refused and the controller previews again. Otherwise it replaces the round's rows in one transaction on the output connection, then records the publish run in the app store: round, scope, actor, time, counts, and a fingerprint of the rows.
- **Idempotent:** repeating a run with unchanged data changes nothing. If recording the run fails after the output was written, the run is simply repeated.
- **Republish all** rebuilds every published round from the recorded runs' scopes, e.g. after restoring the app store (D-30).
- Only the controller previews and runs publishes.

### Files

The same rows, for the same scope, can be downloaded as **CSV** (UTF-8, comma-separated, dot decimal, header row, amounts as plain decimals, blank cells omitted) or **Parquet** (amounts as `decimal(18,2)`). Each published table of plan values downloads as its own file with that table's columns, the run's time and id left out: the amounts, planned hours, investments and their cash out. A download writes nothing and records nothing.

## Reasons

- **Per-run scope:** approved-only keeps drafts out of BI by default, while a whole-round publish serves interim reporting. The status column lets BI tell them apart.
- **Planned cost centers only:** the output matches the status page, and a forecast's plan and actual months cover the same cost centers, so its full year adds up. A cost center switched off once it was approved doesn't stay in the approved scope through its old status.
- **Replace per round** is what makes runs idempotent and safe across databases. It also means the output always equals one consistent selection, never a mix of runs.
- **Codes, not ids:** BI joins plan data to warehouse dimensions by the source's codes. Planlayer's ids are internal and change on a rebuild.
- **Preview before writing:** publishing overwrites what BI reads. The controller sees every added, changed and removed row first, and the refusal on a changed result means the run does exactly what was previewed.
- **Files** cover installations or one-off analyses that don't read `planlayer_out`, without a second export path: they are the same rows.

## Consequences

- New app store table `publish_run`. The output adapter (ADR 0004) gains "read the round's current rows" for the preview, next to "replace a partition".
- E-10 builds preview, run, the run history and the downloads; the setup page links to it per round.
- Customer-owned target tables stay out of v1: Planlayer writes only `planlayer_out` (T-14).
