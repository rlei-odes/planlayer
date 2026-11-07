# Brief: Open-Source Web Planning App (Postgres-first)

*Prepared November 2026. Status: exploration / go–no-go input.*

## 1. The idea in one paragraph

An open-source, browser-based planning tool for corporate budgeting and forecasting: users get an Excel-like grid (enter, paste, fill-drag, spread totals) for things like sales by country × quarter or cost center budgets. Plan data lives in plain Postgres tables, with support for other SQL databases later. It covers versions and scenarios, actuals vs. plan, simple approval workflow, and permissions. It is intended to be self-hostable and readable by any BI tool.

## 2. Who it's for

> Is planning in your ERP clumsy, expensive to set up, or not possible at all?
> Do you already have your data in a data warehouse or data lake for analytics?
> Is getting budget and plan values into the right place tedious admin work?
>
> **Then [Project name] is for you.**

**Typical organization:** a mid-sized company whose actuals already live in a warehouse or lake (Postgres, Snowflake, BigQuery, Databricks, etc.) and whose planning today runs on Excel files sent around by email, then collected and loaded by hand.

**Typical users:**

- **Admin / controller:** connects the data source, defines what gets planned and by whom, and runs the planning cycle.
- **Planners:** cost center owners and sales managers who see their actuals and enter plan values for their area only.
- **Approvers / management:** review, compare versions, approve, and lock.

## 3. Assumptions

**Scope**

- Cost center planning and sales planning are the main areas for v1.
- Income statement and balance sheet planning come later. The balance sheet adds hard constraints (it has to balance and links to the income statement), so it should stay out of early versions.

**Data connection: "bring your own warehouse"**

- The admin connects their existing database containing current values (actuals and master data). The app does not replace the warehouse; it reads from it.
- The admin provides queries or views for the cost center list, the account structure, and actuals. We publish a **data contract**: the required column names and shapes for each view, so the app knows what to expect.
- **To solve:** how hierarchies and interim sums are delivered. Options are parent–child columns, fixed level columns (level 1, level 2, …), or letting the app compute all sums from leaf rows. Computing sums in the app is the most consistent choice; source-provided sums would then serve only as a reconciliation check.
- **To solve:** where plan values end up. Storing them in the app's own Postgres is simplest, but the pitch promises getting values "into the right place," so write-back or scheduled export to the warehouse (a table or view the BI tools already read) is likely a core feature.

**Built-in dimensions**

- Provided by default so admins don't have to define them: **time** (year, quarter, month, optionally day), **version** (Plan 1, Plan 2, Forecast, …), and **status** (draft, submitted, approved, locked).
- Business dimensions (cost center, account, customer, product, country) come from the admin's data.

**Planning environment**

- Planners see actuals (e.g., prior year, year-to-date) next to the plan columns they are editing, in the same grid.

**Plan grain (open)**

- Do planners plan at the lowest account level or at aggregate (sum) level? Likely answer: **configurable per planning area**. For example, cost center planning on a few aggregated cost lines, sales planning on product groups rather than individual products.
- Planning at aggregate level means actuals are rolled up to the same level for comparison. Optionally, aggregate plan values can be spread down to leaf level (by prior-year proportions) for systems that need leaf data.

**Percentage mode**

- For income-statement-style planning: plan sales as an absolute value, then enter other lines as a percentage of a reference line (e.g., cost of goods = 62% of sales).
- Implications: each cell stores its **input type** (value or percentage) and its reference line. The absolute amount is derived and recalculated when the reference changes. Both the percentage and the resulting amount are visible in the grid.
- This is the first step toward driver-based planning. Keep it deliberately limited (percentage of one line) until real usage shows what more is needed, to avoid building a formula engine too early.

## 4. Verdict: is it worth being interested?

**Yes, with a clearly narrowed scope.** The research found no maintained open-source project that combines a web planning grid with a Postgres-native data model. The space splits into four groups, none of which covers this:

- Commercial EPM/xP&A tools (Anaplan, Pigment, Jedox, Board, Adaptive, OneStream) own the web-grid experience, at five- to seven-figure license costs.
- The few open-source EPM projects chose Excel as the UI (Konsolidat) or no UI at all (Budget & Forecast for Claude Code).
- Open-source ERPs (ERPNext, Odoo/OCA) have budget *control* modules (one account per cost center, with warn/stop), not planning.
- Most "open-source budgeting" results are personal finance (Actual, Firefly III, GnuCash) and irrelevant here.

The main risk is not competition but **scope creep**: planning tools tend to grow into consolidation, allocations, drivers, and formula engines. A focused v1 is what makes this feasible.

## 5. Landscape

| Project | License | Stack | UI | Focus | Relevance |
|---|---|---|---|---|---|
| **Konsolidat** | MIT | Airbyte, ClickHouse, dbt, Frappe v16, Cube | Excel add-in for data entry/reporting + Frappe web UI for configuration | Group consolidation (IFRS/GAAP), layered budgets, variance | High: study the budget model and testing approach |
| **Budget & Forecast for Claude Code** | MIT | Plain SQL on Postgres/PGlite, Node CLI | None (agent commands + read-only pages) | SME budget/forecast, versions, covenants, close | High: closest Postgres data model |
| **Plannifico** | Apache 2.0 | Java server + client console, REST API | Console | Multidimensional plans, what-if, top-down/bottom-up | Medium: concepts only; check activity |
| ERPNext Budget | GPL | Frappe | Forms | Spend control per cost center/account | Low (ERP-bound) |
| Odoo OCA `account_budget_oca` | LGPL | Odoo | Forms | Analytic budgets | Low (ERP-bound) |
| fincube | Apache 2.0 | — | — | Empty repo (README only) | None |

### Correction on Konsolidat's UI

Konsolidat **does** have a web UI: its stack includes a Frappe v16 application providing UI, API, 2FA, RBAC, audit, and workflow. That UI is for configuration (dimensions, measures, datasets, scenarios, budget cycles/sheets/layers, spread profiles, connectors, roles, period close). Plan values are read and written from **Excel** via worksheet functions (`=K.EPM()` to read, `=K.EPMSAVE()` to write budgets back). So it has a web admin UI but not a web planning grid.

## 6. What to learn from the open-source backends

### Konsolidat (most mature engine)

- **Layered budgets:** four layers (base, challenge, management, board) stored separately and summed at query time, with an audit trail. This handles top-down adjustments without overwriting bottom-up input. Strongly worth copying.
- **Budget cycles with a lock gate** and **spread profiles** (annual → months). Their tests assert that spreads have twelve periods and sum back to the annual figure.
- **Dimension registry:** dimensions and measures are registered in the app, which generates warehouse columns and dbt variables. This is a pattern for user-defined dimensions without hand-written migrations.
- **Canonical staging schema:** every ERP lands in the same set of canonical models (accounts, entities, periods, rates, GL entries, trial balance, budget entries). This is a clean way to load actuals from any source.
- **Tests as product feature:** a large dbt assertion suite (e.g., unique grain of the budget fact, roll-ups tie to leaves, no circular hierarchies) gates period sign-off.
- **Honest gaps:** rolling forecasts are on their roadmap, and cost allocation was removed in Sept 2026 for a redesign. Both are areas where a planning-first tool can differ.
- **Heavy stack:** nine Docker services; they estimate ~0.3–1 FTE to operate. A Postgres-only design can be much lighter.

### Budget & Forecast for Claude Code (closest data model)

- **Versions as first-class rows:** actuals, budgets, forecasts, and scenarios are all versions; a locked version cannot change, enforced by the database itself.
- **Roll-forecast operation:** creates a new forecast version from actuals for closed months plus the old forecast for the rest. This is a core planning operation to support natively.
- **Every change carries an actor** and lands in an activity log with the previous value.
- **Version copy with shift and uplift** (copy a version, shift months, apply a percentage) is a simple, high-value seeding feature.
- **Variance commentary** as data (notes against entity/account/month), with thresholds stored in a settings table.
- **Import discipline:** idempotent imports, a dry-run mode, and nothing is saved until every row maps.
- **One DB adapter** for real Postgres or embedded PGlite, which is useful for demos and tests.
- **Limitation:** fixed to entity × account × month; no generic dimensions and no grid UI by design.

### Plannifico (concepts)

- Stated goals worth keeping: what-if scenarios on many dimensions, driver-based planning, combining bottom-up and top-down, and scaling to millions of records.
- A separate calculation server behind a REST API, decoupled from the client.

## 7. The gap to target

A **web-first, Postgres-first, planning-first** tool:

- Excel-like grid in the browser (paste, fill-drag, keyboard navigation, totals, spreading).
- Generic, user-defined dimensions (entity, cost center, account, product, country…) and time periods.
- Versions/scenarios/layers, with actuals loaded alongside for comparison.
- Lightweight workflow: submit → approve → lock, per version and per planning unit (e.g., cost center).
- Cell- and row-level permissions (planners see and edit only their cost centers).
- Plain SQL tables that any BI tool can read directly.
- One `docker compose up` (app + Postgres) to self-host.

**Explicitly out of scope for v1:** statutory consolidation, FX translation, intercompany elimination, allocations, and a general formula engine. These are where Konsolidat and commercial tools are strong and where effort explodes.

## 8. Architecture sketch

**Data model (long/narrow fact table):**

```sql
-- Dimensions are metadata, not hard-coded columns
dimension(id, code, name)
member(id, dimension_id, code, name, parent_id, sort_order)  -- hierarchies via parent_id

version(id, code, kind,            -- actual | budget | forecast | scenario
        status,                    -- draft | submitted | approved | locked
        base_version_id, created_by, created_at)

layer(id, code, sort_order)        -- base | challenge | management | board

fact(version_id, layer_id, period, -- e.g. first day of month
     member_key,                   -- canonical key of dimension members (or one FK per dimension)
     amount numeric(20,4),
     updated_by, updated_at,
     primary key (version_id, layer_id, period, member_key))

change_log(id, fact_key, old_amount, new_amount, actor, reason, at)
comment(id, version_id, period, member_key, text, actor, at)
spread_profile(id, name, weights numeric[])   -- weights sum to 1
```

**Key design choices:**

- The grid is a **pivot over the fact table**: rows = one or more dimensions, columns = periods (or another dimension). The server returns a cell block; edits go back as `(coordinates, value)` pairs.
- **Totals are computed, never stored.** Editing a total triggers a *spread* operation that writes leaf values (proportionally to existing values, equally, or by a spread profile).
- **Locks in the database:** a trigger or constraint rejects writes to locked versions, so rules hold even if someone bypasses the app.
- **Database portability:** stay with standard SQL (`GROUP BY ROLLUP`, window functions, upserts via an abstraction layer). Postgres-only features (triggers, row-level security) can have equivalents or app-level fallbacks for other databases.
- **Row-level security** in Postgres for planner permissions, mirrored in the API layer.
- **Grid component:** evaluate Glide Data Grid (MIT, canvas, fill handle) and RevoGrid (MIT core) first; AG Grid Enterprise or Handsontable only if license costs are acceptable for contributors and users (they would conflict with a fully free open-source product).
- **Backend:** TypeScript (Node) to share types with the frontend, or Python if the spreading and calculation logic is expected to grow. A thin REST/JSON API either way.

## 9. Suggested v1 (MVP)

1. Define dimensions and members (CSV import plus simple editor), including hierarchies.
2. Create versions; copy a version with shift and uplift.
3. Planning grid: choose row dimensions, filter others, periods as columns; edit, paste, fill-drag, subtotals.
4. Top-down spreading from totals (proportional / equal / profile).
5. Import actuals from CSV; plan vs. actual vs. prior-version comparison view.
6. Submit/approve/lock per version; database-enforced locks.
7. Change log and cell comments.
8. Users, roles, and member-level edit permissions.

**Later:** layers (base/challenge/management/board), rolling forecast, drivers (price × volume), headcount planning template, simple formulas, ERP connectors, other databases.

## 10. Risks and open questions

- **Grid performance and UX** are the product. Budget real effort here (large grids, keyboard flow, paste of thousands of cells).
- **Scope discipline:** say no to consolidation and allocations early, or partner/integrate instead.
- **Target users:** mid-sized companies with Excel-based budgeting are the realistic audience. Enterprises will compare against Anaplan/Pigment feature lists.
- **Sustainability:** a hosted version or paid support is the common model (both Konsolidat and Budget & Forecast for Claude Code sell hosting/customization around an MIT core).
- **License choice:** MIT/Apache maximizes adoption; AGPL protects against hosted forks. Decide before the first public release.
- **Open question:** one FK column per dimension (fast, simple SQL, schema changes when dimensions change) vs. a generic member key (flexible, more complex queries). Konsolidat's approach of generating columns from a registry is a middle ground worth prototyping.

## 11. Next steps

1. Clone Konsolidat and Budget & Forecast for Claude Code; read their schemas, budget models, and tests (an afternoon each).
2. Prototype the fact table + pivot query + spreading in Postgres with ~1M rows to check performance.
3. Spike two grid components (Glide, RevoGrid) on the same pivot API, testing paste and fill-drag.
4. Write a one-page scope statement listing what v1 will *not* do.
5. Talk to 3–5 finance/controlling people about their current Excel planning process before building UI beyond the grid.

## Sources

- Konsolidat: https://konsolidat.com · https://github.com/grynn-in/konsolidat · https://sourceforge.net/projects/konsolidat/
- Budget & Forecast for Claude Code: https://github.com/Enterprise-DNA-OS/budget-forecast-for-claude-code
- Plannifico: https://github.com/plannifico/Plannifico
- ERPNext budgeting: https://docs.frappe.io/erpnext/budgeting
- OCA account-budgeting: https://github.com/OCA/account-budgeting
- awesome-selfhosted, money/budgeting: https://awesome-selfhosted.net/tags/money-budgeting--management.html
