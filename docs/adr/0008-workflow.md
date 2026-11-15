# 0008: One-step approval per cost center, approvers per hierarchy node

- **Status:** Accepted
- **Closes:** D-22
- **Date:** 2026-11-08

## Context

Approval happens per cost center, with a status page that rolls up by department (T-20). Locks are enforced in the database by statement-level triggers (ADR 0005). Publishing is a separate, explicit controller action (T-21). Planning rounds have a status of their own (ADR 0007).

## Decision

### Status per round × cost center

| Status | Meaning | Planner can edit |
|---|---|---|
| `not_started` | no plan value saved yet | yes |
| `in_progress` | values saved, not submitted | yes |
| `submitted` | waiting for approval | no |
| `approved` | approved | no |

| Transition | Who | Note |
|---|---|---|
| `not_started` → `in_progress` | automatic, on the first save | |
| `in_progress` / `not_started` → `submitted` | the planner (owner or granted) | an empty cost center can be submitted ("nothing to plan") |
| `submitted` → `approved` | the approver | single or bulk, e.g. all submitted cost centers below a node |
| `submitted` → `in_progress` | the approver (reject) | a comment is required |
| `approved` → `in_progress` | the controller (reopen) | a comment is required |

### Round status

- **`setup`:** the controller configures; planners can't edit.
- **`open`:** planning, with statuses as above.
- **`closed`:** no edits anywhere, whatever a cost center's status. Only the controller closes and reopens a round.

### Approvers

- The controller assigns approvers to nodes of the cost center hierarchy, in Planlayer.
- A cost center's approver is the user assigned to the nearest node above it, found through the closure table (ADR 0002). A node can have several approvers, and any one of them may approve.
- With no assigned node above a cost center, the controller role approves it.
- A planner can't approve their own cost center.

### Enforcement and history

- **The lock trigger** on the fact tables (ADR 0005) allows writes to a plan version only while the round is `open` and the cost center's status is `not_started` or `in_progress`. Synced actuals are not affected.
- **Every transition** is a row in a workflow event table: actor, time, from, to, comment. The status is the latest event.
- **The status page** shows every enabled cost center with its status, approver, last change and the round deadline. Counts roll up per hierarchy node, with filters "overdue" and "waiting for me".

## Reasons

- **One step** covers the common mid-size case (cost center owner → department head or controller) with the least friction. A chain can be added later as further statuses, without changing the lock rule.
- **Approvers per node** match how companies delegate: department heads approve their department. Nodes are maintained in Planlayer, because sources rarely carry approvers. The controller fallback means no cost center is ever left without an approver.
- **Blocking edits after submit** keeps what the approver sees stable. Changes go through reject or reopen, so the history explains every change.
- **The status as a database fact** read by the lock trigger means no code path can edit an approved cost center.

## Consequences

- New tables: `workflow_status` (round, cost center, status), `workflow_event`, `approver_assignment` (node, user). The lock trigger joins `workflow_status` and `planning_round`.
- D-21 adds the approver role, scoped by node assignment, next to planners (owner and grants) and the controller.
- E-07 builds the transitions, the status page and bulk approval. Email notifications on submit, reject and approve come after v1.
- Publishing (T-21) can take a round's approved cost centers only, or the whole round. That is part of D-12.

## Statuses across the app

The statuses this decision set up are joined by those of later modules. This section lists all of them and how they combine; each module's ADR holds its own reasons.

### The statuses

| Status | Values | Kept per | Set by | Defined in |
|---|---|---|---|---|
| Round status | `setup`, `open`, `closed` | round | controller | ADR 0007, this ADR |
| Cost center planned | enabled, not enabled | round × cost center | controller (setup) | ADR 0007, ADR 0014 |
| Workflow status | `not_started`, `in_progress`, `submitted`, `approved` | round × cost center | planner, approver, controller | this ADR |
| Closed months | months up to *actuals through* | round (forecasts) | controller (setup) | ADR 0015 |
| Productive | yes, no | round × cost center | controller (setup) | ADR 0013 |
| Transfer | `proposed`, `accepted`, `declined`, `cancelled` | position | sending and receiving side | ADR 0021 |
| Personnel costs | on, off | round | controller (setup) | ADR 0022 |
| Pay release | released, not released | round | HR, once the round is open | ADR 0023, ADR 0024 |
| Pay handover | handed over, not handed over | round × cost center | HR, once access is released | ADR 0024 |
| Pay access | listed, not listed | round × cost center × user | HR | ADR 0023 |
| Proposed owner | decided, proposal | round × cost center | source sync, HR | ADR 0023 |
| HR review | confirmed, not confirmed; off unless HR switches it on for the round | round × cost center | HR, in an open round; withdrawn by others' changes | ADR 0022, ADR 0024 |
| Small group | small, not small | round × cost center × personnel account | recompute, from the minimum group size | ADR 0022 |
| Note thread | open, resolved | line | anyone who reads the line | ADR 0017 |

### Round status × who writes what

| What | `setup` | `open` | `closed` |
|---|---|---|---|
| Setup (forms, columns, scope, modules, rules) | controller; personnel cost rules HR and controller | controller; personnel cost rules HR and controller | nothing (reopen first) |
| Plan values, hours, workforce, positions | controller (preparing; no status moves) | planners and controller, while the cost center is editable | nothing |
| Pay of positions | HR (preparing) | HR; the people with access once HR has handed the cost center over; while the cost center is editable | nothing |
| Access lists | HR | HR | HR |
| Release, handover | nothing | HR | nothing new; HR may take them back |
| HR review | nothing | HR | nothing |
| Cost forwarding definitions | controller | controller | nothing |
| Workflow actions | none | as in the transition table above | none |
| Notes, item lists | everyone who reads the line | everyone who reads the line | everyone who reads the line |
| Publishing | controller | controller | controller |

### Workflow status × the sheets of a cost center

A cost center's sheets share one workflow status: costs, capacity (productive cost centers), workforce and the pay of its positions.

| | `not_started` | `in_progress` | `submitted` | `approved` |
|---|---|---|---|---|
| Typed values, hours, positions, pay | write; the first write in an open round moves to `in_progress` | write | read only | read only |
| Accept a transfer (both sides must be editable) | yes | yes | refused | refused |
| Written by a calculation: forwarding charges and credits, personnel cost totals | written, status unchanged | written, status unchanged | written, status unchanged | written, status unchanged |
| Approve | – | – | needs HR's review where the cost center has positions in a personnel costs round and HR switched the review on | – |
| Overdue after the deadline | yes | yes | no | no |
| Published with scope *approved* | no | no | no | yes |

### Gates between statuses

| Action | Refused while |
|---|---|
| Open a round | – (from `setup` only) |
| Close a round | a small group exists |
| Publish, or download the round's rows | a small group exists |
| Approve a cost center | HR hasn't confirmed its pay (personnel costs on, positions on the cost center, review switched on) |
| Release access control | the round isn't open |
| Hand pay over, confirm it | the round isn't open; for a handover also: access isn't released |
| The people with access edit pay | HR hasn't handed the cost center's pay over |
| Turn personnel costs off | the round is open and personnel cost values exist |
| Change the workforce mode away from positions | personnel costs are on; or the round is open and has workforce values |
| Readers see pay | the round's pay is not released, or they aren't listed for the cost center |
| A proposed source owner reads pay | HR hasn't added them |

