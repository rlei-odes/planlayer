# 0018: Cost forwarding

- **Status:** Accepted
- **Closes:** the design of K-3
- **Date:** 2026-11-13

## Context

Service cost centers collect costs that others cause: a tooling cost center buys the tools for the machining and assembly lines, facilities pay for the buildings, IT for the PCs and CAD licences. Their plans are only complete when those costs are passed on to the consuming cost centers (Leistungsverrechnung, Umlage). The receiving cost center then carries what it causes; the source carries what is left.

Controllers use three ways of passing costs on, known from ERP controlling as assessment and indirect activity allocation:

- **all of the source's planned costs,** split by percentages or portions (e.g. the tooling cost center by the lines' rough consumption);
- **a fixed amount,** split the same way, independent of what the source plans;
- **a rate per unit of a factor,** times each receiver's quantity: 1,200 a year per m³ in building A, 1,500 in building B, a rate per PC or per CAD licence. Only rate × quantity is passed on, so the source keeps a difference between its costs and what it passes on (over- or under-coverage). A source can have several factors with different rates.

Forwarding is the controller's work, done once the cost centers have planned, and its values change after cost centers are submitted or approved.

## Decision

### A forwarding belongs to the round

- A **forwarding** has a code and name, a **method** (`full`, `fixed` or `rate`), a **source** cost center, a **charge account** for the receivers and a **credit account** for the source. It lists its **receivers**, each with one number:
  - `full` and `fixed`: a **share**. The forwarding says whether shares are **percentages** (summing to at most 100; the rest stays on the source) or **portions** (any positive numbers, normalised to 100 %);
  - `rate`: a **quantity** of the forwarding's unit (m³, PC, FTE, …), with the **rate** per unit and year on the forwarding.
- A `fixed` forwarding has a year **amount**. Amounts, rates and quantities are for the year; the months get a twelfth each, the year's cents kept exact.
- Forwardings are kept per round, written by controllers only, while the round is in setup or open. A new round carries them with the checklist item *Cost forwarding* (ADR 0014), for sources and receivers it plans.

### Plan accounts of their own

- Each forwarding charges and credits **its own two accounts**, so every forwarding is a line of its own on both sides. The controller chooses the account group they belong to; the page proposes free codes and names, which the controller may change. A code that doesn't exist yet becomes a manual account (ADR 0012) in that group. An existing account keeps its place in the hierarchy.
- An account is a forwarding account of at most one forwarding of a round, on one side. Forwarding accounts hold no actuals of their own.
- Actuals of forwarding are booked by the ERP on its own accounts, typically in the same group. The controller marks those **not planned** in the round (ADR 0014). In a forecast, the closed months show them as actuals, and the open months the forwarding accounts' plan values, so the group adds up to the full year.

### Values are stored plan values, computed

- A forwarding writes the charges onto each receiver's charge account and their sum onto the source's credit account, per month, as plan values in `fact_cost_center` (natural-positive, T-7). They are published with the round's other plan values.
- **Full forwarding passes on what is left on the source:** per month, the source's planned costs in the round, including charges it receives from other forwardings, less its revenue, its capacity credits and the credits of its other forwardings. A building cost center can charge by m³ at a rate and pass the rest on in full; a productive cost center passes on only what its activity credits don't cover. So forwardings run in order: a full forwarding after the forwardings that charge its source or credit it. A set of forwardings where full forwardings depend on each other in a circle is refused on save; so are two full forwardings of one source (one forwarding with shares does that).
- **Rounding:** each month's amount is split into cents by the largest remainder, so the source's credit is exactly the sum of the receivers' charges.
- **Forecasts:** forwarding writes the open months only; the closed months hold actuals (ADR 0015).

### Recomputed on every change

- Saving forwardings recomputes all forwardings of the round in one change batch.
- **A full forwarding follows its source automatically:** when plan values of a source cost center change (an edit, an undo, capacity credits, values carried into a new round), the full forwardings fed by it, and the forwardings downstream of them, are recomputed in the same transaction and change batch.
- These writes go into other cost centers than the one edited, which the planner may not write. They run as the **system user** for the rest of the transaction, so the change log names *system* in the planner's batch. Undo of a planner's batch reverts the planner's cells and then recomputes; forwarding cells aren't the planner's to revert.

### What the database enforces

- **Forwarding accounts are written by controllers only** (the system user is one). A planner can't type into them, by any route.
- Forwarding values change **whatever the cost center's status**: submitted and approved cost centers take new charges, and writing them moves no status. Approval covers what a cost center planned, not what is forwarded to it. The round must still be open, or in setup for a controller, and the cost center enabled in it.
- Forwarding accounts are outside account sets and not-planned accounts, like capacity credits.
- The forwarding tables are written by controllers only, while the round is in setup or open.

### What cost centers see

- The **forwarding page** (*Round* in the sidebar, controllers) lists the round's forwardings in a table and edits one at a time: source, method, shares or quantities and rate, accounts. It shows what each receiver gets for the year and, for each source, its planned costs, its other credits (revenue, capacity), what is passed on, and what stays: for rate forwardings, the over- or under-coverage.
- **Both sides see it on their sheet:** the receiver's charge line and the source's credit line are read only and marked *Forwarded*, naming the forwarding and the other side. Credits are shown negative, so a group's subtotal is what the cost center carries. Credits from capacity planning are shown negative too (K-1).

## Reasons

- **Shares and quantities in one number per receiver:** the three methods differ in what is passed on, not in who receives it. One receiver table keeps the page tabular.
- **Percentages or portions:** controllers think in percentages for a rough split, and in portions (machines, licences) when they count. Percentages below 100 leaving a rest on the source is the ERP convention.
- **Own accounts per forwarding:** the receiver sees what it is charged for, the source sees each factor's coverage, and BI can tell forwarded costs from direct ones. The group puts them where the actual forwarding is booked, so plan and actuals compare within the group.
- **Stored values:** the cost center sheet, its totals, the published output and BI read one fact table, as with capacity credits (ADR 0013).
- **Automatic for full forwarding:** a full forwarding that waits for the controller to recompute shows receivers stale charges while the source plans. The system user writes only values derived from the controller's definitions, so planners gain no way to write into other cost centers.
- **Past workflow status:** forwarding is typically run after approval. Refusing it on approved cost centers would mean reopening them all.

## Consequences

- New tables `forwarding` and `forwarding_receiver`; view `forwarding_account` (round, account, side). The lock trigger of `fact_cost_center` treats forwarding accounts separately; a guard trigger protects the forwarding tables.
- Planning edits and undo recompute forwardings when the cost center feeds a full forwarding. Creating a round recomputes after carrying values.
- Not in this decision: factor quantities from a source dataset or from headcount planning; quantities varying by month; a change log and undo for the forwarding definitions; forwarding in a cost center's own currency; iterating reciprocal forwarding. They are on the backlog under K-3.
