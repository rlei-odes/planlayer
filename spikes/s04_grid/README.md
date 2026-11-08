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
| First edit after page load keeps every key | ✗ the first key is lost: the editor code loads on first use | ✓ |
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
| 1 | Type a number straight into a selected month cell, Enter moves down | | |
| 2 | Tab / Shift+Tab / arrows / Home / End / Page Down across the grid | | |
| 3 | Copy 3 × 12 cells from Excel (German locale, `1.234,56`) and paste | | |
| 4 | Copy a 2,000-cell block from Excel and paste (speed, correctness) | | |
| 5 | Copy from the grid and paste into Excel (values and layout intact) | | |
| 6 | Fill-drag a value across months; fill-drag a row down | | |
| 7 | Type on a blue group row: the spread to accounts is visible at once | | |
| 8 | Type in *Plan FY*: the months fill by the prior-year pattern | | |
| 9 | Locked (grey) rows are clearly locked and refuse edits | | |
| 10 | The percentage line reads well (rate and amount) | | |
| 11 | Delete key clears to blank; typing 0 keeps 0.00 | | |
| 12 | Scroll the 58k-cell tab: smoothness | | |
| 13 | Looks like a planning sheet: alignment, totals, density | | |
| 14 | Undo (Ctrl+Z) after an edit or paste | | |
| | **Total** | | |

Weighing the result: items 1–8 are daily work for a planner, so a 0 there rules a candidate out. Maintenance (the facts above) breaks a tie.
