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
