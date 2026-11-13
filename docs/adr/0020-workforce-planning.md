# 0020: Workforce planning

- **Status:** Accepted
- **Closes:** the design of headcount and position planning (scope.md, "Candidates for after v1")
- **Date:** 2026-11-13

## Context

A cost center's plan starts from its people as much as from its costs. Controllers ask how many full-time equivalents (FTE) and how many heads each cost center will have by month, how many of them are employees and how many external, and how many new positions the plan builds up. Some companies plan this as a few figures per cost center. Others plan each position: who sits in it, from when to when, at what contract percentage, and whether a planned position is new or replaces a leaver.

FTE and headcount are **levels**: how many there are at a point in time. Costs are flows. A year of costs is their sum; a year of headcount is where it stands at the end of the year.

Workforce planning is a third module on the cost center, after cost center planning (ADR 0007) and capacity planning (ADR 0013). It uses the same rounds, workflow, rights and fact table rules.

## Decision

### A module of the round, with one mode

- Workforce planning is the round module `workforce`. The controller turns it on in setup and picks its **mode** for the whole round:

  | Mode | Planners enter |
  |---|---|
  | `fte` | FTE per line and month |
  | `headcount` | headcount per line and month |
  | `both` | FTE and headcount per line and month |
  | `positions` | positions; FTE and headcount are rolled up from them |

- It covers every cost center planned in the round. A cost center's workforce sheet has the same status as its other sheets (ADR 0013), so editing it starts the cost center and approval locks it.
- Owners, deputies and controllers write it, and whoever reads the cost center reads it (ADR 0009). No separate responsibility.
- The setup's mode is copied with a new round's setup (ADR 0014). A mode change, or turning the module off, clears the round's workforce values and positions when the round is in setup. In an open round it is refused while there are any.

### Master data: staff types and categories

| Dataset | Fields (* required) |
|---|---|
| `staff_types` | code*, name*, external |
| `staff_categories` | code*, name* |
| `staff` | code*, cost_center_code*, title*, staff_type_code*, staff_category_code*, name, entry_date, leave_date, contract_percent |
| `workforce_actuals` | period*, cost_center_code*, staff_type_code*, staff_category_code*, headcount*, fte |

- **Staff types** say what kind of contract a person has. The migration seeds four as manual members: Employee, Apprentice, Temporary (agency) and Freelancer; the last two are **external**. Controllers edit the list on the master-data page, or a source delivers it.
- **Staff categories** are free groupings such as *Service technician* or *Office*, from the source (e.g. HR job families) or entered by hand.
- **`staff`** is HR's list of people, synced into `hr_staff` and replaced by each sync. It is where positions of existing people come from. It holds names, so row-level security restricts it to those who read the cost center.
- **`workforce_actuals`** are month-end FTE and headcount from HR, synced into the fact table under the actuals version, for the window of fiscal years like the other actuals (ADR 0014).

### Values: levels per line and month

- Planned and actual values live in `fact_workforce`, one column per dimension (ADR 0001): version, layer, measure (`fte` or `headcount`), period, entity, cost center, staff type and staff category.
- A **line** is a staff type × staff category on a cost center, e.g. *Employee · Service technician*. A sheet shows the lines with values in the round, in the prior year's actuals, or added by a planner (`workforce_line`). Positions roll up to the same lines, so every mode compares and publishes alike.
- Values are levels as at month end. Zero and blank differ, as everywhere.
- **Year and quarter are the value at their last month:** the year column shows the year-end value (December in a calendar fiscal year), and a quarter shows its last month. A typed year value fills every open month with it, and a typed quarter its three months. Nothing is spread by a basis: a level that holds from January carries through to December. Group and total rows sum their lines.
- **Forecasts** (ADR 0015): closed months show actual FTE and headcount, read only, and the store refuses plan values in them. A typed year value fills the open months.
- **Comparison columns** are the round's (ADR 0016): prior-year actuals, the year's actuals (cut at the closed months in a forecast), or another round's values. A forecast shows its full year, actual months and planned months.

### Positions

- In mode `positions`, the planner lists the cost center's positions. Each position has:

  | Field | Notes |
  |---|---|
  | Position number | optional; HR's number, unique in the round where given |
  | Title | required, e.g. *Service technician field* |
  | Name | optional: the person in it, blank for a vacancy |
  | Staff type, category | required |
  | Entry date, leave date | optional; blank means "before the year" and "stays" |
  | Contract % | 0 < % ≤ 100; required for internal types, optional for external |
  | Plan position | a position still to be filled, not a person employed today |
  | Replacement | a plan position that replaces a leaver; only a plan position can be one |
  | Description | free text |

- **Month-end snapshot:** a position counts in a month if it is active on the month's last day. Entry on 15 March counts from March; leaving on 31 March still counts in March; leaving on 15 March does not.
- **Roll-up:** per line and open month, headcount is the number of positions counted, and FTE the sum of their contract percentages / 100. A line with positions gets a value in every open month, 0 once everyone has left. FTE stays blank in a month where none of the line's counted positions has a contract %, e.g. freelancers without one.
- The rolled-up values are **stored** in `fact_workforce`, recomputed in the same transaction as every position change, so they share its change batch. This is the same pattern as the credits of capacity planning. The sheet, comparisons, the status of the cost center, publishing and BI read one table.
- **Build-up:** the positions page shows, per month and for the year, the headcount, the FTE, new positions (plan positions that are not replacements) entering, replacements entering, and people leaving. These figures are computed on read, from the positions.
- **Starting from HR:** *Add staff from HR* takes the cost center's people in `hr_staff` who are active in the fiscal year and not yet in the round (by position number) as positions, with no plan flag. Controllers can do this for every planned cost center at once in setup.
- Positions are audited: a trigger writes every change, as old and new row, into `workforce_position_log` with the request's actor and change batch.

### Carrying over

The checklist item *Workforce* (ADR 0014) copies the lines added to sheets and the positions to a new round. It skips positions whose leave date is before the new fiscal year. In a positions round, the roll-up is recomputed. The item *Plan values* also copies typed FTE and headcount, month by month and without uplift, into the target's open months, as it does with planned hours (ADR 0016).

### Publishing

A publish writes `fact_plan_workforce`: one row per round × cost center × staff type × staff category × month × measure (`fte`, `headcount`), with the cost center's status and `origin` (`plan`, or `actual` in a forecast's closed months). `dim_staff_type` and `dim_staff_category` name the codes, and `v_plan_workforce` joins them. **Positions are not published:** they carry names, and the aggregate is what BI reports.

## Reasons

- **One module with modes, not three modules:** FTE, headcount and positions answer the same question at different depths. One mode per round keeps the status page, publishing and comparisons uniform. Different modes per cost center can be added on top of it.
- **Type × category as the line:** it is the grain positions roll up to, so a simple round and a positions round can be compared line by line. It also separates employees from external staff, which controllers report apart.
- **Year-end values, filled rather than spread:** headcount is a level. The year's figure is where it ends, and a value typed for the year holds for every month. Spreading a level by a seasonality profile would invent hiring and leaving.
- **Month-end snapshot:** it agrees with the year-end value (December is the year), gives whole heads, and matches how HR reports headcount. Pro-rata FTE by days is what costs need, and belongs to a personnel cost module.
- **Stored roll-up:** as with credits (ADR 0013), the sheet, publishing and BI read one fact table, and the lock trigger moves the cost center's status on every write.
- **No names in the output:** positions carry personal data. Inside Planlayer they are visible only to those who read the cost center; the warehouse gets the aggregates.
- **Types as an editable list:** companies distinguish more than employees and freelancers (apprentices, agency staff, interns). A flag *external* is what the rules need, so any list works.

## Consequences

- New dimensions `staff_type` (6) and `staff_category` (7); `member.external` for staff types. New measures `fte` (3) and `headcount` (4). `round_module.mode` holds the workforce mode.
- New tables: `fact_workforce`, `workforce_change_log`, `workforce_line`, `workforce_position`, `workforce_position_log`, `hr_staff`. Row-level security on all but the logs reads `cost_center_access`. Lock triggers follow those of capacity planning: the round open (or in setup for controllers), the cost center planned, and its status editable. The closed-months trigger of ADR 0015 also guards `fact_workforce`.
- New datasets: `staff_types`, `staff_categories`, `staff`, `workforce_actuals`.
- New page *Workforce* (Plan, before Planning). There is a setup section for the mode and *Add staff from HR*, and staff types and categories appear on the master-data page.
- New output objects: `fact_plan_workforce`, `dim_staff_type`, `dim_staff_category`, `v_plan_workforce` (`docs/output.md`).
- Not in this decision, and on the backlog under W-items: personnel costs from positions (salary bands, pro-rata FTE, raises); a position replacing a named leaver; contract changes during the year; long absences (counted in headcount, not FTE); workforce figures as factors for cost forwarding (K-3), e.g. IT costs per employee by category; modes per cost center; undo on the positions page.
