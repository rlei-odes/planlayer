# 0004: Adapter interface with Arrow batches and a contract schema

- **Status:** Accepted
- **Closes:** D-10
- **Date:** 2026-11-08

## Context

Planlayer reads master data and actuals from the customer's systems and publishes plan data back (T-3, T-14). Sources are mapped by the admin to a view, table or query (T-16). Postgres comes first; other databases, warehouses and file formats follow as further adapters (T-4). Amounts must stay exact (T-12).

## Decision

1. **A source** is a relation (schema and name) or a query entered by the admin.
2. **A source adapter** offers:
   - `test_connection()`;
   - `schema(source)`: the source's column types;
   - `read(source, columns, cast)`: a stream of Arrow record batches. With `cast`, every batch is converted to the contract schema by a safe cast, which fails on lost digits or overflow.
3. **A target adapter** offers `replace(target, data, partition)`, which replaces the rows of one partition (e.g. a version) atomically. It is idempotent: repeating a publish gives the same result.
4. **The contract schema comes from the field mapping.** Each Planlayer field has a declared type (e.g. `amount` is `decimal(18,2)`). Types are never inferred from the source alone.
5. **The Postgres adapter** uses SQLAlchemy Core for reads (server-side cursor, batches of 50k rows), and `DELETE` plus `COPY` in one transaction for `replace`.
6. **Arrow is the interchange format** between adapters and the sync and publish code.

## Reasons

Measured in spike S-05 ([spikes/s05_adapter](../../spikes/s05_adapter/README.md)):
- **One interface fits a second backend unchanged:** a DuckDB-over-Parquet adapter returns the same schema and rows as Postgres for every dataset, and has an atomic, idempotent `replace`.
- **The contract schema is necessary:** Postgres reports aggregated view columns as plain `numeric`, and DuckDB widens sums. Only a declared type gives the same `decimal(18,2)` from both, and the safe cast turns bad source data into an error instead of a rounding.
- **Throughput:** 240k rows/s at row level. Syncs at plan grain are bound by the source query, not by the transfer.

## Consequences

- D-11 (logical datasets and mapping) defines each field's Arrow type. The mapping UI (E-01) previews a mapped dataset by reading a first batch with the cast applied, so type errors show before the first sync.
- Sync (D-27) and publish (D-12) consume and produce Arrow tables, independent of the backend.
- New source types are new adapter classes. Sync, validation and publish code don't change.
