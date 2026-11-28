# 0037: Review: totals, overview and deviations

- **Status:** Proposed
- **Closes:** D-32
- **Date:** 2026-11-28

## Context

A round runs in three steps: planners **plan** their cost centers, approvers and controllers
**review** what was planned, and the controller **releases** it by publishing (T-21) and closing
the round. The workflow (ADR 0008) and the Status page (T-20) cover who has submitted and who
approves; nothing yet shows what was planned in amounts above one cost center.

- **The sheet** (T-25) shows one cost center's accounts with group subtotals (ADR 0003). It has
  no line for what the cost center costs in all, what it passes on, or what is left on it. A
  productive cost center's credits (ADR 0013) and forwarding's charges and credits (ADR 0018) are
  lines among the others; K-1 asks for a total cost line and the coverage difference.
- **The Status page** rolls up per hierarchy node by status, not by amount.
- **Published data** reaches BI only when the controller publishes, by default approved cost
  centers only (ADR 0011). An approver deciding whether to approve looks at drafts and
  submissions, which BI never sees.

Values are natural-positive (T-7); an account's role (ADR 0019) says whether it is a cost, a
revenue or a credit, decided by use for credits. Reads run as the user under row-level security
(ADR 0009), and small groups of personnel costs are hidden from readers other than HR
(ADR 0022).

## Decision

### The cycle in the app

The sidebar's round pages are grouped by the cycle: **Plan** (Capacity, Workforce, Personnel
costs, Planning, Investments, My item lists), **Review** (Overview, Deviations, Status, Log) and
**Release** (Publish). Setup, Forwarding and Responsibilities stay under *Round*. Each page keeps
its current rights; the Review pages are shown to whoever reads cost centers, as Status is.

### Totals on the sheet

Every leaf account on a cost center falls into one **total line**, decided by use as roles are
(ADR 0019):

| Total line | Accounts |
|---|---|
| **Primary costs** | cost accounts that no forwarding of the round charges |
| **Forwarded in** | charge accounts of the round's forwardings |
| **Total costs** | primary costs + forwarded in |
| **Forwarded out** | credit accounts of the round's forwardings |
| **Activity credits** | accounts credited by capacity planning |
| **Revenue** | revenue accounts |
| **Balance** | total costs − forwarded out − activity credits − revenue |

- The total lines sit at the bottom of the sheet, pinned, read only, with every column the sheet
  has: comparison columns, months or quarters, the year.
- A line that is empty on the cost center in every column is left out, except Total costs and
  Balance.
- Balance is the coverage difference of a productive cost center (K-1) and what a service cost
  center fails to pass on; a full forwarding leaves 0.
- Credits and revenue show negative, as the sheet shows credits today.

### Overview

The *Overview* page aggregates a hierarchy node.

- **Node and hierarchy:** the reader picks a node in any cost center hierarchy (ADR 0002). It
  starts on the lowest node above every cost center the reader reads: an approver's node, the top
  for a controller.
- **Reference:** one of the round's comparison columns (prior-year actuals, another round, a
  projection), chosen on the page and shared with *Deviations*. It starts on the round's first
  comparison column.
- **Cost centers view:** rows are the node's tree, sub-nodes collapsible down to cost centers.
  Columns are the plan, the reference, the deviation (plan − reference) and the deviation in
  percent, for one total line chosen on the page (Total costs at first), and the status
  (counts on nodes, the status on cost centers). A cost center opens its sheet.
- **Accounts view:** rows are the node's account groups and accounts, summed over its cost
  centers, with the same columns.
- **A selected row** shows all its total lines with plan, reference and deviation.
- Values are year totals of the round; a forecast's year is its actuals through plus its open
  months, as on the sheet.

### Deviations

The *Deviations* page lists the lines that explain a node's totals, largest first.

- **Rows:** cost center × account with plan, reference, deviation and deviation in percent,
  sorted by the size of the deviation. One switch shows cost center × account group instead.
- **Filters:** a minimum deviation in amount and in percent, total line, account group, status.
- **New and dropped:** a line with plan and no reference value (blank or 0) is marked *New*, a
  line with a reference and no plan *Dropped*; neither has a percentage. A line blank on both
  sides isn't listed.
- **From a row:** open the sheet at that line, and see and start the line's notes (ADR 0017), so
  a reviewer asks the planner there, with an @mention, and the answer stays with the line.
- **Export:** the list as shown, as an Excel workbook.

### Shared by both pages

- **Favourable or not:** a deviation is coloured by role (T-7): costs above the reference are
  unfavourable, credits and revenue above it favourable.
- **Rights:** the reads run as the user under row-level security, so every sum holds only what
  the reader may read. Personnel costs of a small group stay hidden from readers other than HR;
  a row missing them says so.
- **Every status counts:** not started, in progress, submitted and approved alike; the status
  filter narrows it.
- **Computed in SQL, classified in the domain:** plan and reference are summed per cost center ×
  account over the closure table in one query per page (ADR 0003), under the user's rights. The
  total lines, deviations and percentages come from one pure function in `planlayer.domain`,
  which the sheet uses too.

## Reasons

- **The approval needs it where it happens.** Approving is a decision on amounts. Drafts and
  submissions are only in the app store, not in `planlayer_out`, so a BI tool can't show them
  before the controller publishes. Inside Planlayer the totals sit next to the status, the sheet,
  the notes and the *Approve* button, under the same rights.
- **Total lines by use, not by setting:** forwarding and capacity already know which accounts
  they charge and credit, as for roles (ADR 0019). Nobody maintains a category per account.
- **One figure at a time on the Overview:** six total lines with plan, reference and two
  deviations each make 24 columns; one chosen line with its breakdown on selection fits a screen.
- **Deviations as the drill-down:** an approver wants to know which lines make a node's
  difference. A sorted list with thresholds answers that faster than opening sheets one by one,
  and notes on the line turn a question into a recorded explanation.
- **Year totals:** approvers decide on the year. Months stay on the sheet.

## Consequences

- K-1's open item (a total cost line and the coverage difference) is covered by the sheet's
  total lines.
- Forwardings between two cost centers of one node cancel in its Balance but appear in its Total
  costs twice, once as primary costs on the sender and once as forwarded in on the receiver.
  The Overview's hint says so.
- Actuals booked on the ERP's own forwarding accounts (in the demo 8800–8820, both sides on the
  same accounts) aren't charge or credit accounts of a forwarding, so in a comparison column
  they count as primary costs until they are classified.
- The Overview reads every cost center below a node at once, like the Status page; F-09's
  measurement on the `large` dataset covers it.
- Open, to decide before building:
  - how actuals on the ERP's forwarding accounts are split into forwarded in and out;
  - whether *Close round* moves from Status to the Release group's Publish page;
  - whether cost center rows on the Overview carry the Status page's actions (approve, reject),
    or the Overview links to Status for them.
