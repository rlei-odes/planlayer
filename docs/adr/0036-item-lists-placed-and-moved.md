# 0036: Item lists started anywhere, judged against a round, moved and taken over

- **Status:** Accepted
- **Amends:** ADR 0017 (item lists)
- **Date:** 2026-11-27

## Context

An item list belongs to a cost center × leaf account, outside rounds (ADR 0017). It was started
only from its line on a planning sheet. A round may not plan that line: the cost center or the
account isn't planned, the account is outside the cost center's account set, or the source no
longer delivers the member. The list then stays, but nothing showed it, and it couldn't be put
on another line. A list whose owner is disabled couldn't be re-shared, moved or deleted by anyone.

## Decision

- **Start a list from *My item lists*:** *+ List* asks for a cost center the user plans in the
  current round and an account it plans there (the choice of the sheet's *Add account*). A line
  that has a list the user sees opens it; otherwise the editor starts a new one.
- **Judged against the current round:** *My item lists* follows the round chosen in the top bar.
  A list whose line the round doesn't plan carries the reason: cost center or account no longer
  delivered, account a group now, cost center not planned, account not planned, or not in the
  cost center's account set. A filter shows only those. Without a round the page still lists
  everything.
- **Move:** the owner moves a list to another cost center × account. The target is a cost
  center the owner plans, and its line has no list yet. Items, categories, visibility and owner
  move; the plan values of both lines stay as they are.
- **Take over:** a controller hands a list whose owner is disabled to themself or to an active
  user who plans its cost center. Controllers read the lists of disabled owners, private ones
  included, so they can decide.
- **Enforced in the database:** the guard trigger on `item_list` allows a change of cost center
  or account only by the owner and only to a cost center they plan; a change of owner only by a
  controller, from a disabled owner, to themself or to someone who plans the cost center; a change
  of visibility only by the owner. The row-level security policies give controllers read and
  update on lists of disabled owners. Every change is logged in `annotation_log` as before.

## Reasons

- **The owner moves it:** a move changes who sees a list shared with its cost center, as a
  change of visibility does, which is the owner's.
- **Plan values don't move:** a list is not an entry form (ADR 0017). The new line's marker shows
  the difference between list total and year value, and *Save & apply total* applies it there.
- **Against the current round:** lists outside rounds have no round of their own. The round the
  user works in is the one whose sheets the lists should fit.
- **Takeover from a disabled owner only:** an active owner can share or hand on what they own.
  Without an owner able to act, a list would be stuck; a controller is the role that keeps
  round-wide things working.

## Consequences

- Migration 0005: the guard trigger and the read and update policies on `item_list`.
- `GET /api/item-lists?round=` returns `unplaced`, `owner_disabled` and `can_take_over`;
  `POST /api/item-lists/{id}/move`, `GET /api/item-lists/{id}/owners`,
  `POST /api/item-lists/{id}/owner`.
