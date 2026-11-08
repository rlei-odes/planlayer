# /// script
# requires-python = ">=3.13"
# dependencies = ["sqlalchemy==2.1.8", "psycopg[binary]==3.3.8"]
# ///
"""S-06: per-request user context with a SQLAlchemy pool, RLS, lock trigger and audit trigger.

Runs in the dev database ``planlayer`` exactly as an installation would: the tables belong to the
unprivileged ``planlayer`` role (which owns its schema), so RLS must be FORCEd to apply to the
owner. Every check is an assertion; the script prints timings at the end.
"""

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from decimal import Decimal

from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.exc import DBAPIError

ADMIN_URL = "postgresql+psycopg://postgres:postgres@localhost:5432/planlayer"
APP_URL = "postgresql+psycopg://planlayer:planlayer@localhost:5432/planlayer"

PLANNER_A, PLANNER_B, NOBODY = 1, 2, 3
CC_A = [101, 102, 103]
CC_B = [201, 202]
BUDGET, LOCKED_VERSION = 2, 3

SCHEMA = """
    CREATE TABLE s06.user_scope (user_id int, cost_center_id int, PRIMARY KEY (user_id, cost_center_id));
    CREATE TABLE s06.fact_cc (
        version_id smallint, layer_id smallint, measure_id smallint, period date,
        cost_center_id int, account_id int, amount numeric(18,2) NOT NULL,
        row_version bigint NOT NULL DEFAULT 1,
        PRIMARY KEY (version_id, layer_id, measure_id, period, cost_center_id, account_id));
    CREATE TABLE s06.lock (
        version_id smallint, cost_center_id int, locked_by int, locked_at timestamptz DEFAULT now(),
        PRIMARY KEY (version_id, cost_center_id));
    CREATE TABLE s06.change_log (
        id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, at timestamptz DEFAULT now(),
        actor int, batch_id text, op text, version_id smallint, period date, cost_center_id int,
        account_id int, old_amount numeric(18,2), new_amount numeric(18,2));

    -- RLS: the owner (planlayer) is subject to it only with FORCE.
    ALTER TABLE s06.fact_cc ENABLE ROW LEVEL SECURITY;
    ALTER TABLE s06.fact_cc FORCE ROW LEVEL SECURITY;
    CREATE POLICY scope ON s06.fact_cc
        USING (cost_center_id = ANY (ARRAY(
            SELECT cost_center_id FROM s06.user_scope
            WHERE user_id = nullif(current_setting('app.user_id', true), '')::int)))
        WITH CHECK (cost_center_id = ANY (ARRAY(
            SELECT cost_center_id FROM s06.user_scope
            WHERE user_id = nullif(current_setting('app.user_id', true), '')::int)));
"""

ROW_FUNCTIONS = """
    CREATE FUNCTION s06.guard_row() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE l s06.lock;
    BEGIN
        IF TG_OP IN ('UPDATE', 'DELETE') THEN
            SELECT * INTO l FROM s06.lock
            WHERE version_id = OLD.version_id AND cost_center_id = OLD.cost_center_id;
            IF FOUND THEN
                RAISE EXCEPTION 'version % / cost center % is locked by user %',
                    OLD.version_id, OLD.cost_center_id, l.locked_by USING ERRCODE = 'P0001';
            END IF;
        END IF;
        IF TG_OP IN ('INSERT', 'UPDATE') THEN
            SELECT * INTO l FROM s06.lock
            WHERE version_id = NEW.version_id AND cost_center_id = NEW.cost_center_id;
            IF FOUND THEN
                RAISE EXCEPTION 'version % / cost center % is locked by user %',
                    NEW.version_id, NEW.cost_center_id, l.locked_by USING ERRCODE = 'P0001';
            END IF;
            RETURN NEW;
        END IF;
        RETURN OLD;
    END $$;

    CREATE FUNCTION s06.audit_row() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        INSERT INTO s06.change_log (actor, batch_id, op, version_id, period, cost_center_id,
                                    account_id, old_amount, new_amount)
        SELECT nullif(current_setting('app.user_id', true), '')::int,
               nullif(current_setting('app.batch_id', true), ''), TG_OP,
               coalesce(NEW.version_id, OLD.version_id), coalesce(NEW.period, OLD.period),
               coalesce(NEW.cost_center_id, OLD.cost_center_id),
               coalesce(NEW.account_id, OLD.account_id),
               CASE WHEN TG_OP <> 'INSERT' THEN OLD.amount END,
               CASE WHEN TG_OP <> 'DELETE' THEN NEW.amount END;
        RETURN NULL;
    END $$;
"""

ROW_TRIGGER_DDL = """
    CREATE TRIGGER guard BEFORE INSERT OR UPDATE OR DELETE ON s06.fact_cc
        FOR EACH ROW EXECUTE FUNCTION s06.guard_row();
    CREATE TRIGGER audit AFTER INSERT OR UPDATE OR DELETE ON s06.fact_cc
        FOR EACH ROW EXECUTE FUNCTION s06.audit_row();
"""

# Statement-level variant: one call per statement, rows from transition tables.
STATEMENT_FUNCTIONS = """
    CREATE FUNCTION s06.guard_stmt() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE hit record;
    BEGIN
        SELECT r.version_id, r.cost_center_id, l.locked_by INTO hit
        FROM changed r JOIN s06.lock l USING (version_id, cost_center_id) LIMIT 1;
        IF FOUND THEN
            RAISE EXCEPTION 'version % / cost center % is locked by user %',
                hit.version_id, hit.cost_center_id, hit.locked_by USING ERRCODE = 'P0001';
        END IF;
        RETURN NULL;
    END $$;
    CREATE FUNCTION s06.audit_stmt() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP = 'INSERT' THEN
            INSERT INTO s06.change_log (actor, batch_id, op, version_id, period, cost_center_id,
                                        account_id, old_amount, new_amount)
            SELECT nullif(current_setting('app.user_id', true), '')::int,
                   nullif(current_setting('app.batch_id', true), ''), 'INSERT',
                   version_id, period, cost_center_id, account_id, NULL, amount FROM changed;
        ELSIF TG_OP = 'UPDATE' THEN
            INSERT INTO s06.change_log (actor, batch_id, op, version_id, period, cost_center_id,
                                        account_id, old_amount, new_amount)
            SELECT nullif(current_setting('app.user_id', true), '')::int,
                   nullif(current_setting('app.batch_id', true), ''), 'UPDATE',
                   n.version_id, n.period, n.cost_center_id, n.account_id, o.amount, n.amount
            FROM changed n JOIN before_rows o
              USING (version_id, layer_id, measure_id, period, cost_center_id, account_id);
        ELSE
            INSERT INTO s06.change_log (actor, batch_id, op, version_id, period, cost_center_id,
                                        account_id, old_amount, new_amount)
            SELECT nullif(current_setting('app.user_id', true), '')::int,
                   nullif(current_setting('app.batch_id', true), ''), 'DELETE',
                   version_id, period, cost_center_id, account_id, amount, NULL FROM changed;
        END IF;
        RETURN NULL;
    END $$;
"""

STATEMENT_TRIGGER_DDL = """
    -- A lock check after the statement still rolls it back, because the exception aborts it.
    CREATE TRIGGER guard_ins AFTER INSERT ON s06.fact_cc REFERENCING NEW TABLE AS changed
        FOR EACH STATEMENT EXECUTE FUNCTION s06.guard_stmt();
    CREATE TRIGGER guard_upd AFTER UPDATE ON s06.fact_cc REFERENCING NEW TABLE AS changed
        FOR EACH STATEMENT EXECUTE FUNCTION s06.guard_stmt();
    CREATE TRIGGER guard_upd_old AFTER UPDATE ON s06.fact_cc REFERENCING OLD TABLE AS changed
        FOR EACH STATEMENT EXECUTE FUNCTION s06.guard_stmt();
    CREATE TRIGGER guard_del AFTER DELETE ON s06.fact_cc REFERENCING OLD TABLE AS changed
        FOR EACH STATEMENT EXECUTE FUNCTION s06.guard_stmt();
    CREATE TRIGGER audit_ins AFTER INSERT ON s06.fact_cc REFERENCING NEW TABLE AS changed
        FOR EACH STATEMENT EXECUTE FUNCTION s06.audit_stmt();
    CREATE TRIGGER audit_upd AFTER UPDATE ON s06.fact_cc
        REFERENCING NEW TABLE AS changed OLD TABLE AS before_rows
        FOR EACH STATEMENT EXECUTE FUNCTION s06.audit_stmt();
    CREATE TRIGGER audit_del AFTER DELETE ON s06.fact_cc REFERENCING OLD TABLE AS changed
        FOR EACH STATEMENT EXECUTE FUNCTION s06.audit_stmt();
"""

DROP_TRIGGERS = """
    DROP TRIGGER IF EXISTS guard ON s06.fact_cc; DROP TRIGGER IF EXISTS audit ON s06.fact_cc;
    DROP TRIGGER IF EXISTS guard_ins ON s06.fact_cc; DROP TRIGGER IF EXISTS guard_upd ON s06.fact_cc;
    DROP TRIGGER IF EXISTS guard_upd_old ON s06.fact_cc; DROP TRIGGER IF EXISTS guard_del ON s06.fact_cc;
    DROP TRIGGER IF EXISTS audit_ins ON s06.fact_cc; DROP TRIGGER IF EXISTS audit_upd ON s06.fact_cc;
    DROP TRIGGER IF EXISTS audit_del ON s06.fact_cc;
"""

UPSERT = text("""
    INSERT INTO s06.fact_cc (version_id, layer_id, measure_id, period, cost_center_id, account_id, amount)
    SELECT :v, 1, 1, p, c, a, x
    FROM unnest(CAST(:p AS date[]), CAST(:c AS int[]), CAST(:a AS int[]), CAST(:x AS numeric[])) AS t(p, c, a, x)
    ON CONFLICT (version_id, layer_id, measure_id, period, cost_center_id, account_id)
    DO UPDATE SET amount = EXCLUDED.amount, row_version = s06.fact_cc.row_version + 1""")


@contextmanager
def unit_of_work(engine: Engine, user_id: int | None, batch_id: str | None = None) -> Iterator[Connection]:
    """One transaction with the request's context; ``set_config(..., true)`` = SET LOCAL."""
    with engine.begin() as conn:
        if user_id is not None:
            conn.execute(text("SELECT set_config('app.user_id', :u, true)"), {"u": str(user_id)})
        if batch_id is not None:
            conn.execute(text("SELECT set_config('app.batch_id', :b, true)"), {"b": batch_id})
        yield conn


def visible(engine: Engine, user_id: int | None) -> set[int]:
    with unit_of_work(engine, user_id) as conn:
        return {r[0] for r in conn.execute(text("SELECT DISTINCT cost_center_id FROM s06.fact_cc"))}


def cells(ccs: list[int], accounts: int, value: str = "100.00") -> dict[str, list[object]]:
    p, c, a, x = [], [], [], []
    for cc in ccs:
        for acc in range(1, accounts + 1):
            for month in range(1, 13):
                p.append(date(2027, month, 1)); c.append(cc); a.append(acc); x.append(Decimal(value))  # noqa: E702
    return {"p": p, "c": c, "a": a, "x": x}


def expect_error(fn, match: str) -> str:  # type: ignore[no-untyped-def]
    try:
        fn()
    except DBAPIError as e:
        message = str(e.orig)
        assert match in message, message
        return message
    raise AssertionError(f"expected an error containing {match!r}")


def main() -> None:
    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS s06 CASCADE; CREATE SCHEMA s06 AUTHORIZATION planlayer"))
    admin.dispose()

    engine = create_engine(APP_URL, pool_size=4, max_overflow=0)
    with engine.begin() as conn:
        conn.execute(text(SCHEMA))
        conn.execute(text(ROW_FUNCTIONS))
        conn.execute(text(ROW_TRIGGER_DDL))
        conn.execute(text(STATEMENT_FUNCTIONS))
        conn.execute(text("INSERT INTO s06.user_scope VALUES (1, 101), (1, 102), (1, 103), (2, 201), (2, 202)"))
    results: list[tuple[str, str]] = []

    # Seed: each planner writes their own cost centers, in a batch.
    with unit_of_work(engine, PLANNER_A, "seed-a") as conn:
        conn.execute(UPSERT, {"v": BUDGET, **cells(CC_A, 10)})
    with unit_of_work(engine, PLANNER_B, "seed-b") as conn:
        conn.execute(UPSERT, {"v": BUDGET, **cells(CC_B, 10)})

    # 1. Each planner sees only their scope; no context sees nothing (fail closed, no error).
    assert visible(engine, PLANNER_A) == set(CC_A)
    assert visible(engine, PLANNER_B) == set(CC_B)
    assert visible(engine, None) == set()
    assert visible(engine, NOBODY) == set()
    results.append(("scope per user, no context sees nothing", "ok"))

    # 2. SET LOCAL does not leak to the next transaction on the same pooled connection.
    one = create_engine(APP_URL, pool_size=1, max_overflow=0)
    visible(one, PLANNER_A)
    assert visible(one, None) == set()
    # A session-level SET does leak: never use it.
    with one.connect() as conn:
        conn.execute(text("SET app.user_id = '1'"))
        conn.commit()
    assert visible(one, None) == set(CC_A), "session SET should have leaked"
    one.dispose()
    results.append(("SET LOCAL per transaction does not leak through the pool", "ok"))

    # 3. Writing outside the scope is refused by the policy's WITH CHECK.
    expect_error(lambda: _write(engine, PLANNER_A, CC_B), "row-level security")
    results.append(("write outside scope refused", "ok"))

    # 4. Locks cover insert, update and delete, and name who locked.
    with admin_conn() as conn:
        conn.execute(text("INSERT INTO s06.lock (version_id, cost_center_id, locked_by) VALUES (2, 101, 9)"))
    msg = expect_error(lambda: _write(engine, PLANNER_A, [101]), "locked by user 9")
    expect_error(lambda: _delete(engine, PLANNER_A, 101), "locked by user 9")
    with unit_of_work(engine, PLANNER_A) as conn:  # an unlocked cost center still works
        conn.execute(UPSERT, {"v": BUDGET, **cells([102], 1, "5.00")})
    results.append(("lock on insert/update/delete", msg.splitlines()[0]))

    # 5. Audit rows carry actor and batch from the context, with old and new values.
    with unit_of_work(engine, PLANNER_A, "batch-42") as conn:
        conn.execute(UPSERT, {"v": BUDGET, **cells([103], 2, "7.50")})
    with admin_conn() as conn:
        rows = conn.execute(text(
            "SELECT actor, op, old_amount, new_amount FROM s06.change_log WHERE batch_id = 'batch-42'")).all()
    assert len(rows) == 24 and {(r[0], r[1], r[2], r[3]) for r in rows} == {(1, "UPDATE", Decimal("100.00"), Decimal("7.50"))}
    results.append(("audit actor/batch/old/new", f"{len(rows)} rows ok"))

    # 6. Concurrency: threads with different users share the pool.
    errors: list[str] = []

    def worker(n: int) -> None:
        for i in range(40):
            user = PLANNER_A if (n + i) % 2 else PLANNER_B
            want = set(CC_A) if user == PLANNER_A else set(CC_B)
            got = visible(engine, user)
            if got != want:
                errors.append(f"thread {n}: user {user} saw {got}")

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors[:3]
    results.append(("16 threads x 40 requests over a 4-connection pool", "no cross-talk"))

    # 7. Cost of the triggers on a 5,040-cell upsert (update path), as planner with full scope.
    with admin_conn() as conn:
        conn.execute(text("DELETE FROM s06.lock"))
        conn.execute(text("INSERT INTO s06.user_scope SELECT 1, g FROM generate_series(1000, 1059) g ON CONFLICT DO NOTHING"))
    big = cells(list(range(1000, 1060)), 7)
    for label, ddl in (
        ("no triggers", DROP_TRIGGERS),
        ("row triggers", DROP_TRIGGERS + ROW_TRIGGER_DDL),
        ("statement triggers", DROP_TRIGGERS + STATEMENT_TRIGGER_DDL),
    ):
        with engine.begin() as conn:
            conn.execute(text(ddl))
        with unit_of_work(engine, PLANNER_A, "warm") as conn:
            conn.execute(UPSERT, {"v": BUDGET, **big})
        times = []
        for _ in range(5):
            start = time.perf_counter()
            with unit_of_work(engine, PLANNER_A, "bench") as conn:
                conn.execute(UPSERT, {"v": BUDGET, **big})
            times.append((time.perf_counter() - start) * 1000)
        results.append((f"upsert 5,040 cells, {label}", f"{sorted(times)[2]:7.1f} ms"))

    # The statement-level lock still refuses writes.
    with admin_conn() as conn:
        conn.execute(text("INSERT INTO s06.lock (version_id, cost_center_id, locked_by) VALUES (2, 1000, 9)"))
    expect_error(lambda: _write(engine, PLANNER_A, [1000]), "locked by user 9")
    expect_error(lambda: _delete(engine, PLANNER_A, 1000), "locked by user 9")
    with admin_conn() as conn:
        n = conn.execute(text("SELECT count(*) FROM s06.change_log WHERE batch_id = 'bench'")).scalar_one()
    results.append(("statement-level lock and audit", f"lock ok, {n:,} audit rows from benchmark"))
    engine.dispose()

    width = max(len(k) for k, _ in results)
    for k, v in results:
        print(f"{k:<{width}}  {v}")


def _write(engine: Engine, user: int, ccs: list[int]) -> None:
    with unit_of_work(engine, user) as conn:
        conn.execute(UPSERT, {"v": BUDGET, **cells(ccs, 1)})


def _delete(engine: Engine, user: int, cc: int) -> None:
    with unit_of_work(engine, user) as conn:
        n = conn.execute(text("DELETE FROM s06.fact_cc WHERE cost_center_id = :c"), {"c": cc}).rowcount
        assert n > 0, "nothing to delete: the test would not exercise the lock"


@contextmanager
def admin_conn() -> Iterator[Connection]:
    engine = create_engine(ADMIN_URL)
    try:
        with engine.begin() as conn:
            yield conn
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
