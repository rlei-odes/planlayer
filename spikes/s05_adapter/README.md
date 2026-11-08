# Spike S-05: adapter interface, Postgres and DuckDB-over-Parquet

```
cd tools/gen_testdata && uv run gen-testdata --db postgresql://postgres:postgres@localhost:5432/dwh --out out/standard
cd spikes/s05_adapter && uv run s05_check.py
```

The interface is in [adapters.py](adapters.py). A source is a relation or an admin-entered query. Reads stream Arrow record batches, cast to a contract schema. A write replaces one partition in a single transaction.

| Check | Result |
|---|---|
| Postgres and Parquet read cost centers, hierarchy, accounts, GL actuals and sales identically, with the same schema and rows | ✓ (163k / 150k rows) |
| The same admin query (sales at plan grain) in both SQL dialects | ✓ same 37,118 rows |
| A value with more decimals than the contract | refused |
| Replace of a version twice, Postgres and Parquet | idempotent; other partitions kept; 0.00 stays 0.00 |
| Read throughput, Postgres, row level (large GL actuals) | 5.6M rows in 23 s (240k rows/s) |
| Read at plan grain, large sales (aggregation in the source view) | 63k rows in 13.5 s, almost all of it spent inside Postgres |

Findings:
- **Types can't be inferred reliably from the source.** A view's `round(sum(...))` loses the numeric precision, so Postgres reports plain `numeric`, and DuckDB widens sums to `decimal(38,3)`. The field mapping (T-16) therefore declares each field's type, and the adapter casts with a safe cast, which fails loudly on lost digits.
- **The same interface works unchanged for a non-SQLAlchemy backend** (DuckDB over files), including writes: one file per partition, written to a temp name and renamed.
- **Sync time is dominated by the source query** at plan grain. Row transfer at 240k rows/s is not the bottleneck. If it ever becomes one, a native Arrow driver can replace the row fetch inside the Postgres adapter.
