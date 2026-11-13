# gen-testdata

Deterministic test data for Planlayer: **Nordwerk Industrietechnik**, a fictional manufacturer of industrial pumps and valves with about 400 employees and €100M revenue. Same seed, same data.

The generator writes a simulated customer warehouse into a Postgres database:
- **`bronze`:** raw tables shaped like an ERP extract, with German and English column names, local currencies, ledger signs (debit positive), document numbers and dated tranches.
- **`gold`:** one view per dataset, in EUR, with the hierarchy current as of the anchor date. An admin points Planlayer at these views (T-16).

```
uv run gen-testdata --db postgresql://postgres:postgres@localhost:5432/dwh --variant standard
uv run gen-testdata --db ... --variant small --fiscal-start-month 4 --out out/small-april
```

| Option | Values |
|---|---|
| `--variant` | `small`, `standard` (default), `large` |
| `--date-mode` | `fixed` (default; actuals end June 2026), `relative` (actuals end last month) |
| `--fiscal-start-month` | 1–12, default 1. The actuals cover full fiscal years plus the current year to date. |
| `--seed` | default 42 |
| `--out` | also export every gold view as CSV and Parquet (amounts as decimals) |

Each run replaces the generator's own tables and views; nothing else in the schemas is touched.

## Variants

| Variant | Entities | Cost centers | Accounts | SKUs | Periods | Fact rows | Time |
|---|---|---|---|---|---|---|---|
| `small` | DE | 15 | 34 | 30 | 1 year + YTD | ~7k | <1 s |
| `standard` | DE, CH, PL, US | 63 | 158 | 400 | 3 years + YTD | ~310k | ~2 s |
| `large` | DE, CH, PL, US | 1,241 | 158 | 8,000 | 5 years + YTD | ~10.4M | ~1 min |

## The company

- **Entities:** DE (parent, EUR), CH (CHF), PL (PLN), US (USD). Bronze holds local currency and a monthly rate table, and gold converts to EUR.
- **Cost centers:** company → division → department → cost center, ragged: corporate cost centers hang directly under their division. There's an alternate hierarchy by function (production, sales, admin). Each cost center has an owner, the department head.
- **Accounts:** a P&L tree, root → 5 sections → 14 groups (the aggregate planning lines) → 158 leaves (one, 6950, without bookings). Accounts are typed `revenue` (credit side) or `cost`. The section *Internal activity allocation* holds the four accounts capacity planning credits (`kontoart` `L`), and the three accounts of *Internal cost forwarding* (8800–8820); the ledger books the actual credits and forwardings on them (see below).
- **Products:** 5 lines → 40 groups → 400 SKUs. Each SKU sells in 3–9 of the 12 countries.
- **Countries:** 12 countries in 4 regions. Each country is served by one entity and booked on one sales cost center.
- **Capacity (ADR 0013):** four activity types (machine hours, measurement and quality control, repair work, engineering hours), each crediting one of those accounts, and 32 work centers on 27 production and engineering cost centers with an hourly rate per activity type they output (a few output two). Gold views: `v_activity_types`, `v_work_centers`, `v_work_center_rates`, `v_capacity_actuals`.

## Patterns in the actuals

- **Growth and seasonality:** 3–4 % growth a year. Production has an August shutdown (factor ~0.55) and a quieter December. Sales peak in Q4. Personnel costs have bonus months in June and December.
- **Activity credits (TD-21):** every month, each work center credits its cost center actual hours × rate per activity type, on the activity type's account, one posting per work center. They are credit-side in the ledger (negative) and positive in `v_gl_actuals_natural`, which flips `kontoart` `E` and `L`, as Planlayer plans credits natural-positive.
- **Cost forwarding (TD-22):** every month, one balanced document per forwarding: receivers debited, the source credited with the sum. Facilities charges building A (production hall, 95 a year per m²) and building B (offices, 150) on 8810; IT charges PCs (1,500 a year) and CAD licences (6,000) on 8820, leaving the rest on IT; Tooling, last, passes on its month's balance, charges received included, 40/25/20/15 % to Machining, Assembly Line 1, Assembly Line 2 and Painting on 8800.
- **Irregular entries:** noise, one-off spikes (×3), and negative corrections.
- **Zero vs. missing:** some months net to exactly 0.00 through a posting and its reversal, which is different from months with no posting at all. Sales have returns (negative quantity) and invoice/credit-note pairs that net to zero.
- **Actual hours:** per work center, activity type and month, with the production season (August shutdown) and ±10 % noise.
- **Revenue in two places:** sales revenue is booked in the GL per entity, month and product line, on the country's sales cost center. GL and sales revenue agree within FX rounding (< €1 per entity and month).

## Special cases

The tests rely on these; their names are constants in `master.py`.

| Case | Where |
|---|---|
| Moved cost center | *Quality Assurance*: Pump Production → Pump Engineering at the start of the prior fiscal year |
| New cost center | *Digital Service*: valid from six months into the prior fiscal year, with no owner |
| Closed cost center | *Legacy Assembly* (US): valid to the end of the prior fiscal year, with no actuals after |
| Renamed department | *Pump Plant Poland* → *Pump Plant Wrocław* at the start of the current fiscal year |
| No owner | *Digital Service*, *Tooling*, *Legal* |
| Owner without a user | *Test Bench*: `k.weber@nordwerk.example` is not in `USERS` |
| Created for the next year | *Additive Manufacturing* (DE1290, Pump Production): valid from the start of the next fiscal year, so it has no actuals; the hierarchy view shows it under its first parent. Standard and large only. |
| Closed at the end of the current year | *Shift Management*: valid to the end of the current fiscal year, so the next year's round doesn't include it |
| Account without bookings | *6950 Cloud subscriptions* in IT and software: new, nothing booked on it. Standard and large only. |

The `small` variant keeps the moved and the new cost center.

## Gold views

| View | Columns |
|---|---|
| `v_entities` | entity_code, entity_name, country_code, local_currency |
| `v_cost_centers` | cost_center_code, cost_center_name, description, entity_code, owner, function, valid_from, valid_to |
| `v_accounts` | account_code, account_name, account_type |
| `v_products` | product_code, product_name, group_code, line_code |
| `v_countries` | country_code, country_name, region_code, entity_code |
| `v_*_hierarchy` | member_code, member_name, parent_code, is_group, sort_order, for `cost_center`, `cost_center_function`, `account`, `product` and `country` |
| `v_gl_actuals_ledger` | posting_month, company_code, cost_center, gl_account, amount_eur (ledger signs) |
| `v_gl_actuals_natural` | the same in natural signs (revenue positive) |
| `v_sales_actuals` | period, entity_code, country_code, product_code, measure (`quantity`, `revenue`), value |
| `v_activity_types` | activity_type_code, activity_type_name, account_code, variable_account_code |
| `v_work_centers` | work_center_code, work_center_name, cost_center_code |
| `v_work_center_rates` | work_center_code, activity_type_code, rate |
| `v_capacity_actuals` | posting_month, work_center, activity_type, actual_hours |

The fact views name their columns as a warehouse would, not as Planlayer's fields, so mapping them is part of setting up a source.
