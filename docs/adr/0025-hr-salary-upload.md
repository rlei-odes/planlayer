# 0025: HR's salary upload

- **Status:** Accepted
- **Amends:** ADR 0022 (HR's salary from the staff list), ADR 0012 (the dataset `staff`)
- **Date:** 2026-11-15

## Context

HR's salaries reach Planlayer through the dataset `staff` and its optional field `monthly_salary` (ADR 0022). That puts pay in the admin's source query and in the sync, and assumes the warehouse holds pay at all. Many companies keep pay in the payroll system only. Their HR exports it as a spreadsheet and wouldn't hand it to the person who runs the sync.

## Decision

### Salaries from the source, or from HR

- One installation setting, `installation.pay_from_source`, **off by default**. The admin switches it on the *Staff (HR)* dataset under *Master data*, *Sources*.
- **On:** the sync reads the staff list's pay fields (`monthly_salary`) and replaces HR's salaries in the staff list (`hr_pay`), as before. HR doesn't upload.
- **Off:** the sync doesn't read the pay fields, even when a column is mapped to them, and leaves `hr_pay` as it is. The admin's mapped preview leaves them out too. HR uploads the salaries on the personnel costs page.

### The upload

- The personnel costs page has an *Upload* tab for HR, per round, while the round is in setup or open and the setting is off.
- **Template:** HR downloads the round's people as CSV, Excel or Parquet: everyone in HR's staff list and every position of the round taken from HR, one row per person number. Columns: `number`, `name`, `title`, `cost_center`, `monthly_salary`. The salary is blank, so the template holds no pay.
- **File:** HR uploads CSV (comma, semicolon or tab, UTF-8 or Windows-1252), Excel (`.xlsx`, first sheet) or Parquet. Columns are found by name, ignoring case and spaces. `number` and `monthly_salary` are required, `cost_center` is compared when present, and other columns are ignored and listed. Amounts are read as the grid reads typed amounts (`4500.50`, `4.500,50`, `4'500.50`). A salary is non-negative with at most two decimals.
- **Matching** is by the person's number:
  - **Round positions:** every position of the round with that number, both halves of a transfer included. Plan positions carry Planlayer's numbers and are never matched.
  - **Staff list:** the person in HR's staff list with that number.
- **Check first:** the upload is checked before anything is written. Each row gets one outcome:

  | Outcome | Meaning | Written on *Apply* |
  |---|---|---|
  | Changed | a position's HR salary differs from the file | HR's salary on the positions, and in the staff list |
  | Unchanged | the positions already have it | the staff list, if it differs there |
  | Not in the round | in HR's staff list, no position in the round | the staff list |
  | No salary | the salary cell is blank | nothing: a blank leaves the salary as it is |
  | Unknown number | neither in the staff list nor the round | nothing |
  | Failed | no number, a number twice in the file, an unreadable amount, or every position's cost center locked | nothing |

  A row whose `cost_center` differs from the position's (or the staff list's) is applied and flagged, because the number identifies the person.
- **Locks:** HR's salary on a position follows the pay locks of ADR 0022 and 0024 for HR. A cost center not planned in the round, submitted or approved isn't changed, and the row says so.
- **Apply:** HR applies the checked file. The server reads the file again and writes what the check shows, in one change batch: HR's salary on the positions (`position_pay.hr_salary`, logged in `position_pay_log`), the staff list's salary (`hr_pay`), and the recomputed totals. HR makes the change, so HR's review isn't withdrawn. Nothing about the upload is stored beyond what it writes.

## Reasons

- **A setting, not both at once:** with salaries from the source, the next sync would overwrite what HR uploaded. One owner of `hr_pay` at a time keeps it clear whose figure it is.
- **Off by default:** pay stays out of the admin's query unless the company decides otherwise, which matches ADR 0022's aim that as few people as possible handle it.
- **HR's salary, not the planned one:** the upload is HR's figure of what people earn. The planned salary stays with the people HR hands pay over to (ADR 0024).
- **Positions and the staff list:** the round gets the salaries at once, and people taken later (*Add staff from HR*, the next round) get them without another upload.
- **A blank template:** a downloaded file with everyone's pay would be the easiest leak; HR fills it from payroll.
- **Check, then apply with the file again:** nothing with pay is held between the two steps, and HR sees every row's outcome before anything changes.
- **The number decides:** cost centers in HR's export may lag behind the plan (a transfer, a reorganisation). Refusing those rows would block the upload for the wrong reason; flagging them lets HR check.

## Consequences

- `installation.pay_from_source` (migration 0024); `sync.PAY_FIELDS`, `pay_from_source`, `staff_mapping`.
- Endpoints `PUT /api/datasets/staff/pay-from-source` (admin) and, under `/api/versions/{code}/personnel/salaries`, the summary, `template.{csv,xlsx,parquet}`, `check` and `apply` (HR). The uploaded file is the request body.
- `openpyxl` for Excel files.
- `planlayer seed-dev-mappings` switches the setting on, because the test warehouse delivers salaries.
- Not in this decision, and on the backlog: uploading bonuses or planned salaries, and removing an HR salary by upload (a blank leaves it).
