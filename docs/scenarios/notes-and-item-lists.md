# Scenario: line notes and item lists

A walkthrough on the standard test data for notes, tasks, the gridnote and item lists. Each step names what to do and what to check. It exercises [ADR 0017](../adr/0017-notes-and-item-lists.md).

Starting point: Budget 2026 open, Budget 2027 and Forecast 6+6 2026 created after it. DE1010 *Machining* is owned by `anna.keller@`.

## 1. Controller: a note with a task

Log in as `controller@`, *Planning*, round Budget 2026, DE1010.

1. The *Account* header shows a speech bubble and a list icon: the sheet's lines take notes and item lists. Point at an account row: the same two appear beside its badge. Click the bubble: the notes panel opens on the right.
2. Type "Please check the July invoice @Ann", pick *Anna Keller* from the suggestions, tick *Assign as a task to Anna Keller*, *Add note*. The thread shows the task pill; the row's bubble shows 1.
3. Type `@` again: only people who may read DE1010 are offered.

## 2. Anna: My notes

Log in as `anna.keller@`.

- The sidebar shows *My notes* with a badge of 1. The page lists the task under *Tasks for me*, with the line and "opens in B2026".
- Click it: the planning page opens on DE1010 in Budget 2026, with the notes panel and the thread highlighted. The row's bubble is amber: a task for you.
- *Reply* "Done, it was booked twice." and *Resolve*. The badge goes; the thread moves under *Show 1 resolved*.

## 3. The gridnote

Still on DE1010, open another account's notes.

1. *+ Gridnote*: type "Maintenance contract", Tab, "12,000", Enter: a new row opens below with the cursor in it. Add "Spare parts 3,500.50". The total reads 15,500.50. *Save gridnote*.
2. *Insert total*: the line's year value becomes 15,500.50, spread as its badge says. Ctrl+Z undoes it like typing.
3. Log in as `head.pumps@` (approver, reads DE1010): the gridnote shows, but without *Edit* or *Insert total*. Notes and replies still work.

## 4. Carried forward

Log in as `controller@`, round Budget 2027, DE1010.

- An open thread written in Budget 2026 shows, with a pill "Budget 2026". Resolved threads of Budget 2026 don't.
- Reply to it: the pill goes. Back in Budget 2026, it no longer shows: it is Budget 2027's now.

## 5. Item lists

As `anna.keller@`, Budget 2026, DE1010.

1. Hover a row and click the list icon: the item list overlay opens. Add "Licences, Anthropic, 12 × 150" and "Cloud hosting, 8,400.40, renews in March". Amounts read 1,800.00 and 8,400.40; the total 10,200.40. *Line's year value … differs*.
2. *Save & apply total*: the line's year value becomes 10,200.40; the row shows the list icon.
3. Change a unit price and *Save* only: the list icon gets an amber dot (its total differs from the year value).
4. *My item lists* shows the list (owner: Me, Private, total, last change).
5. Log in as `controller@`: the private list doesn't show, neither on the sheet nor in *My item lists*. Back as Anna, set *Visible to → Cost center*: the controller now sees and edits it, but can't change its visibility or delete it. Set it *Public*: `head.pumps@` reads it, without editing.

## 6. Capacity notes

*Capacity*, DE1010: a work center line has the same bubble. Notes there are the capacity line's own; *Insert total* writes hours.
