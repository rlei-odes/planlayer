# 0006: RevoGrid as the planning grid

- **Status:** Accepted
- **Closes:** D-17
- **Date:** 2026-11-08

## Context

The planning page shows one planning unit at a time (T-25): accounts × months with group subtotals, prior-year values, locked rows and percentage lines. Planners type, paste from Excel and LibreOffice, and fill-drag. The grid component must be MIT-compatible (T-8) and work with React 19 (T-9).

## Decision

The planning grid uses **RevoGrid** (`@revolist/react-datagrid`, MIT), configured with:
- **`applyOnClose`:** a typed value is saved when the editor closes by any route except Escape, including clicking away. The default discards it.
- **`range`:** range selection, fill-drag, and paste of blocks.
- **Edit events intercepted** (`beforeedit`, `beforerangeedit`): the grid never changes its own data. Every edit, paste and fill goes through the planning model (parse, spread, validate), and the grid shows the result.
- **Copy shaped through the copy events,** so copied blocks carry what the planner expects, including headers.

## Reasons

From spike S-04 ([spikes/s04_grid](../../spikes/s04_grid/README.md)), one cost center with real data:
- **Hands-on scores are about even** (24 vs. 23 of 28). Both pass typing, keyboard flow, locale-mixed paste, fill-drag, spreading on group and year cells, locked rows, and 58k-cell scrolling.
- **Keystrokes:** the other candidate intermittently drops the first keystroke when typing straight into a selected cell, a defect a planner entering numbers would hit daily. RevoGrid kept every key in every test.
- **Maintenance:** RevoGrid releases monthly and supports React 19. The other candidate has had no stable release since February 2024, and supports React 19 only in alpha builds.
- **Cells are DOM elements:** styling is CSS, and browser find and accessibility tools work on the cells.

## Consequences

- **Undo** is not provided by the grid and is built in the app, as reverting the last saved change batch (D-19, E-05).
- **Copy:** E-05 implements the copy handler, values plus row and column headers.
- **The percentage line** edits its rate, not its computed amount. The model passes the rate to the editor.
- **Upgrades:** RevoGrid is pinned exactly and updated deliberately; the smoke test from the spike becomes an end-to-end test of the grid page.
