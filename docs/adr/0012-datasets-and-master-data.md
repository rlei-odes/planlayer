# 0012: Datasets, source queries and master data in Planlayer's tables

- **Status:** Accepted
- **Closes:** D-11, D-31; the preview and confirmation part of D-27
- **Date:** 2026-11-08

## Context

Sources are mapped, not prescribed (T-16): the admin binds each logical dataset to the customer's data. Master data is imported, then curated in Planlayer (T-17). Some installations plan without any source (the Simple and Medium levels of E-03), and move to a source later. Adapters read queries as Arrow batches cast to a contract schema (ADR 0004).

## Decision

### Datasets and their fields

| Dataset | Fields (* required) |
|---|---|
| `entities` | code*, name* |
| `cost_centers` | code*, name*, entity_code, owner, valid_from, valid_to, is_group, sort_order; plus the hierarchy |
| `accounts` | code*, name*, account_type, is_group, sort_order; plus the hierarchy |
| `actuals` | period*, entity_code*, cost_center_code*, account_code*, amount* |

Every dataset is optional. Without `actuals`, rounds have no actuals to compare with or spread by. A cost center without an entity belongs to the built-in entity `-` ("No entity").

### Mapping: a query and named columns

- The admin enters one SQL query per dataset, run on the source connection, and names which result column is which field. Field types come from the dataset's contract (ADR 0004), so a value that doesn't fit is an error, not a silent cast.
- **Hierarchy,** in either form:
  - **parent code:** a column naming each row's parent; group nodes are rows of their own. A row is a group when `is_group` says so, or else when another row names it as parent;
  - **level columns:** pairs of code and name columns from the top level down (e.g. division, department), as in flattened ERP exports. Group nodes are built from the distinct level values; an empty level is skipped, so ragged trees work.
- **Preview** runs the query read-only with a row limit and shows the columns and first rows, before and after the field mapping.
- Queries run in a read-only transaction with a statement timeout. The source role has `SELECT` rights only (T-14).

### Master data lives in Planlayer's tables

- Members of every dimension live in `member` and the hierarchy edges in `hier_edge`. Each member and edge records its **origin**: `source` or `manual`.
- **Manual members:** the controller adds cost centers, accounts, groups and entities on the master-data page, and edits or deletes them (deleting only while nothing refers to them). The same page builds all master data from scratch when there is no source.
- **Local overrides on synced members:** the controller may rename a synced member or move it to another parent. The override survives syncs and is marked; the page shows the source's value next to it, and the override can be removed.
- A member the source stops delivering is kept with its values (D-13 decides how it's flagged).

### Sync: preview, confirm, run

1. **Preview** reads every mapped dataset, resolves every code and builds the hierarchies. It reports problems (unknown codes, cycles, duplicate codes), and members new, changed, not delivered any more, and **matches**: source rows whose code exists as a manual member. Nothing is written.
2. **Matches are confirmed:** a run refuses while the source delivers a code that is still a manual member and hasn't been confirmed ("found 1 match from the source").
3. **Run:** applies everything in one transaction, as the preview showed. A confirmed match keeps the member's identity and plan values. From then on the source owns it, and its manual data is kept as a **shadow record** (name, attributes, parents, who confirmed and when).
4. Every run is recorded with actor, counts and the mappings used.

Admins edit mappings. Admins and controllers preview, confirm and run syncs, and controllers edit master data.

## Reasons

- **One store, two ways in:** plans, scopes and workflow refer to members, never to a source. A source, manual entry, or both feed the same tables, so moving from Simple to Full is a sync, not a migration.
- **Queries, not prescribed views:** customers' warehouses differ. A query plus named columns adapts to them without asking the DBA for new views, and still meets a typed contract.
- **Both hierarchy forms:** parent-child tables and flattened level columns are the two shapes ERP and warehouse exports come in.
- **Confirmation for matches:** a manual member turning into a source member changes who owns its data. The controller sees it before it happens, and the shadow record keeps what was entered by hand.
- **Overrides survive syncs:** the source is sometimes slow to reflect a reorganisation or a better name; the controller fixes it in Planlayer without losing the fix on the next sync.

## Consequences

- New: `dataset_mapping`, `member_override`, `edge_override`, `member_shadow`; `origin` on `member` and `hier_edge`. `member.name` and `hier_edge.parent_id` hold the effective values, with the source's values next to them, so every reader sees overrides without change.
- The master-data page (E-01, E-02): mappings with preview (admin), sync preview, confirmation and run, and the member tree with manual members and overrides.
- Sign rules for actuals (E-12) and alternate hierarchies as further datasets come later.
- D-27 keeps open: no silent overwrite of changed actuals in closed periods, and a source hash per run.
