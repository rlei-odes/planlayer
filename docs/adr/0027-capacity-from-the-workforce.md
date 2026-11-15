# 0027: Capacity from the workforce

- **Status:** Accepted
- **Amends:** ADR 0013 (productive cost centers, capacity lines), using ADR 0020 (positions)
- **Date:** 2026-11-15

## Context

A productive cost center's capacity sheet (ADR 0013) has one line per work center × activity type, with a plan rate; planned hours × rate are credited to the cost center. The hours are typed per line. That fits machines. Where people do the work, the hours follow from the people:

- An engineering department books its hours to customer projects or internal R&D, two activity types at their own rates.
- An assembly cost center outputs no more hours than its workers provide.
- Some companies record machine hours and, separately, the hours of the machines' operators.

A round with workforce planning by positions (ADR 0020) knows every position of the cost center: contract percentage, entry and leave dates. What is missing is how many of a position's hours are productive, and where they go.

## Decision

### Two sources of hours per productive cost center

- On the setup page's *Cost centers* tab, two switches replace *Productive*: **Work centers** (hours typed per line, as before) and **Workforce** (hours from the positions). Either makes the cost center productive (the module `capacity`, ADR 0013); both together suit mixed recording.
- *Workforce* needs workforce planning by positions in the round. Without *Work centers*, the sheet takes no typed hours; a save that would leave typed hours there is refused until they are cleared.
- The controller sets the round's **hours per FTE a year** (e.g. working days less holidays and vacation, × 8 hours), in the setup's workforce section. Required once a cost center plans hours from the workforce.

### Productive share and line per position

- Each position has a **productive share** (0–100 %): the part of its hours that is output. A manager gets 0 %, an assembly worker e.g. 80 % (the rest is meetings, cleaning up, preparing tools).
- Each position's hours go to **one line** of its cost center (work center × activity type), and the line's plan rate prices them.
- The cost center has a **default line and default share**; a position may have its own of either. A position without a share gives no hours.
- Owners, deputies, controllers and capacity planners set them on the capacity page's **Workforce** tab, which shows the positions, their hours and the hours per line. Who reads the cost center's capacity reads the tab.

### The hours

- Per position and month: hours per FTE × contract % × productive share × the month's weight × the share of the month's days the position is active. A position without a contract % (an external one) counts as full time.
- The month's weight is the line's spread (ADR 0013): even by default for these lines, or a seasonality profile chosen on the sheet, e.g. one with the summer holidays.
- The hours of a line's positions are summed per month, rounded per cell, and written to the line's planned hours (`fact_capacity`) in the round's open months. Such a line is **staffed** (`capacity_line.staffed`): its hours are read only on the sheet and marked *Workforce*; its rate, spread and state stay editable. A line no longer receiving hours from positions loses them.
- Credits, the cost center's status, forecasts, publishing and comparisons work as for typed hours: the hours are planned hours.
- The hours are recomputed in the same transaction as every change that moves them: a productive share or line, the defaults, a position (saved, removed, taken from HR, transferred), a line's spread or state, the setup (switches, hours per FTE), and undo on the sheet. A full transfer leaves the position's own share and line behind; the receiving cost center's defaults apply.

### Storage and guards

- `round_module.fte_hours` (module `capacity`); `capacity_unit` per round and cost center: the two switches, the default line and share; `capacity_position` per position: its own share and line.
- Row-level security on both tables reads `capacity_access`, as the capacity lines. A lock trigger applies the capacity locks (round open, or in setup for controllers; the cost center productive and editable). A trigger logs every change as old and new row in `capacity_staff_log`, with the actor and change batch.
- Settings refer to stored capacity lines; a line removed (undo of an added line) clears the reference.
- A new round's *Capacity* item copies the hours per FTE, the switches and defaults, and the positions' own settings, matched by number and cost center; the hours are recomputed there. Without positions in the new round, the cost center's hours are typed only.

## Reasons

- **Hours written to the existing lines:** the rate, the credits, the published hours, forecasts and comparisons exist once. A line staffed by positions differs from a typed one only in where its hours come from.
- **One line per position:** the smallest model that covers assembly, mixed recording and most engineering departments, with one choice per person. A split of one person over several lines is on the backlog.
- **A default per cost center:** most positions of a cost center share the line and share; only the exceptions need input.
- **Hours per FTE per round:** working time is a planning assumption of the year, like rates. Contract % scales it per position.
- **Settings in their own tables:** capacity planners write capacity, not positions; the tables follow capacity's rights and locks while positions follow the workforce's.
- **Recomputed and stored, not computed on read:** the same pattern as credits (ADR 0013) and the workforce roll-up (ADR 0020).

## Consequences

- Migration 0026: `round_module.fte_hours`, `capacity_line.staffed`, `capacity_unit`, `capacity_position`, `capacity_staff_log`, their policies and triggers.
- Setup API: `capacity_work_centers`, `capacity_workforce` and `fte_hours` replace `productive_cost_centers`.
- Capacity API: units carry `staff_hours`; sheet rows carry `staffed`, the sheet `typed_hours` and `staff_hours`; `GET`/`PUT …/capacity/{cc}/workforce` and `PUT …/workforce/positions/{id}`.
- The setup page's read now runs with the user, as every request does: the new tables are under row-level security.
- Not in this decision, and on the backlog under K-2: one position split over several lines; hours per FTE per entity or cost center; hours from positions as a limit next to typed machine hours.
