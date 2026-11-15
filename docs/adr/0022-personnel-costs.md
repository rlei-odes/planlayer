# 0022: Personnel costs

- **Status:** Accepted; who reads pay is amended by [ADR 0023](0023-pay-rights-by-hr.md), where HR's salaries come from by [ADR 0025](0025-hr-salary-upload.md), exclusions from rules by [ADR 0026](0026-rule-exclusions-and-cost-breakdown.md)
- **Closes:** W-7 (personnel costs from positions), W-18 (confidential personnel data), first cut
- **Amends:** ADR 0009 (roles), ADR 0020 (positions), ADR 0011 (publishing), ADR 0008 (workflow)
- **Date:** 2026-11-15

## Context

In a positions round (ADR 0020) the plan already knows who works where, from when to when, and at what contract percentage. Personnel costs are the largest cost block of most cost centers, and they follow from exactly that: each position's pay by month, the yearly extras on top of it, and the employer's social contributions, written to the personnel accounts of the cost center that carries the person.

Pay is confidential in a way the rest of a plan is not. Today everyone who reads a cost center reads all of it: its owner and deputies, the approvers above it, controllers and viewers (ADR 0009). Pay per person must be read by fewer people than that. In the setup this decision aims at, the controller reads no one's pay. HR reads all of it, and the cost center's owner reads that of their own people. Totals still have to reach the cost center sheet, the status page and the warehouse, because personnel costs are part of the plan. A total over one or two people is a salary, though, so totals of small groups must not be shown either.

## Decision

### A module of the round, on top of positions

- *Personnel costs* is the round module `personnel_costs`. The controller turns it on in the round's setup, next to the workforce mode, while the round is in setup or open. It needs workforce planning in mode `positions`, because costs come from positions. While it is on, the workforce mode can't leave `positions`.
- It can be turned off in setup. Turning it off removes the round's rules, the pay of its positions and the values they wrote. In an open round, turning it off is refused once values exist.
- The module is copied with a new round's setup, rules included (ADR 0014).

### HR, and who reads pay

- A fourth role, **HR** (`app_user.is_hr`), set by the admin on the users page like the other roles. It combines with them.
- **Pay** means the salaries, bonuses and costs per position, and HR's salaries from the staff list. Pay may be read by:

  | Who | Reads pay of | Writes |
  |---|---|---|
  | HR | every cost center, in every round, closed rounds included | salaries, bonuses, the cost center charged, the review |
  | the cost center's owner, as the source names them | their cost center | salaries and bonuses of its positions |

- Nobody else reads pay: not deputies, not approvers, not viewers, and not controllers unless they also hold the HR role. An owner the controller sets on the responsibilities page (ADR 0009) plans the cost center but gets no pay rights. Only the owner the source names reads pay, so a controller can't grant themselves pay by changing a responsibility.
- The view `pay_access` lists who reads and writes which cost center's pay. Forced row-level security on the pay tables reads it, as `cost_center_access` guards plan values (ADR 0009). The API checks it too, and reports a cost center outside it as not found.
- To read the names on positions, HR's role reads every position and HR's staff list (the read policies of `workforce_position` and `hr_staff`). HR doesn't read cost plans.
- **The system user reads pay only to compute totals.** Calculating a cost center's personnel costs runs with the system user's rights (as forwarding and transfers write, ADR 0018 and 0021), whoever started it. What comes out of it is totals per account and month, never pay per position.

### Pay per position

| Field | Who writes | Notes |
|---|---|---|
| HR salary | HR | the monthly salary from HR's staff list, taken with the position, or typed by HR |
| Planned salary | owner, HR | monthly; blank means the HR salary. A plan position starts at 0 |
| Bonus | owner, HR | a yearly amount |
| Charged to | HR | another cost center that carries this position's costs (for small groups, below) |

- **HR's figure and the plan stay apart.** Only HR changes HR's salary: it comes from the staff list, and HR corrects or fills it in where the list has none. The planned salary is a field of its own, so anyone reading pay sees what HR's figure is and what the plan changed.
- A position taken from HR (*Add staff from HR*) takes HR's salary from the staff list. The dataset `staff` gets an optional field `monthly_salary`. Synced salaries go to `hr_pay`, a table of their own under the pay rules, not to `hr_staff`, which everyone who reads the cost center reads.
- Salaries are what the person is paid per month at their contract percentage. Planlayer doesn't scale them by the contract percentage.
- Pay is audited: a trigger writes every change, with old and new values, into `position_pay_log`. Like `position_pay`, the log rows are guarded by `pay_access`.
- Pay follows the position's locks: the round open (or in setup), the cost center planned, its status editable.

### Staff types: salaried or typed

The round's rules give each staff type a salary account and say whether it is **salaried** or **typed**:

- **Salaried:** the monthly salary, raises, yearly extras, long-service awards and contributions are calculated (below).
- **Typed:** the position's monthly amount is all there is. This covers freelancers, agency staff and people paid by the hour. No raises, extras or contributions apply.

The default is typed for external staff types and salaried for the others. A staff type without an account in the round is not costed, and the rules page warns about it.

### Rules of the round

HR and controllers edit the rules while the round is in setup or open. The rules hold no pay, so controllers may see them. Every rule names the account it writes to. Except for the bonus, a rule may be limited to one entity, for companies whose countries pay differently.

| Rule | What it adds per salaried position and month | Account |
|---|---|---|
| **Yearly extra** | base pay × *n* / 12, where *n* is the number of extra salaries per year (13th salary: 1; Austria's 13th and 14th: 2) | its own |
| **Raise** | a percentage on base pay and on the yearly extras, from a month on | its own |
| **Long-service award** | in the month the person completes *n* years since entry: *m* monthly salaries or a fixed amount, per step (e.g. 10, 25 and 40 years) | its own |
| **Bonus** | the position's yearly bonus, spread over the months worked | the round's bonus account |
| **Contribution** | a rate on the month's gross pay, up to an optional monthly ceiling | its own |

- **Base pay** for a month is the salary × the share of the month's days the position is active. Someone entering on the 15th of a 30-day month costs 16/30 of the salary that month; a plan position starts on its entry date.
- **Yearly extras are spread over the year,** not put in the month they are paid. This matches the accounting, which accrues them month by month, so the plan and the ledger compare month by month. The number of extra salaries is stored as *n*. The UI also takes them as a percentage of base pay: Norway's 10.2 % holiday pay is *n* = 1.224, because *n* / 12 = 10.2 %.
- **Raises** compound: two raises of 2 % from April and 1 % from October give 1.02 × 1.01 from October. The raise's own account gets everything the raise adds to base pay and to the yearly extras. A raise can be added at any point of the round, also after the cost centers are approved, as a percentage across all salaried positions. It shows as a cost apart from the planned salaries.
- **Long-service awards** need the position's entry date. A position without one gets none.
- **Contributions** are calculated per person and month: rate × min(gross pay, ceiling × the month's active share). Gross pay is everything above. Ceilings make the plan follow the rule that pay above the contribution ceiling (in Germany, the *Beitragsbemessungsgrenze*) bears no employer contribution. Several contribution lines (pension, health, unemployment, a company pension) each have their own rate, ceiling and account.
- Amounts are summed per cost center charged, account and month, and rounded to cents once (T-12).

### Totals on the cost center

- The totals are **stored** in `fact_cost_center` on the personnel accounts of the cost center charged, as capacity's credits are (ADR 0013). The sheet, comparisons, the status page and publishing read them like any other plan value. The cost center's full forwardings follow them (ADR 0018).
- They are recomputed in the transaction of every change that moves them: pay, a position (dates, type, category, removal, transfer), *Add staff from HR*, and the rules. A rule change recomputes every cost center of the round.
- **Personnel accounts are written by the calculation only.** The sheet shows them read only, marked *From personnel costs*. In the store's lock (ADR 0008, 0014) they behave like forwarding accounts: written with controller rights, whatever the cost center's status, outside account sets and not-planned accounts, and without moving the status. A raise added after approval reaches the approved cost centers' totals this way.
- In a forecast, only the open months are written. The closed months hold actuals (ADR 0015).

### Small groups

- A total over few people reveals their pay. A **small group** is a cost center and personnel account whose plan values in the round come from fewer positions than the round's **minimum group size**. Salaried and typed positions count separately, because they write to separate accounts: one employee, one person paid by the hour and one freelancer are three groups of one.
- HR sets the minimum per round. The default is 3. A value below 3 is shown in bold red as a warning, not refused.
- While a cost center has a small group, its personnel accounts are hidden from everyone who doesn't read its pay. A row-level-security policy on `fact_cost_center` reads the round's stored small groups (`pay_small_group`), which every recompute rewrites.
- HR sees each small group as a task on *Home* and on the personnel costs page. They resolve it by charging the positions' costs to another cost center (*Charged to*), e.g. the department's, until the groups are large enough.
- **Closing the round and publishing are refused while small groups exist.** Then neither the round's sheets nor `planlayer_out` holds a total over fewer people than the minimum.

### HR's review

- HR confirms each cost center's pay on the personnel costs page.
- A change that moves the cost center's personnel totals withdraws the confirmation when someone other than HR makes it: pay, positions, or a rule (a controller's rule change withdraws every cost center's). HR confirms a submitted cost center, and rejecting or reopening it withdraws the confirmation (ADR 0024).
- A cost center with positions **can't be approved until HR has confirmed it** (ADR 0008). Planners still submit without it, so HR reviews what is submitted.

### Publishing

Publishing writes the personnel accounts with the cost center's other plan values (ADR 0011). Pay per position is never published, and neither is anything with names (ADR 0020). The download files follow the same rule.

## Reasons

- **A role and a view of its own:** pay must be read by fewer people than plan values. A separate view keeps the rules of ADR 0009 for plan values as they are, and the store enforces both.
- **The source's owner, not the controller's override:** the people who read pay are then set by the source and by the admin's HR role, not by the planning administration. The controller reads totals.
- **HR's salary kept apart from the planned one:** what HR delivered stays visible next to what the plan assumes. A review is then about the difference.
- **Stored totals on the cost center:** as with credits and forwarding, every page and BI read one fact table. The personnel costs page is the only place that reads pay.
- **Spread extras:** the plan is compared with the ledger month by month, and the ledger accrues the 13th salary, holiday pay and bonuses over the year.
- **Extras as a number of salaries:** "one extra salary" and "8.33 % of base pay" are the same rule. One number keeps the calculation simple, and the UI offers both ways of typing it.
- **Ceilings per person and month:** they cost nothing to model and keep contributions for high earners right. A flat rate on the salary total overstates them.
- **Raises as a rule with their own account:** a blanket raise is a decision taken on top of the plan, often late. As its own cost, it stays visible and can be changed without touching anyone's salary.
- **Small groups hidden and resolved before closing:** masking alone leaves the leak in node totals and in the published output. Charging to a larger cost center removes it, and HR decides where.
- **Approval waits for HR, submitting doesn't:** HR reviews a finished cost center, and the approver approves pay that HR has seen.

## Consequences

- New tables: `hr_pay`, `position_pay`, `position_pay_log`, `pay_staff_type`, `pay_rule`, `pay_rule_step`, `pay_setting`, `pay_small_group`, `pay_review`. New view `pay_access`; the view `pay_account` lists the round's personnel accounts. `app_user.is_hr`. The module check of `round_module` allows `personnel_costs`.
- The lock on `fact_cost_center` treats personnel accounts like forwarding accounts. Its read policy hides small groups.
- New page *Personnel costs* (Plan, after Workforce): *Pay by cost center* for HR and owners, *Rules of the round* for HR and controllers. The setup's workforce step gets the module switch. The users page gets the HR role. *Home* lists, for HR, small groups and submitted cost centers to review, and gives owners a *Pay* button next to their sheets. The cost center sheet marks the personnel accounts.
- A transfer during the year (ADR 0021) gives the receiving half the person's salary; the yearly bonus stays with the sending half.
- Not in this decision, and on the backlog:
  - encryption at rest of pay columns, with a key set up in a guided installation step;
  - HR granting or withholding pay rights on a cost center;
  - contract changes during the year (W-10);
  - rules limited to staff categories;
  - actuals of personnel accounts on small cost centers, which come from the ledger as they are.
