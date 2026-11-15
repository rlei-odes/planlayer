# 0026: Exclusions from rules, and a position's costs by part

- **Status:** Accepted
- **Amends:** ADR 0022 (rules of the round, pay per position)
- **Date:** 2026-11-15

## Context

The round's rules (ADR 0022) apply to every salaried position, optionally limited to an entity. Companies make exceptions: managers get no collective raise, executives no overtime allowance, a managing director pays no unemployment contribution. Rules limited to staff categories (on the backlog) cover large groups, but exceptions often concern a few people who share no category.

Whoever reads a position's pay also sees only its total cost for the year, not what makes it up. Without the parts, it isn't clear what a rule does to a position, or whether it applies at all.

## Decision

### Exclusions per position

- HR excludes a position from any rule of the round: a yearly extra, a raise, a long-service award or a contribution. The position's pay dialog lists the round's rules with an *Applies* tick; unticking excludes it.
- An excluded rule adds nothing for the position. Rules that build on it follow: excluded from the 13th salary, a raise no longer adds its percentage on that extra, and contributions are calculated on the smaller gross pay.
- The exclusions are stored with the position's pay (`position_pay.excluded_rules`, rule ids). Like the rest of the pay, they are guarded by `pay_access`, follow the pay locks (ADR 0022, 0024) and are logged in `position_pay_log`.
- **Only HR** sets them. The API refuses others, and the lock on `position_pay` refuses a change of `excluded_rules` by anyone but HR. Changes by the owner (planned salary, bonus) keep them.
- The bonus and the salary account of the staff type aren't rules. They can't be excluded: the bonus is the position's own amount.

### Rules keep their identity

- Saving the rules changes them in place: a rule edited keeps its id, a new one is added, and only a rule removed from the list is deleted. Exclusions of a removed rule are ignored from then on.
- A save that names a rule id the round doesn't have is refused (the page was out of date).
- A new round's rules are copies with new ids (ADR 0014). The positions' exclusions are carried with them, mapped to the copies.

### Costs by part

- For each position, the page shows its costs over the fiscal year by part: the salary (or the typed amount), each rule of the round, and the bonus, with the account each writes to and the position's total.
- Rules for another entity are listed as *for (entity) only*, excluded rules as *excluded by HR*, both without an amount.
- While the dialog is being filled in, the parts are recalculated by the server as a preview (`…/positions/{id}/preview`), which writes nothing. Saving then recomputes the totals as before.
- Everyone who reads the position's pay sees the parts. Where pay can't be changed (round or cost center locked, a transferred position), the dialog opens read only.

## Reasons

- **Per position, by HR:** an exception for a few people is a decision about their pay, and HR decides pay. A tick on the position is the smallest addition that covers it, and the rules stay as simple as they are.
- **On the pay row:** the locks, row-level security and the log of pay apply without new tables or policies.
- **Rules changed in place:** otherwise every save of the rules would silently drop all exclusions.
- **The parts from the server:** the calculation exists once (T-11), so what the dialog shows is what the totals contain.

## Consequences

- Migration 0025: `position_pay.excluded_rules`; the lock on `position_pay` checks it.
- The domain's rules carry their ids, and a position carries its exclusions; `parts()` returns a position's costs by part.
- The page's per-position cost for the year now applies entity-limited rules by the position's entity. Before, it left them out; the stored totals were already right.
- The rules API takes and returns each rule's `id`.
