# Spike S-04: grid component shoot-out

Both candidates sit behind the same planning model ([src/model.ts](src/model.ts)):
- **The grid:** one cost center, "Machining" with 69 accounts and its 2025 actuals from the `standard` dataset, laid out per T-25.
- **Spreading:** typing in *Plan FY* spreads by prior-year actuals, and typing on a blue group row spreads to its accounts.
- **Special rows:** a percentage line shows its rate and amount, and the depreciation group is locked (grey).
- **Values:** blank and 0 are distinct.
- **Paste** accepts `1234.56`, `1,234.56`, `1.234,56`, `1'234.56` and `(5)`. Cells that can't take a value are listed, never dropped silently.
- **Stress tabs** have 3,900 rows × 15 columns, about 58k cells.

Versions: Glide Data Grid `6.0.4-alpha24`, RevoGrid `4.29.1`. Both are MIT.

```
cd spikes/s04_grid
npm ci
npm run dev                         # http://localhost:5174
npm run build && node smoke.mjs     # headless checks (needs: npx playwright install chromium)
```

## Automated checks (smoke.mjs)

| Check | Glide | RevoGrid |
|---|---|---|
| Renders, frozen account column | ✓ | ✓ |
| Typing *Plan FY* spreads exactly by PY | ✓ | ✓ |
| Typing straight into a selected cell keeps every key | ✗ intermittently drops the first key while the editor opens | ✓ |
| Value kept when clicking away instead of Enter | ✓ | ✓ with `applyOnClose` (default off: the value is lost) |
| Fast typing (15 ms per key) keeps every digit | ✓ | ✓ |
| Ctrl+V of a locale-mixed block, invalid cells reported | ✓ | ✓ |
| Switch to the 58k-cell tab / 20 scroll steps | 0.8 s / 1.0 s | 1.0 s / 1.3 s |
| Page errors | none | none |

## Facts outside the grids

| | Glide | RevoGrid |
|---|---|---|
| Latest stable release | 6.0.3, February 2024 (React ≤18) | 4.31.4, November 2026 |
| React 19 support | only in alpha builds (latest October 2025) | yes |
| Rendering | canvas: custom drawing for every look | DOM cells: CSS styling, browser text search |
| Peer dependencies | lodash, marked, react-responsive-carousel | none |

## Hands-on scoring sheet

Score each item 0 (fails), 1 (works, but awkward) or 2 (works well). Use real Excel or LibreOffice for the paste items.

| # | Task | Glide | RevoGrid |
|---|---|---|---|
| 1 | Type a number straight into a selected month cell, Enter moves down |2  | 2 |
| 2 | Tab / Shift+Tab / arrows / Home / End / Page Down across the grid | 2 | 2 |
| 3 | Copy 3 × 12 cells from Excel (German locale, `1.234,56`) and paste | 1 - appears not after ctrl v, only after clicking away. | 2 |
| 4 | Copy a 2,000-cell block from Excel and paste (speed, correctness) | 2 (automated: 2,004 cells, 20 ms) | 2 (automated: 2,004 cells, 128 ms) |
| 5 | Copy from the grid and paste into Excel (values and layout intact) | 1 | 1 copies hidden cells/numbers along, cannot copy the headers along with the numbers together |
| 6 | Fill-drag a value across months; fill-drag a row down | 2 | 2 |
| 7 | Type on a blue group row: the spread to accounts is visible at once | 2 | 2 |
| 8 | Type in *Plan FY*: the months fill by the prior-year pattern | 2 | 2 |
| 9 | Locked (grey) rows are clearly locked and refuse edits | 2 | 2 |
| 10 | The percentage line reads well (rate and amount) | 1 reads well, but when entering the cell, the value is there, not the percentage. On edit, that value is then saved as percentage. | 1 |
| 11 | Delete key clears to blank; typing 0 keeps 0.00 | 2 | 2 |
| 12 | Scroll the 58k-cell tab: smoothness | 2 | 2 |
| 13 | Looks like a planning sheet: alignment, totals, density | 2 | 2 |
| 14 | Undo (Ctrl+Z) after an edit or paste | 0 | 0 |
| | **Total** | **23** | **24** |

## Result

Both grids score about the same once two prototype bugs are fixed: Glide not redrawing after a paste (item 3), and the percentage cell editing the amount instead of the rate (item 10). Neither grid has undo (item 14). Undo belongs to the app anyway, as reverting a saved change batch (D-19). RevoGrid's copy (item 5) can be shaped with its copy events (`beforecopyregion`, `beforecopyapply`).

Decision: **RevoGrid** ([ADR 0006](../../docs/adr/0006-grid-component.md)). The deciding facts are Glide's intermittent loss of the first keystroke, and its maintenance state.

Weighing the result: items 1–8 are daily work for a planner, so a 0 there rules a candidate out. Maintenance (the facts above) breaks a tie.
