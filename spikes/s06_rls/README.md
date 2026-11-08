# Spike S-06: request context, RLS, lock and audit triggers

```
cd spikes/s06_rls && uv run s06_context.py
```

The spike runs in the dev database `planlayer`, as an installation would. Its schema belongs to the unprivileged `planlayer` role, and every check is an assertion.

| Check | Result |
|---|---|
| Each planner sees only their scope; a request without user context sees nothing, and gets no error | ✓ |
| Context set per transaction (`set_config(..., true)` = `SET LOCAL`) doesn't leak to the next request on a pooled connection | ✓ |
| A session-level `SET` does leak through the pool | demonstrated, so never use it |
| Writing outside one's scope is refused (policy `WITH CHECK`) | ✓ |
| A lock refuses insert, update and delete, and names who locked | ✓ |
| Audit rows carry actor and batch from the context, with old and new value | ✓ |
| 16 threads × 40 requests over a 4-connection pool, alternating users | no cross-talk |
| Upsert of 5,040 cells: no triggers / row-level triggers / statement-level triggers | 68 / 194 / 97 ms |

Findings:
- **FORCE is required.** The app role owns its tables, and an owner bypasses RLS unless `FORCE ROW LEVEL SECURITY` is set.
- **Unset context:** `current_setting('app.user_id', true)` returns `''` once a `SET LOCAL` has ended, so policies use `nullif(..., '')::int`. An unset context then matches nothing, without erroring.
- **Statement-level triggers** with transition tables cover lock and audit for a whole bulk write in one call each, at about a quarter of the cost of row-level triggers. A lock check in an AFTER statement trigger still rolls the statement back.
