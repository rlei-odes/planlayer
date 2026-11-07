# Budget & Forecast for Claude Code — notes

Source: https://github.com/Enterprise-DNA-OS/budget-forecast-for-claude-code (MIT), cloned to `research/budget-forecast/`.
Size: one 350-line SQL migration, one 300-line Node CLI, a seed file and a smoke test. No front end, by design.

## What it is

A Postgres schema plus a CLI that agent slash-commands call. It targets NZ/AU SMEs (20–500 staff). The model is fixed: **entity × account × month**. There are no cost centers, no products, no hierarchies and no user-defined dimensions. Each entity reports in its own currency, and amounts in different currencies are never added together.

## Data model

| Table | Shape | Notes |
|---|---|---|
| `entities` | country, currency, tax settings | |
| `accounts` | code, name, `type` (revenue/cogs/expense/…/equity), `tag` (cash/debt/interest/depreciation/tax/wages), owner + email | A flat list. Account `type` drives sign and P&L grouping; `tag` drives covenant and EBITDA logic. |
| `versions` | `kind` ∈ actuals/budget/forecast/scenario, `starts_on`, `months` (1–60), `based_on`, `locked`, `approved_by/on` | CHECK constraints: actuals have no date range and can't be locked; locked requires an approver and a date. A partial unique index allows exactly one actuals version. |
| `lines` | version × entity × account × period → `amount`, `source jsonb` | `UNIQUE(version, entity, account, period)`. `source` records provenance (file/row/column, or `copied_from` + shift + uplift). |
| `commentary` | entity × account × period → note, actor | Variance explanations. |
| `activity` | record, record_id, action, actor, `before_data`/`after_data` jsonb, evidence | A generic audit log. Every write requires `--actor`, and most require `--evidence` (a reason). |
| `imports` | filename, sha256, kind, row_count, actor | One row per import that was applied. |
| `settings` | key → numeric | Thresholds, e.g. a variance needs a comment when it is ≥10% **and** ≥5,000. |

**Sign convention:** all amounts are stored as positive natural amounts (revenue and costs both positive). `profit_sign(type)` gives +1/−1/0 and is applied in views for net profit and for favourable/unfavourable variances.

## Mechanisms worth copying

- **The lock is enforced in the database.** The `guard_line()` trigger on `lines` rejects any insert, update or delete in a locked version, rejects moving a line out of a locked version, and rejects plan lines outside the version's month range. The error message says who approved the version and when, and what to do instead ("copy it to a new version").
- **"Current" versions are functions, not flags.** `current_budget()` is the newest approved budget covering the last closed month; `current_forecast()` is the newest forecast. Variance views use these, and answers say which version they used.
- **The closed period is derived from data.** `v_latest` takes each entity's last month that has actuals. Nothing is stored for it.
- **Version copy with shift + uplift** is one `INSERT … SELECT`. The uplift can be limited to revenue or to costs (by account type). Provenance goes into `source`.
- **Roll forecast:** copy the source forecast, delete the closed months for each entity, insert actuals for those months. `v_unrolled` flags a forecast that still holds forecast figures for closed months.
- **Import discipline:**
  - The whole file is resolved before anything is written. Every unmapped account or level is listed, and nothing is saved until all of them map (via a map file).
  - Duplicate keys within the file are rejected.
  - A re-run is idempotent and counts unchanged rows. A value that differs from what is stored is refused unless `--update` is given.
  - `--dry-run` runs the whole import in a transaction and rolls it back.
  - The file's sha256 is recorded.
- **Comparisons cover only the months both versions share.** Comparing a version with itself is refused.
- **"Attention" view:** a single UNION of everything that needs a person (unexplained variances, a forecast not rolled, a budget not approved, …). Data-quality and process checks are treated as a product feature.

## Demo data and tests

- **Fictional company "Harbourline Group",** deliberately untidy. The seed header lists each defect it contains: two unexplained overspends, a forecast not rolled, cash under the covenant minimum, an overdue tax return, and so on.
- **Dates move with today.** The budget starts six months before the current month, and actuals run to last month, so the demo never goes stale.
- **The smoke test** runs every command against a temporary database. It asserts that migrations and the seed are idempotent and checks **exact numbers** (e.g. net profit 607,680) and exact sets of flagged issues. Coverage is tracked per command.

## Limits relative to Planlayer

- No generic dimensions, no hierarchies, no grid, no per-planner permissions. RLS is switched on with no policies, so it only blocks anonymous Supabase access.
- No workflow beyond `locked`; there is no submit/approve per planning unit.
- No spreading. Plans are entered per month or copied.

## Implications for Planlayer

| Topic | Takeaway | Backlog |
|---|---|---|
| Lock + period-range trigger | Adopt as-is in the app store. | D-22 |
| Sign convention | A decision is needed: stored natural-positive vs. GL-signed. It affects spreading, percentage mode and variance sign. | D-26 (new) |
| Closed periods | Derive from actuals or store explicitly? | D-03 |
| Import / sync discipline | Resolve fully before writing; idempotent; refuse silent overwrites; dry-run; record a hash and counts per sync run. | D-09, E-01 |
| Audit log | Actor required; reason/evidence field; before/after. | D-23 |
| Demo data | Seed with a documented defect list. Offer both a fixed-date mode (golden tests) and a relative-to-today mode (demo). | TD-11 |
| Attention view | Good later feature; it also covers reconciliation. | E-01, later |
