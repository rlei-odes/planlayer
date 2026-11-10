# 0013: Productive cost centers and capacity planning

- **Status:** Accepted
- **Closes:** the design of K-1 and K-2
- **Date:** 2026-11-10

## Context

A productive cost center outputs activity (Leistungen) and is credited for it. Its plan starts from capacity: the hours its work centers will run, per activity type (Leistungsart), times an hourly rate. The credits then sit next to the cost center's planned costs. Companies differ in how many activity types they use (machine hours, quality measurement, repair work…) and in whether a rate has a fixed and a variable part.

Capacity planning is the second planning area after cost center planning. It builds on rounds and modules (ADR 0007), workflow (ADR 0008), roles and scopes (ADR 0009), the fact table rules (ADR 0001) and master data from datasets (ADR 0012).

## Decision

### Productive cost centers are a module of the round

- Capacity planning is the round module `capacity`. Its enabled members are the round's **productive cost centers**.
- The controller marks them on the setup page, in their own step. A new round copies them from the round it is set up like.
- Only a productive cost center has a capacity sheet.

### Master data: activity types and work centers

Two new dimensions, each with its own optional dataset, synced and curated like the others (ADR 0012):

| Dataset | Fields (* required) |
|---|---|
| `activity_types` | code*, name*, account_code (the account credited), variable_account_code |
| `work_centers` | code*, name*, cost_center_code |
| `work_center_rates` | work_center_code*, activity_type_code*, rate* (per hour) |
| `capacity_actuals` | period*, work_center_code*, activity_type_code*, hours* |

- The controller adds and edits activity types and work centers on the master-data page, as with other members.
- A planner may add a work center to a cost center they plan, e.g. a machine they intend to buy. It becomes a manual work center of that cost center.
- `work_center_rates` says which activity types a work center outputs and at which rate.

### Capacity lines and rates belong to the round

- A productive cost center's sheet has one line per work center × activity type. The lines come from the master data (the cost center's work centers and their rates). Lines added in the round are kept with the round.
- Each line has a **plan rate**. It starts at the source rate, and anyone who plans the cost center can change it on the sheet.
- The controller can set a line **inactive** for the round. It leaves the sheet and stops crediting the cost center.
- A rate has one component. The tables also hold a **variable rate**, credited to the activity type's `variable_account_code`, so that a fixed and a variable part can go to two accounts. The variable part has no UI yet.

### Hours are plan values of their own planning area

- Planned hours live in `fact_capacity`, one column per dimension (ADR 0001): version, layer, measure, period, entity, cost center, work center and activity type. The cost center is fixed when the line enters the round, so scopes and locks work on that column, as on the cost center facts.
- Actual hours are synced into the same table under the actuals version.
- Hours are entered like cost lines: per month, or as a year total. A year total spreads like the line's prior-year actual hours by default, or evenly, or by a seasonality profile (working days are the typical choice). The choice is kept on the line, as with a line spread (ADR 0007). A work center added by hand has no history, so its lines spread evenly by default.
- Row-level security, the lock trigger and an audit trigger apply as for plan values. Changes go into `capacity_change_log`, under the same change batch as the request. Changes to a line (plan rate, state, spread) go into `capacity_line_log` by a trigger, with the same batch.
- **Undo** works as on the cost sheet (ADR 0010): one of the user's batches is reverted as a new batch (hours, line states, and a line the batch added is removed), and the credits are recomputed in it. It is refused as a conflict if anything the batch wrote changed since. A work center created with a line stays in master data.

### Credits are stored plan values on the cost center

- Per month and activity type, the credit is the sum over the cost center's active lines of hours × rate. It is stored in `fact_cost_center` on the activity type's account, for the cost center and its entity.
- Credits are recomputed in the same transaction whenever hours, a rate or a line's state changes, so they share that request's change batch. They are published with the round's other plan values (ADR 0011).
- On the cost center sheet, a credit line is read only and marked as coming from capacity planning. Credits are stored natural-positive (T-7). Showing them negative, with a total cost line and the coverage difference, is the next step of K-1.

### One workflow for both sheets

- A cost center has one status per round (ADR 0008), covering both its capacity sheet and its cost sheet. Submitting from either page submits the cost center. Editing either sheet sets it in progress, and approval locks both.
- The sidebar shows Capacity before Planning, the usual order of work, but neither waits for the other.
- Both pages share the selected cost center: the one a page shows is the one the other opens with, where it can (the capacity page lists only productive cost centers).

### Capacity planners

- **Capacity planner** is a new responsibility, granted by the controller on a cost center or a node like a deputy. It may read the cost center's sheets and write its capacity sheet, not its costs.
- Owners, deputies and controllers write both sheets.
- Enforcement: the view `capacity_access` lists who may read and write which cost center's capacity. Row-level security on `fact_capacity` and the capacity lines filters through it. The policies of `fact_cost_center` let a capacity planner write only the credit accounts of the cost centers they plan capacity for, which the credits need.

## Reasons

- **A module, not a flag on the cost center:** whether a cost center is productive is decided per year, and rounds already compose modules with enabled members. Copying with the round keeps preparing next year a review.
- **Members for work centers and activity types:** they get syncing, manual entry, overrides and origins without new machinery, and fact tables refer to them by key like every other dimension.
- **Rates and lines per round:** a rate is a planning assumption of the year, not master data. The source rate is the starting point; the planned rate is what the credits use.
- **Stored credits:** the cost center's sheet, its totals, the published output and BI all read one fact table. Credits computed on read would have to be repeated in each of them.
- **One status:** costs and capacity of a productive cost center are one plan. Approving one without the other would lock half of it.
- **A responsibility, not a user role:** capacity is planned for specific cost centers, so the right needs a scope, as deputies have.
- **Two rate columns ready, one in use:** most companies use one rate. Keeping the second column and account in the tables means adding the UI later needs no migration of data.

## Consequences

- New tables: `work_center_rate` (the source's rates, replaced by each sync), `capacity_line`, `fact_capacity`, `capacity_change_log`, `capacity_line_log`, `capacity_grant`. New view: `capacity_access`. `member` gets a work center's cost center and an activity type's accounts, as codes like a cost center's entity. The module check allows `capacity`.
- New pages and steps: the capacity page (Plan, before Planning); a setup step for productive cost centers; activity types and work centers on the master-data page; capacity planners on the responsibilities page.
- Not in this decision: a published table of planned hours; showing credits negative with the coverage difference (K-1); usage factors (K-4). They are on the backlog under K-1 and K-2.
- Cost forwarding (K-3) can follow the same pattern: lines per round, a quantity per line, and stored credits and charges on the cost center facts.
