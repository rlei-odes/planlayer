# 0014: Preparing a planning round: actuals by year, scope, account sets, carry-over

- **Status:** Accepted
- **Closes:** part of D-13 and D-27; amends ADR 0007 (hidden accounts, new rounds) and ADR 0012 (sync of actuals, members no longer delivered)
- **Date:** 2026-11-11

## Context

Every year a controller prepares the next planning round from the previous one: the same setup with a few changes. Cost centers stop being planned or are new, accounts are phased out or added, and an administrative cost center should not see manufacturing accounts. The previous round's settings, and often its values, are the starting point. The admin keeps the source queries working across years.

Rounds read actuals from the one actuals version (ADR 0001) by period: prior-year actuals and current actuals are the round's fiscal year minus one and the round's fiscal year (ADR 0007).

## Decision

### Actuals are synced for a window of fiscal years

- A sync replaces actuals and work center actual hours **only for a window of fiscal years**; every other period stays as stored. The window is chosen on the Sync page (or `planlayer sync --years 2025-2027`). By default it covers the years the rounds in setup or open compare with: each such round's fiscal year and the year before. Without such rounds, every year is replaced.
- The admin's query stays independent of the year. The sync wraps it and adds the window as a filter on the column mapped to `period` (`period_col >= first day AND period_col < first day after the window`), which the warehouse applies before sending rows. The Sources page shows that filter for the default window.
- A period is any day of its month: a date, a timestamp or ISO text. The sync counts it as the month, so daily rows add up to the monthly cell.
- The sync preview lists the actual cells per fiscal year in the window, stored now and delivered by the source. It warns when a year that holds actuals would be emptied: the sign of a query that names a fixed year. Changed members list what changed, as was → becomes.
- Rounds don't own actuals. A round reads the periods it needs from the actuals version, so two rounds of the same year compare with the same actuals.
- Syncs are written by the system user, the actor of jobs (ADR 0009). The run records who asked for it. An admin who reads no cost centers can therefore run a sync, and its preview counts stored actuals.

### Master data only grows

A member the source no longer delivers keeps its values and its place in the hierarchy, so its values still roll up. The sync preview lists it as no longer delivered, and the member records since when (`missing_since`), cleared when the source delivers it again. Whether it is planned in a round is the controller's decision, made in the round's scope.

### The round's scope: cost centers and accounts

The setup page has three tabs sharing one draft and one save: **Forms & columns** (entry forms, comparisons, starting point, preview), **Cost centers** and **Accounts**.

- **Cost centers:** the cost center tree with, per cost center, whether it is planned in the round, its account set (own or inherited), whether it is productive (ADR 0013), its prior-year actuals and its value in the round it was set up from. Cost centers that are new since that round, or no longer delivered by the source, are marked.
- **Accounts not planned** (replaces ADR 0007's hidden accounts): an account the round doesn't plan.
  - A sheet still shows it where it has comparison values, read-only, so the planner sees what it carried and every total includes it. Without comparison values it doesn't appear.
  - It takes no plan values: the store refuses them, planners can't add it, and it has no line spread.
  - In an open round it can only be marked while no cost center has plan values on it. In a round in setup, marking it clears its plan values in the same save, an audited change batch: there they are values the controller carried over or prepared.
- **Account sets:** named lists of accounts and account groups (a group stands for every account below it), per round, e.g. "Manufacturing", "Administration", "Accounting".
  - A set is assigned to a node of the cost center hierarchy or to a single cost center; the nearest assignment above a cost center wins, as with approvers (ADR 0008). A cost center without one plans every account, as before.
  - A sheet lists every account of its set, with or without values, so a new cost center or a new account starts with its lines in place.
  - Accounts outside the set that have comparison values show read-only, like accounts not planned. Planners add accounts only from the set.
  - The store refuses plan values outside the cost center's set. Accounts credited by capacity planning (ADR 0013) are exempt; capacity planning writes them.
  - Assigning or changing sets follows the rule for accounts not planned: in a round in setup, plan values that fall outside a cost center's set are cleared in the same save; in an open round the save is refused and names the cost centers, because the planner could no longer change those values.

### New rounds: a source round and what it carries

A new round is set up like a chosen earlier round, by default the latest round of the same kind (budget from budget, forecast from forecast). A checklist says what it carries:

| Item | Carries | Default |
|---|---|---|
| Entry forms and comparisons | default form, exceptions, comparison columns | on |
| Compare with the source round | adds the source round as a version comparison column | on |
| Cost center scope | cost centers not planned | on |
| Accounts | accounts not planned, account sets and their assignments | on |
| Capacity | productive cost centers and their capacity lines (plan rates, active, spread) | on |
| Sheets | accounts added to sheets, line spreads | on |
| Plan values | the source round's plan values, moved to the new fiscal year month by month and multiplied by 1 + an uplift in percent, rounded to cents | off |

- Cost centers valid in the new fiscal year and not in the source round start planned. The scope tab marks them as new.
- Plan values are copied only for cost centers planned in the new round, and only for accounts the new round plans for them (not marked not planned, inside their account set, not credited by capacity planning). The copy is one change batch of the controller (ADR 0010), so it is audited and can be undone.
- Creating a round is a step of its own, before its setup: the page shows only the source round and the checklist. The round keeps how it was created (who, when, the checklist, the uplift and the number of plan values copied) and its setup page shows it. Carried values are the round's plan values from then on; changing the setup later doesn't copy again.
- Every entry form stores months, so changing a form in a round with carried values keeps them: a line switched to a year form shows their total, and only a year total typed afterwards is spread the new way.

### Comparison columns say what they hold

A comparison column has an optional note from the controller, shown to planners as the column's tooltip (e.g. "January to June only"). Actuals columns also state how far their actuals go: a budget prepared mid-year compares with the current year's actuals up to the last synced month, and the months after are blank, not zero.

### The controller prepares a round in setup

While a round is in `setup`, controllers may write its plan values, line spreads and capacity lines; planners can't. The other locks apply as in an open round: the cost center must be planned, and the account must be planned and inside the set. Writes in setup don't move a cost center's workflow status: it stays `not_started` until a planner writes in the open round.

## Reasons

- **A window, not the query, decides the years:** the admin writes the query once. Replacing only the window means a narrowed query or a short source extract can't wipe history, and closed years stop changing silently (D-27). Adding the filter around the query, on the mapped column, keeps the query simple and still lets the warehouse filter early, whatever its columns are called.
- **The system user writes syncs:** the admin role reads no plan values or actuals by design (ADR 0009). A sync is a job, not a planning action, and ADR 0009 already names the system user as the actor of jobs.
- **Members only grow:** sources forget members that still carry history. Dropping them, or their place in the tree, would make totals and comparisons disagree with the past.
- **Not planned, but visible:** a total that leaves out an account's prior-year actuals no longer reconciles with the ledger, and a planner who doesn't see an account phased out keeps asking for it.
- **Account sets on nodes:** cost centers of one kind plan the same accounts. A list per kind, inherited down the tree, is a few settings rather than a cost center × account matrix. Showing the set's lines without values gives new cost centers and new accounts a sheet to start from.
- **Carry-over by checklist:** what is carried depends on the round. A budget usually starts from last year's settings without its values; a forecast usually starts from the budget's values. An uplift on copied values is the most common starting point for a budget built on last year's plan.
- **Writes in setup:** carrying values and lines into a new round, and adjusting them before planners start, is the controller's preparation. The workflow status describes the planners' work, so preparation leaves it untouched.

## Consequences

- New: `account_set`, `account_set_account`, `account_set_assignment`; `hidden_account` becomes `not_planned_account`; `planning_round` keeps `created_by`, `created_at` and `carried`; `comparison` has a `note`. The locks on plan values, line spreads and capacity lines get the setup rule, and the lock on plan values the account set rule.
- The grid marks rows read only when their account is not planned or outside the set, and lists the set's accounts. "Add account" offers the set's accounts.
- The sync API and CLI take a window of fiscal years; the preview returns the window and the cells per year. `GET /api/sync/years` lists the default window and the cells stored per year.
- The setup page's Cost centers tab replaces enabling cost centers on the status page as the place to prepare a round; the status page keeps its quick toggle for a running round.
- Open: an uplift per account group, copying planned hours, and a sync that runs per source round year automatically.
