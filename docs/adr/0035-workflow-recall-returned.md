# 0035: Recall, returned cost centers, closing and back to setup

- **Status:** Accepted
- **Amends:** ADR 0008 (workflow)
- **Date:** 2026-11-27

## Context

ADR 0008 gives each cost center not started → in progress → submitted → approved, with reject
and reopen, and each round setup → open → closed. That leaves four gaps: a planner who submits
too early has to ask the approver to reject; a rejected planner sees the status but not why; a
round closes without a word about cost centers still open; a round opened too early can't go
back.

## Decision

- **Recall:** submitted → in progress, by who plans the cost center (owner or deputy), while it
  isn't approved. No comment: it tells no one anything. A controller rejects instead, with a
  comment. Offered on the sheet and in the Status page's row actions. HR's confirmation of the
  pay goes, as with every return to the planner (ADR 0024).
- **Returned:** a cost center that a reject or a reopen sent back shows who, when and the comment
  as a note on its sheet and a tag on the planner's Home, until the next submit. It is the latest
  workflow event, as long as that is a return with a comment; no state of its own is stored.
- **Closing with cost centers not approved** needs the controller's confirmation. The API answers
  409 and lists them until the request says `unapproved_ok`; the Status page shows the list and
  asks. Hard blocks (small groups of personnel costs, ADR 0022) come first.
- **Back to setup:** an open round returns to setup while no cost center has started, i.e. every
  enabled cost center is *not started*. The check covers the whole round, with system rights,
  whatever the controller's own rights reach. The Status page offers it only then.

## Reasons

- **Recall by the planner only:** it undoes the planner's own step. Anyone else taking a
  submission back owes the planner a reason, which is what a reject with its comment is.
- **Returned derived from the events:** the workflow events already hold who, when, from what and
  the comment. A recall carries no comment, so a return with a comment is a reject (from
  submitted) or a reopen (from approved), and the next submit ends it without anything to clear.
- **Confirm rather than refuse closing:** rounds close with cost centers that never planned (a
  reorganisation, a dormant cost center). Refusing would force the controller to approve or
  disable them first; a confirmation states what happens and leaves the choice.
- **Back to setup only before anyone starts:** once a planner has entered values, setup would lock
  them out of their own work in progress. Before that, nothing is lost.

## Consequences

- `recall` in the workflow actions; `("open", "setup")` in the round transitions, guarded in
  `set_round_status`.
- The grid returns `returned` and `can_recall`; Home's cost centers carry `returned`.
- `POST /api/versions/{code}/status` takes `unapproved_ok` and answers 409 without it.
- Open: recall on the capacity and workforce sheets (today from the cost center sheet and the
  Status page).
