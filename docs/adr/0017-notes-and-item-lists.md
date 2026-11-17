# 0017: Line notes and item lists

- **Status:** Accepted
- **Closes:** N-1 (the note model) and L-1 (lists), as amended here
- **Date:** 2026-11-12

## Context

Planners explain why a value is what it is, leave to-dos for each other, and keep the evidence behind large values: a list of the invoices, licences or contracts an account will carry. Today this happens in mails and private spreadsheets, outside the plan and lost by the next round. Both belong to a line of a sheet and outlive the round they were written in.

## Decision

### Notes belong to a line, not to a round

- A **line** is a cost center × leaf account on the cost center sheet, or a cost center × work center × activity type on the capacity sheet. Account groups and nodes carry no notes. An **investment** (ADR 0028) takes notes as a line does; it belongs to one round, and so do its threads, and it has no gridnote.
- Notes come in **threads**: a first note, then replies, oldest first. Each note keeps its author and when it was written or last edited. Authors edit and delete their own notes. Deleting a thread's first note deletes the thread with its replies, as in a document's comment threads.
- **Mentions:** a note can `@mention` users who may read the line. A mention can **assign** the thread as a task to one mentioned user; a later reply can assign it to someone else.
- **Resolving:** a thread, task or not, can be resolved and reopened. Resolving records who and when.
- **Rights:** everyone who may read the line may write notes and replies and resolve threads: planners, deputies, approvers, capacity planners, controllers and viewers. This holds in every round status, submitted and approved cost centers included, because a note is conversation, not a plan value. Row-level security on the threads follows the line's sheet (`cost_center_access` for cost lines, `capacity_access` for capacity lines), and an investment's threads follow the investment's own. An investment's thread keeps no cost center of its own, as the investment moves between cost centers; mentions go to those who read its cost center.

### Notes carry forward until resolved

- A thread remembers the round it was **last written in**. Writing to it (a note, a reply, an edit, resolving or reopening) moves it to the round it was written from.
- A round shows the threads last written in it, and the **open** threads of earlier rounds. Rounds are ordered by creation, so a budget's open to-dos follow into the forecasts and the next budget.
- A thread from an earlier round shows that round's name as a pill. Once written to in the current round, the pill goes: the thread is fresh again. Resolved threads stay in the round they were resolved in.

### One gridnote per line

- A line has at most one **gridnote**: rows of a text and an amount, with their sum. It is shown above the line's threads, with who changed it last and when.
- Anyone who may write the line may change it. Readers see it.
- **Insert total** writes the sum as the line's year total, as if typed into the year column: spread by the line's badge, audited, undoable, and refused where the line is locked. Gridnote and plan aren't synced afterwards.
- Amounts are decimal strings; the server computes the sum.

### Item lists

- An **item list** belongs to a cost center × leaf account, outside rounds, so it is kept up to date during the year and is ready when the next round opens. A line has at most one list. Lists are for the cost center sheet only.
- **Items:** name, supplier, quantity (default 1), unit price, comment. The amount is quantity × unit price, rounded to the cent, computed by the database. The total is the sum of the amounts. The simple case is a name and a unit price.
- **Visibility,** set by the list's owner:
  - *private:* the owner only;
  - *cost center:* everyone who may plan the cost center, now and later, sees and edits it;
  - *public:* everyone who may read the cost center sees it, and those who may plan it edit it.

  Only the owner changes visibility and deletes the list. The database enforces both through row-level security and a trigger.
- **Into the plan:** *Save & apply total* writes the list total as the line's year total, exactly like the gridnote's *Insert total*. The line's marker warns while the list total and the line's year value differ. A list is not an entry form, and rounds keep no snapshots of lists.
- *My item lists* lists every list the user can see, with its line, total and visibility, and opens each in the same editor as the sheet's overlay.

### Finding one's notes

*My notes* lists the open threads assigned to the user, those that mention them and those they started. Each opens the line's sheet with its threads. The sidebar shows the number of open tasks assigned to the user. There is no e-mail yet. (Amended by ADR 0021: these lists are now part of the *Home* page, and the badge sits on *Home*.)

### Audit

Threads, notes, gridnotes, lists and items are logged by a trigger into `annotation_log` (table, operation, row as before and after, actor and change batch of the request), as plan values are (ADR 0005).

## Reasons

- **The line, not the round:** what a note explains, a to-do, a contract, an invoice list, stays true across rounds. Keying by line and remembering the round written in gives both the history and the carry-forward without copying notes into each round.
- **Open threads carry, resolved ones don't:** an open to-do is still to do in the next round; a resolved one is history. The pill keeps the context visible until someone takes the thread up again.
- **Everyone who reads may write:** an approver's reason for a rejection, a viewer's question and a planner's answer belong on the same line. Locks protect plan values, not the conversation about them.
- **Insert total and apply total, no sync:** a line whose value silently follows a list would change submitted plans whenever the list changes. An explicit write goes through the same locks, audit and undo as typing, and the marker shows when the two drift apart.
- **Quantity × unit price with quantity 1:** one way to read every row, with the simple case still one number.
- **Visibility per list:** a planner's working notes stay private until they are worth sharing. Lists shared with the cost center stay with it when planners change.

## Consequences

- Migration 0013:
  - tables `note_thread`, `note`, `note_mention`, `note_grid`, `item_list`, `item` and `annotation_log`;
  - row-level security on all but the log;
  - the visibility trigger on `item_list` and the audit trigger.
- The grid and capacity sheet APIs return per line its open threads (and whether one is a task for the user), the gridnote, and the item list (total, visibility, owner).
- New pages: *My notes* and *My item lists* in the Plan group. The sheets get a notes panel and an item list overlay.
- The notes show in one of two displays of the same content: a **panel** beside the sheet, which keeps the line in view (the sheets), or a **dialog** in the middle of the page, over a list (investments). In the dialog, Escape closes from an empty field too, as nothing typed is lost.
- Migration 0028: `note_thread.capex_item_id` (module `capex`), and the investments' former comments as their threads.
- Open:
  - notes on groups and nodes;
  - e-mail notifications;
  - item lists as an entry form with versions per round (the backlog's L-1 as first written);
  - comparing a list's states over time.
