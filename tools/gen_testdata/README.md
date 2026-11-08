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
| `small` | DE | 15 | 27 | 30 | 1 year + YTD | ~7k | <1 s |
| `standard` | DE, CH, PL, US | 62 | 150 | 400 | 3 years + YTD | ~310k | ~2 s |
| `large` | DE, CH, PL, US | 1,240 | 150 | 8,000 | 5 years + YTD | ~10.4M | ~1 min |

## The company

- **Entities:** DE (parent, EUR), CH (CHF), PL (PLN), US (USD). Bronze holds local currency and a monthly rate table, and gold converts to EUR.
- **Cost centers:** company → division → department → cost center, ragged: corporate cost centers hang directly under their division. There's an alternate hierarchy by function (production, sales, admin). Each cost center has an owner, the department head.
- **Accounts:** a P&L tree, root → 4 sections → 12 groups (the aggregate planning lines) → 150 leaves. Accounts are typed `revenue` (credit side) or `cost`.
- **Products:** 5 lines → 40 groups → 400 SKUs. Each SKU sells in 3–9 of the 12 countries.
- **Countries:** 12 countries in 4 regions. Each country is served by one entity and booked on one sales cost center.

## Patterns in the actuals

- **Growth and seasonality:** 3–4 % growth a year. Production has an August shutdown (factor ~0.55) and a quieter December. Sales peak in Q4. Personnel costs have bonus months in June and December.
- **Irregular entries:** noise, one-off spikes (×3), and negative corrections.
- **Zero vs. missing:** some months net to exactly 0.00 through a posting and its reversal, which is different from months with no posting at all. Sales have returns (negative quantity) and invoice/credit-note pairs that net to zero.
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
| `v_gl_actuals_ledger` | period, entity_code, cost_center_code, account_code, amount (EUR, ledger signs) |
| `v_gl_actuals_natural` | the same in natural signs (revenue positive) |
| `v_sales_actuals` | period, entity_code, country_code, product_code, measure (`quantity`, `revenue`), value |
