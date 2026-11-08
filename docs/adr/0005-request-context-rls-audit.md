# 0005: Request context, row-level security, locks and audit in the database

- **Status:** Accepted
- **Closes:** D-23; the enforcement part of D-21 and D-22
- **Date:** 2026-11-08

## Context

Locks, scopes and the change log must hold no matter which code path writes (T-11). The app connects through a connection pool as one database role, which owns its tables (T-14). Bulk writes of thousands of cells are normal (spreading, paste).

## Decision

1. **Request context:** every request runs in one transaction, which first sets `app.user_id` and `app.batch_id` with `set_config(name, value, true)`. That is `SET LOCAL`, which ends with the transaction. Session-level `SET` is never used.
2. **Row-level security:** fact tables have RLS enabled and **forced**, so the owning app role is subject to it.
   - Policies read the context through `nullif(current_setting('app.user_id', true), '')::int`, so a missing context matches no rows.
   - The scope is written as `= ANY (ARRAY(subquery))` (0001).
   - `USING` filters reads, and `WITH CHECK` refuses writes outside the scope.
3. **Locks:** statement-level triggers with transition tables, on insert, update and delete, refuse any write touching a locked (version, planning unit). The error names who locked it.
4. **Audit:** statement-level triggers with transition tables write one change-log row per changed cell: actor and batch from the context, operation, key, old value and new value.

## Reasons

Measured in spike S-06 ([spikes/s06_rls](../../spikes/s06_rls/README.md)):
- **Context isolation:** per-transaction context showed no cross-talk with 16 threads sharing 4 pooled connections. A session-level `SET` demonstrably leaks to the next request on the same connection.
- **FORCE is required:** without it, the owning app role bypasses every policy.
- **Statement-level triggers** cost 29 ms on a 5,040-cell upsert, against 126 ms for row-level triggers. Lock refusal and audit content were identical.
- **Database enforcement** means the API's own checks are a convenience for good error messages, not the protection.

## Consequences

- The store layer offers one way to run a request: a unit of work that opens the transaction and sets the context. Code that bypasses it sees no rows.
- Background jobs (sync, publish, spreading jobs) run with a context too: the user who started them, or a system actor id.
- D-21 still defines the role model (roles × member scopes, owner-based defaults per T-17) and how a synced owner is matched to a login. D-22 defines the state machine; its lock is the trigger above.
- The change log grows with every cell written. Retention and partitioning are set in D-23's follow-up when real volumes are known.
