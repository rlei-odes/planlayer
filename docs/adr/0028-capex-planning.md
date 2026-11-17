# 0028: Capex planning

- **Status:** Accepted
- **Closes:** the design of K-5 (first part: planning investments; depreciation follows)
- **Date:** 2026-11-16

## Context

A budget round also plans investments (capex): what each cost center wants to buy, when it is taken into use, and how much it costs. Investments differ from the cost center sheet:

- They are a **list**, not accounts × months. A cost center responsible plans for all their cost centers at once, and the cost center is one field of an investment.
- Large investments are planned as **large projects** (Großprojekte): a new hall is not one line of 8 million, but its building shell, façade, ventilation and cranes, each with its own date. The controller sets an amount above which a single investment should be broken down.
- Investments are **confirmed one by one** by managers and the CFO or controllers, by priority. Who confirms which investment follows the cost center hierarchy, as approval does (ADR 0008).
- The finance team wants to know **when the cash goes out**, which is not when an asset is taken into use: down payments come earlier, retentions later, and part of a project falls into the next year.

Investments later feed depreciation into the cost center plans (K-5). Depreciation needs a useful life and the existing assets, which are out of this decision.

## Decision

### A module of the round

- **Capex** is a module of the round (T-24), switched on in the setup (step 7) with two settings: the **threshold for large projects** (optional) and whether the round plans a **cash-out schedule**. Turning it off is refused while the round has investments. A new round set up like an earlier one takes the settings with its setup; its investments stay with the earlier round.
- **Investment types** are installation-wide, set by controllers on the page's *Types* tab: a code, a name, the **balance sheet account** an investment of the type is capitalised on, and a **default useful life** in years. A type in use can't be removed; made inactive, no new investment takes it.

### Investments and large projects

- An **investment** has a name, a cost center planned in the round, a type, a priority (1 to 3), a start month (the month it is taken into use, first of the month as all periods) and an amount (natural-positive, T-7; blank is not zero).
- A **large project** groups investments, its **lines**. The project has a name, a cost center and a priority, which its lines share: moving a project to another cost center moves its lines. A project has no type, start or amount of its own: it shows its lines' sum and earliest start. A single investment becomes a project with itself as the first line; lines move into and out of projects.
- An investment above the threshold that is not part of a project is **flagged** in the list, so the planner breaks it down. It is not refused: the threshold is the controller's recommendation.

### Confirmation

- Each investment and each large project is **open, confirmed or declined**; a project's lines take its state. Declining needs a reason, which goes to the investment's comments.
- **Confirmers** are controllers and the approvers assigned to a node above the cost center (ADR 0008), at any level, so a division manager and the CFO above them both confirm, except an approver who plans the cost center as owner or deputy.
- A change of **what was confirmed** opens the investment again: its amount, start, type, cost center or project. A change of a project's lines opens the project. Name and priority keep the confirmation.
- The cost center's workflow status (ADR 0008) doesn't lock investments: their confirmation is their own. The round does: open, or in setup for controllers (ADR 0014); closed, nothing changes. The cost center must be planned in the round.

### Cash out

- With the schedule on, a planner spreads an investment's amount over the round's **periods** (its months, or quarters in a round planned by quarter, ADR 0015) and **one period after them: the next fiscal year**. A project's cash out is its lines'.
- The schedule **never exceeds the amount**: an entry above what is left is reduced to the rest as it is typed, the database refuses a schedule above the amount, and a lower amount cuts the schedule from its latest period back. Less than the amount may be scheduled; the rest shows as *not scheduled*.

### Rights, enforced by the database

| | Who |
|---|---|
| read an investment, comment | everyone who reads its cost center (ADR 0009) |
| add, change, delete, schedule cash out | its owner and deputies, controllers |
| confirm, decline, reopen | its confirmers |

Forced row-level security over `cost_center_access` filters reads and writes; confirmers may update rows of cost centers they don't plan, and a trigger lets them change the confirmation only. The trigger also refuses a confirmation by anyone else, opens what changed, and keeps lines in projects. A lock trigger refuses writes outside an open round. Every change of investments, schedules and comments is logged with actor and change batch (`capex_log`).

### The page

*Investments* (Plan in the sidebar, after Planning) has three tabs:

- **List:** every investment the user reads, in a table of records rather than the planning grid (ADR 0006): its rows are investments with fields to pick, not lines with a value per period, so they take ordinary form controls. Large projects are group rows, collapsible, with a row of their own that adds a line; their tag stands in the type column, which a project leaves empty. Name and amount are typed, committed when the field is left; Enter and the up and down arrows move within a column, Escape takes the typed value back. Cost center, type, priority and start are buttons that open a list, or a month picker for start. A block pasted from Excel into a field fills the columns from that field rightwards, in the order the columns show, and rows onto an add row or past the end add investments, in one request. A last row adds an investment once its name is typed; its cost center is the filter's, or the last one used. Filters by text, cost center, priority, confirmation and type, and *To confirm* for confirmers; several investments are confirmed or declined at once. Each row has its comments and, with the schedule on, its cash out.
- **Overview:** amounts by priority and confirmation, rolled up the cost center hierarchy (or by type), over what the user reads: an approver sees their area, the CFO and controllers the whole round. Declined investments are shown apart and left out of the totals. With the schedule on, cash out per period, cumulated, and what isn't scheduled.
- **Types:** the investment types (controllers).

Confirmers find open investments as a task on *Home* (ADR 0021), per round.

Writes are all or nothing; a change of an investment someone else changed since it was read saves nothing and reloads the list (ADR 0010).

### Publishing

A publish run (ADR 0011) writes the round's investments to `fact_plan_capex` and their cash out to `fact_plan_cash_out`, with the types in `dim_capex_type` (`docs/output.md`). Capex has no workflow status, so the scope "approved cost centers" publishes the **confirmed** investments, and "whole round" every one, with its confirmation in a column. The preview compares them with the output too: a round whose investments changed is publishable even when its plan values didn't, and the run refuses when they changed since the preview.

## Reasons

- **One list over the user's cost centers:** that is how a cost center responsible thinks of their investments, and a list grows to dozens of rows where a sheet per cost center would show one or two. Rights stay per cost center, as everywhere.
- **Projects share cost center, priority and confirmation:** a large project is one decision, confirmed as a whole; its lines differ in what and when, which is what depreciation and cash out need.
- **Any approver above confirms:** the manager confirms within their budget, and the CFO above can confirm or overrule; neither waits for the other. Planners don't confirm their own investments.
- **Reopening on change:** a confirmation applies to what was confirmed. Renaming or reprioritising doesn't change the investment.
- **No workflow lock:** investments are confirmed individually, often after the cost center plan is approved; locking them with the cost center would mean reopening approved cost centers for every investment.
- **Cash out capped at the amount:** the schedule describes the investment's payments; more than its amount would be a different investment. Allowing less keeps planners from inventing timing they don't know yet.
- **Types with an account and a useful life:** they are the link to the balance sheet and the default for the depreciation that follows.

## Consequences

- Migration 0027: tables `capex_type`, `capex_setting`, `capex_item` (investments and projects, with a composite foreign key keeping lines on their project's cost center), `capex_cash_out`, `capex_comment`, `capex_log`; view `capex_confirmer`; module `capex` in `round_module`.
- Package `planlayer.capex` (`common`, `items`, `overview`), rules in `planlayer.domain.capex`, endpoints under `/api/rounds/{code}/capex` and `/api/capex-types`.
- `planlayer seed-dev-capex --round CODE` (development, TD-26).
- Output tables `fact_plan_capex`, `fact_plan_cash_out`, `dim_capex_type` and views `v_plan_capex`, `v_plan_cash_out` in `planlayer_out`.
- Not in this decision, on the backlog under K-5: depreciation from investments into the cost center plans, with existing assets; who confirms (the rule above is a first cut); carrying open investments into a new round; undo on the list; investments in the file downloads.
