# Scenario: cost forwarding in Budget 2026

A walkthrough on the standard test data (Nordwerk, `tools/gen_testdata`) for passing service costs on to the cost centers that use them. Each step names what to do and what to check. It exercises [ADR 0018](../adr/0018-cost-forwarding.md).

Starting point: round B2026 open, actuals synced, and the demo forwardings seeded:

```
planlayer seed-dev-forwarding --round B2026
```

| In the source (TD-22) | What it is |
|---|---|
| 8800 *Forwarded tooling costs* | Tooling's actual forwarding: credited on DE1090, charged to Machining, Assembly Line 1 and 2, Painting |
| 8810 *Forwarded building costs* | Facilities charging two buildings by m² |
| 8820 *Forwarded IT services* | IT charging PCs and CAD licences |

All three sit in the group *Internal cost forwarding*. The seed puts the plan forwardings' accounts (`ALOC-<code>` and `ALOC-<code>-CR`) in the same group and marks 8800–8820 not planned in B2026.

| Forwarding (TD-23) | Method | Source | Shows |
|---|---|---|---|
| BLDG-A, BLDG-B | Rate × quantity (95 and 150 per m² and year) | DE1280 Facilities | two factors of one source; a small under-coverage |
| IT-PC, IT-CAD | Rate × quantity (1,500 per PC, 6,000 per licence) | DE1260 IT | most of IT stays on IT |
| MGMT | Fixed amount (180,000 a year), portions 3:2:2:1 | DE1230 Executive Office | a fixed amount, whatever the source plans |
| TOOLS | All costs, 40/25/20/15 % | DE1090 Tooling | the source passes on everything, building and CAD charges included |

## 1. The forwarding page

Log in as `controller@`, choose *Budget 2026*, open *Round → Forwarding*.

1. The table lists the six forwardings with method, source, basis, receivers and what each passes on.
2. *Sources*: Tooling remains at 0.00; Facilities keeps about 25,000 (its costs exceed what the m² rates cover); IT keeps most of its costs; Executive Office keeps everything but the fee.
3. Click *TOOLS*. The editor shows percentages 40/25/20/15, 100 % in total, each receiver's year amount and status. Assembly Line 2 may be approved: it still takes the charge.

## 2. Both sides on the sheets

Open *Planning*.

1. **DE1010 Machining**, at the bottom: *Internal cost forwarding* holds 8800–8820 with last year's actuals, marked *Not planned*, and four *Charged* lines: building A, PCs, management fee, tooling. They are read only. Hover a badge: it names the forwarding and "charged by DE1090 Tooling".
2. **DE1090 Tooling**: the *Passed on* line (ALOC-TOOLS-CR) is negative and green across, like the activity credits, and the group's subtotal is the building and CAD charges received less what is passed on. Last year's activity credits in the comparison column show negative too.
3. Type into a forwarded line: nothing opens. Credits and charges are the controller's.

## 3. A full forwarding follows its source

1. On DE1090 Tooling, change a cost line, e.g. a material account's year total.
2. Back on *Forwarding*: TOOLS passes on the new total, and Tooling still keeps 0.00.
3. On DE1010 Machining, the tooling charge moved by 40 % of the change, although whoever changed Tooling may not write Machining: the forwarding writes as the system user, in the same change batch.

## 4. Change and refuse

1. On *TOOLS*, switch *Shares as* to *Portions* and enter 4/2/2/1: the year amounts preview with ≈, and *Save and recompute* stores them.
2. Make the percentages add up to 101: the total turns red and *Save* is off.
3. Create any forwarding from DE1010 Machining: *Save* is refused, because Machining receives by shares from TOOLS and so passes nothing on. In the receiver picker of an *All costs* or *Fixed amount* forwarding, the other forwardings' sources are greyed out for the same reason.
4. *+ New forwarding*: type the code `MAINT` and the name: the accounts follow as `ALOC-MAINT` and `ALOC-MAINT-CR`, named *Maintenance*, until you type into them.
5. *Add receivers*: tick *Pump Production* for all its cost centers, filter by "Sales" and shift-click a range, then *Add*. Copy a column of numbers from a spreadsheet, click the first quantity and paste: it fills down. Enter and the arrow keys move between cells. A receiver left blank or at 0 gets nothing.

## 5. Into the next round

On *Setup → New round* from B2026, the checklist has *Cost forwarding*. With it ticked, the new round has the forwardings, recomputed from its own plan values.
