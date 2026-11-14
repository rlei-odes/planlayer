# 0021: Transfers of positions, and the start page

- **Status:** Accepted
- **Closes:** W-17 (moving a person to another cost center), first cut
- **Amends:** ADR 0020 (positions, build-up), ADR 0017 (*My notes*)
- **Date:** 2026-11-14

## Context

People move between cost centers during a plan: a technician joins another team in September, a whole position goes to a new department. In a positions round (ADR 0020), that used to take two unrelated edits: a leave date on one sheet and a new position on the other. The build-up then showed a leaver and a hire, and neither side knew about the other's edit.

A transfer touches two cost centers, often planned by different people, and the receiving side carries the person's costs from the move on. It shouldn't happen to them without their agreement.

Planners also need one place that tells them what is waiting for them: transfers to accept, note tasks, cost centers to approve, and the cost centers they still have to plan.

## Decision

### A transfer is proposed by the sending side and accepted by the receiving side

- On a position, *Transfer* opens a dialog: the receiving cost center (any cost center planned in the round), the kind of transfer, the month for a transfer during the year, and an optional note.
- Whoever writes the sending cost center proposes: its owner, deputies or a controller, while its sheet is editable.
- The proposal is a task for whoever writes the receiving cost center: its owner, deputies, or any controller. They accept it, or decline it with an optional reason. The sending side may withdraw it until then.
- **Until accepted, nothing moves.** The position and its FTE and headcount stay with the sending cost center. A position has at most one waiting transfer.
- Accepting runs in one transaction and one change batch, through the same locks as any position change. Both cost centers must still be planned and editable; a submitted or approved side refuses it (423).

### Two kinds

| Kind | On accepting |
|---|---|
| **Whole position** (`full`) | The position moves to the receiving cost center with its whole year and keeps its number. It records the cost center it came from. |
| **During the year** (`during_year`) | The sending position ends the day before the chosen month and is marked *transferred to* the receiving cost center. The receiving cost center gets a position for the same person, with the same number, entering on the first of the month and recording where it came from. Its leave date is the original's. |

- A transfer during the year starts on the first of a month of the fiscal year, but not in the first month (that is a whole-position transfer) and not in a forecast's closed months (ADR 0015). The person must have entered before it and not left before it.
- Rounds plan by month or by quarter (ADR 0015); positions carry dates either way, so both kinds work in every positions round.
- The ended half of a transfer during the year stays as it was transferred: it can't be edited or deleted. Position numbers stay unique per round among positions that haven't ended in a transfer.

### Build-up

The build-up (ADR 0020) names moves apart. A position entering through a transfer during the year counts under *entering* as *moving in*, not as new, a replacement or someone hired already. The ended half counts under *leaving* as *moving out*. Over a department or the company, moves cancel out.

### Visibility

- The receiving side sees whom it is asked to take over (number, title, name, type, category, contract %) before accepting. It doesn't read the sending cost center otherwise.
- Accepting writes the sending side with the system user's row-level-security rights, as forwarding writes receivers (ADR 0018). The audit names the accepting user as the actor.
- A transfer row is the record of the decision: who proposed it and when, who decided and when, the note and the reason. Rows aren't deleted, except with their position.

### The start page

*Home* is the first page of the sidebar and the page the app opens on, for every user. It replaces *My notes*.

- **Open tasks:** transfers to accept (with *Accept* and *Decline* in place), rounds with cost centers waiting for the user's approval, and note threads assigned to the user (ADR 0017). The sidebar badge counts these entries.
- **Your planning:** per open round, the cost centers the user plans as owner, deputy or capacity planner, with their status, the deadline, the share submitted, and buttons to the sheets they fill in (*Capacity*, *Workforce*, *Costs*). Controllers and viewers see the round's statuses at a glance instead, with a link to the status page.
- **Your transfers:** those the user proposed, waiting or decided in the last 30 days, with the reason when declined.
- **Notes** that mention the user or that they started, as on *My notes* before.

## Reasons

- **Propose and accept:** the receiving side takes on the person's FTE, headcount and later their costs (W-7). Making it their decision keeps a planner from finding someone in their plan they didn't agree to. The sending side keeps the values until then, so nothing is counted twice or nowhere.
- **Controllers accept wherever they write:** a cost center without an owner, or a planner who is away, shouldn't block a move.
- **Two positions for a transfer during the year:** each cost center keeps its own part of the year with its own dates, so the month-end roll-up, comparisons and publishing work unchanged. Sharing the number keeps the person recognisable on both sides and in the log.
- **The first of a month:** FTE and headcount are month-end levels (ADR 0020). A move on the first counts on the receiving side from that month and on the sending side up to the month before, with no half months.
- **One start page:** tasks reach planners in one place, without e-mail (still open, ADR 0017). The cost centers they plan, with their status and deadline, are the planning work itself.

## Consequences

- Migration 0018:
  - table `workforce_transfer` with row-level security: both sides read, the sending side proposes, either side updates; a unique waiting transfer per position;
  - `workforce_position.transferred_from_id` and `transferred_to_id`;
  - the round's unique position number becomes a partial unique index over positions not transferred away.
- API: `GET …/workforce/{cc}/transfer-targets`, `POST …/workforce/{cc}/positions/{id}/transfer`, `GET /api/transfers`, `POST /api/transfers/{id}/decision`, `POST /api/transfers/{id}/cancel`, `GET /api/home`. The workforce sheet returns the transfers waiting for the cost center, and per position its transfer state; the build-up returns `moving_in` and `moving_out`.
- The *Workforce* page gets a *Transfer* action per position, a *Transfer pending* note with *Withdraw*, and a card of transfers waiting to be accepted. The *Home* page replaces *My notes*.
- Open, on the backlog under W-17: undoing an accepted transfer (restoring the original); changing the date of an accepted transfer; transfers proposed from HR's sync (W-15); personnel costs split at the date (W-7); e-mail notifications.
