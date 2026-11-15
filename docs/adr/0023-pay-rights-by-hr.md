# 0023: Pay rights controlled by HR

- **Status:** Accepted
- **Closes:** W-18's open items "HR grants or withholds pay rights" and "a log of role changes"
- **Amends:** ADR 0022 (who reads pay)
- **Date:** 2026-11-15

## Context

ADR 0022 gave pay rights to HR and to each cost center's owner as the source names them. That takes the controller out of the decision. It still leaves it with whoever maintains the owner field in the source: a changed owner there reads pay at once, and HR doesn't learn of it. Owners are also not always the only people who should read a cost center's pay: a deputy covering for the owner, or a head of department above, may need it too. Other cost centers should be read by HR alone.

## Decision

### HR keeps the list of readers per round and cost center

- For every round with personnel costs and every cost center planned in it, HR keeps a list of **readers** (`pay_reader`). A reader reads and changes the pay of the cost center's positions in that round, as the owner did in ADR 0022. A cost center without readers is read by HR alone.
- When personnel costs come on in a round, the list starts with each cost center's source owner. A new round set up like an earlier one takes over that round's lists.
- Only HR changes the lists. On the personnel costs page (*Who reads pay*), HR adds and removes readers per cost center. Adding suggests the source owner, the cost center's deputies and the approvers above it; *More…* offers every active user.

### Nothing until HR releases the round

- Readers read pay only once HR has **released** the round (`pay_release`). Until then only HR reads pay in it. HR can take the release back.
- A reader of a round not yet released sees the personnel costs page say so. On *Home*, the cost center's *Pay* button is shown but inactive.
- Changes to the lists after the release apply at once.

### A new source owner is a proposal

- Per cost center, the store keeps the source owner HR last decided on (`pay_owner_seen`). When the source names someone else, or a cost center is newly planned, the row is marked as a proposal on HR's page. The new owner reads nothing until HR adds them.
- HR decides by adding the owner or by keeping the list as it is. Either way the owner counts as decided on.

### Every change is logged

- Triggers write every change of a list, of the release and of the HR role into `pay_access_log`: who did it, when, what, for whom, in which round and on which cost center. No change escapes the log, whichever path made it.
- Every HR user reads the log on the personnel costs page.

### Enforcement

- The view `pay_access` lists, per user, round and cost center, who reads pay: HR and the system user everywhere, and the readers of a released round. The row-level security of ADR 0022 reads it per round.
- HR's salaries from the staff list (`hr_pay`) belong to no round. HR and the system user read them; readers see a position's HR salary through its pay.
- A trigger refuses changes to the lists and the release by anyone but HR. The system user writes them only when personnel costs come on and when a round is carried over.

## Reasons

- **HR decides, the source proposes:** the source knows who is responsible for a cost center. Whether that person reads salaries is HR's decision. A proposal makes each change visible, so HR doesn't have to watch the source.
- **Per round:** responsibilities change between years, and a closed round keeps the rights it had.
- **Release before reading:** HR checks the lists once, at the start, rather than closing gaps after people have already seen pay.
- **A free choice with a log:** in practice the right reader is sometimes outside any rule (a superior, an interim). Suggestions cover the usual cases. A log every HR user reads keeps the rest deliberate.
- **The HR role in the same log:** whoever can grant the HR role can read all pay, so HR sees who was given it.

## Consequences

- New tables: `pay_reader`, `pay_owner_seen`, `pay_release`, `pay_access_log`. `pay_access` gains `version_id`. Existing rounds with personnel costs start with their owners as readers, not released.
- New tab *Who reads pay* on the personnel costs page, for HR. The list of cost centers says whether the round is released; *Home* marks the *Pay* button inactive until then.
- The admin still assigns the HR role. The log makes that visible; encryption at rest (W-18) is what protects pay from administrators of the database.
