# /// script
# requires-python = ">=3.13"
# dependencies = ["psycopg[binary]==3.3.8"]
# ///
"""S-01: grid queries, bulk upsert and RLS on fact_wide (D-01 b/c) vs fact_eav (D-01 a).

Also D-14: subtotals by SQL ROLLUP vs leaves fetched and summed in Python.
Run load.py first.
"""

from collections import defaultdict
from datetime import date
from decimal import Decimal

from common import ACCOUNT, COST_CENTER, COUNTRY, H_ACCOUNT, H_CC, H_PRODUCT, PRODUCT, connect, report, timed

YEAR = ("2025-01-01", "2026-01-01")

# --- Q1: 60 cost centers x 12 months, subtotals per department and total -------------------------

Q1_WIDE = """
    WITH cc AS (SELECT member_id, parent_id FROM spike.hier_edge
                WHERE hier_id = 1 AND member_id = ANY(%(ccs)s))
    SELECT cc.parent_id, f.cost_center_id, f.period, sum(f.amount)
    FROM spike.fact_wide f JOIN cc ON cc.member_id = f.cost_center_id
    WHERE f.version_id = 1 AND f.layer_id = 1 AND f.measure_id = 1
      AND f.period >= %(start)s AND f.period < %(end)s
    GROUP BY f.period, ROLLUP (cc.parent_id, f.cost_center_id)"""

Q1_EAV = """
    WITH cc AS (SELECT member_id, parent_id FROM spike.hier_edge
                WHERE hier_id = 1 AND member_id = ANY(%(ccs)s))
    SELECT cc.parent_id, m.member_id, f.period, sum(f.amount)
    FROM spike.fact_eav_member m
    JOIN cc ON cc.member_id = m.member_id
    JOIN spike.fact_eav f ON f.fact_id = m.fact_id
    WHERE m.dim_id = 2 AND f.version_id = 1 AND f.layer_id = 1 AND f.measure_id = 1
      AND f.period >= %(start)s AND f.period < %(end)s
    GROUP BY f.period, ROLLUP (cc.parent_id, m.member_id)"""

Q1_WIDE_LEAVES = """
    SELECT f.cost_center_id, f.period, sum(f.amount)
    FROM spike.fact_wide f
    WHERE f.version_id = 1 AND f.layer_id = 1 AND f.measure_id = 1
      AND f.cost_center_id = ANY(%(ccs)s) AND f.period >= %(start)s AND f.period < %(end)s
    GROUP BY 1, 2"""

# --- Q1b: one cost center x accounts x months, subtotals per account group ----------------------

Q1B_WIDE = """
    SELECT e.parent_id, f.account_id, f.period, sum(f.amount)
    FROM spike.fact_wide f JOIN spike.hier_edge e ON e.hier_id = 3 AND e.member_id = f.account_id
    WHERE f.version_id = 1 AND f.measure_id = 1 AND f.cost_center_id = %(cc)s
      AND f.period >= %(start)s AND f.period < %(end)s
    GROUP BY f.period, ROLLUP (e.parent_id, f.account_id)"""

Q1B_EAV = """
    SELECT e.parent_id, ma.member_id, f.period, sum(f.amount)
    FROM spike.fact_eav_member mc
    JOIN spike.fact_eav f ON f.fact_id = mc.fact_id
    JOIN spike.fact_eav_member ma ON ma.fact_id = f.fact_id AND ma.dim_id = 3
    JOIN spike.hier_edge e ON e.hier_id = 3 AND e.member_id = ma.member_id
    WHERE mc.dim_id = 2 AND mc.member_id = %(cc)s AND f.version_id = 1 AND f.measure_id = 1
      AND f.period >= %(start)s AND f.period < %(end)s
    GROUP BY f.period, ROLLUP (e.parent_id, ma.member_id)"""

# --- Q2: 12 countries x 40 product groups x 12 months (standard has 40 groups; large too) -------

Q2_WIDE = """
    SELECT f.country_id, e.parent_id, f.period, sum(f.amount)
    FROM spike.fact_wide f JOIN spike.hier_edge e ON e.hier_id = 5 AND e.member_id = f.product_id
    WHERE f.version_id = 1 AND f.measure_id = 1 AND f.product_id IS NOT NULL
      AND f.period >= %(start)s AND f.period < %(end)s
    GROUP BY 1, 2, 3"""

Q2_EAV = """
    SELECT mc.member_id, e.parent_id, f.period, sum(f.amount)
    FROM spike.fact_eav f
    JOIN spike.fact_eav_member mp ON mp.fact_id = f.fact_id AND mp.dim_id = 5
    JOIN spike.fact_eav_member mc ON mc.fact_id = f.fact_id AND mc.dim_id = 4
    JOIN spike.hier_edge e ON e.hier_id = 5 AND e.member_id = mp.member_id
    WHERE f.version_id = 1 AND f.measure_id = 1
      AND f.period >= %(start)s AND f.period < %(end)s
    GROUP BY 1, 2, 3"""

# --- Q3: upsert 5,040 budget cells (60 cost centers x 7 accounts x 12 months) -------------------

UPSERT_WIDE = """
    INSERT INTO spike.fact_wide
        (version_id, layer_id, measure_id, period, entity_id, cost_center_id, account_id, amount)
    SELECT 2, 1, 1, p, e, c, a, v
    FROM unnest(%(p)s::date[], %(e)s::int[], %(c)s::int[], %(a)s::int[], %(v)s::numeric[])
         AS t(p, e, c, a, v)
    ON CONFLICT (version_id, layer_id, measure_id, period, entity_id, cost_center_id, account_id,
                 country_id, product_id)
    DO UPDATE SET amount = EXCLUDED.amount, row_version = spike.fact_wide.row_version + 1"""

UPSERT_EAV = """
    WITH t AS (
        SELECT * FROM unnest(%(p)s::date[], %(e)s::int[], %(c)s::int[], %(a)s::int[],
                             %(v)s::numeric[]) AS t(p, e, c, a, v)),
    hit AS (
        SELECT f.fact_id, t.p, t.e, t.c, t.a, t.v
        FROM t
        JOIN spike.fact_eav_member mc ON mc.dim_id = 2 AND mc.member_id = t.c
        JOIN spike.fact_eav f ON f.fact_id = mc.fact_id AND f.version_id = 2 AND f.layer_id = 1
                             AND f.measure_id = 1 AND f.period = t.p
        JOIN spike.fact_eav_member ma ON ma.fact_id = f.fact_id AND ma.dim_id = 3
                                     AND ma.member_id = t.a
        JOIN spike.fact_eav_member me ON me.fact_id = f.fact_id AND me.dim_id = 1
                                     AND me.member_id = t.e),
    upd AS (
        UPDATE spike.fact_eav f SET amount = hit.v, row_version = f.row_version + 1
        FROM hit WHERE f.fact_id = hit.fact_id RETURNING f.fact_id),
    new AS (
        SELECT nextval('spike.fact_seq') AS fact_id, t.*
        FROM t WHERE NOT EXISTS (
            SELECT 1 FROM hit WHERE (hit.p, hit.e, hit.c, hit.a) = (t.p, t.e, t.c, t.a))),
    ins AS (
        INSERT INTO spike.fact_eav (fact_id, version_id, layer_id, measure_id, period, amount)
        SELECT fact_id, 2, 1, 1, p, v FROM new)
    INSERT INTO spike.fact_eav_member
    SELECT new.fact_id, d.dim_id, d.member_id
    FROM new CROSS JOIN LATERAL (VALUES (1, new.e), (2, new.c), (3, new.a)) AS d(dim_id, member_id)
"""

# --- Q4: Q1 and Q2 under RLS, scope = cost centers below one granted node (via closure) ---------

RLS_SETUP = """
    DO $$ BEGIN
        IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'spike_reader') THEN
            DROP OWNED BY spike_reader; DROP ROLE spike_reader;
        END IF;
    END $$;
    CREATE ROLE spike_reader;
    GRANT USAGE ON SCHEMA spike TO spike_reader;
    CREATE TABLE spike.user_grant (user_id int, node_id int);
    INSERT INTO spike.user_grant VALUES (7, %(node)s);
    ALTER TABLE spike.fact_wide ENABLE ROW LEVEL SECURITY;
    CREATE POLICY scope ON spike.fact_wide FOR SELECT TO spike_reader USING (
        cost_center_id IS NULL OR cost_center_id IN (
            SELECT c.descendant_id FROM spike.user_grant g
            JOIN spike.closure c ON c.hier_id = 1 AND c.ancestor_id = g.node_id
            WHERE g.user_id = current_setting('app.user_id')::int));
    ALTER TABLE spike.fact_eav ENABLE ROW LEVEL SECURITY;
    CREATE POLICY scope ON spike.fact_eav FOR SELECT TO spike_reader USING (
        NOT EXISTS (SELECT 1 FROM spike.fact_eav_member m
                    WHERE m.fact_id = fact_eav.fact_id AND m.dim_id = 2)
        OR EXISTS (
            SELECT 1 FROM spike.fact_eav_member m
            JOIN spike.closure c ON c.hier_id = 1 AND c.descendant_id = m.member_id
            JOIN spike.user_grant g ON g.node_id = c.ancestor_id
            WHERE m.fact_id = fact_eav.fact_id AND m.dim_id = 2
              AND g.user_id = current_setting('app.user_id')::int));
    GRANT SELECT ON ALL TABLES IN SCHEMA spike TO spike_reader;
"""

RLS_TEARDOWN = """
    RESET ROLE;
    DROP POLICY IF EXISTS scope ON spike.fact_wide;
    DROP POLICY IF EXISTS scope ON spike.fact_eav;
    ALTER TABLE spike.fact_wide DISABLE ROW LEVEL SECURITY;
    ALTER TABLE spike.fact_eav DISABLE ROW LEVEL SECURITY;
    DROP TABLE IF EXISTS spike.user_grant CASCADE;
"""


def main() -> None:
    conn = connect()
    conn.execute(RLS_TEARDOWN)
    div_pumps = conn.execute(
        "SELECT member_id FROM spike.member WHERE dim_id = 2 AND code = 'DIV_PUMPS'"
    ).fetchone()[0]
    ccs = [r[0] for r in conn.execute(
        """SELECT m.member_id FROM spike.closure c JOIN spike.member m ON m.member_id = c.descendant_id
           WHERE c.hier_id = 1 AND c.ancestor_id = %s AND NOT m.is_group
           ORDER BY m.code LIMIT 60""", (div_pumps,))]  # fmt: skip
    one_cc = ccs[0]
    params = {"ccs": ccs, "cc": one_cc, "start": YEAR[0], "end": YEAR[1]}

    def run(sql: str) -> object:
        return conn.execute(sql, params).fetchall()

    out: list[tuple[str, str]] = []

    def bench(label: str, sql: str) -> list[tuple[object, ...]]:
        ms, rows = timed(lambda: run(sql))
        out.append((label, f"{ms:8.1f} ms  ({len(rows):,} rows)"))  # type: ignore[arg-type]
        return rows  # type: ignore[return-value]

    wide = bench("Q1  60 CC x 12 months, ROLLUP     wide", Q1_WIDE)
    eav = bench("Q1  60 CC x 12 months, ROLLUP     eav", Q1_EAV)
    assert sorted(map(str, wide)) == sorted(map(str, eav)), "Q1 results differ"
    bench("Q1b 1 CC x accounts x 12, ROLLUP   wide", Q1B_WIDE)
    bench("Q1b 1 CC x accounts x 12, ROLLUP   eav", Q1B_EAV)
    w2 = bench("Q2  countries x 40 groups x 12     wide", Q2_WIDE)
    e2 = bench("Q2  countries x 40 groups x 12     eav", Q2_EAV)
    assert sorted(map(str, w2)) == sorted(map(str, e2)), "Q2 results differ"

    # D-14: leaves from SQL, subtotals in Python (Decimal), compared with ROLLUP.
    parent = dict(conn.execute(
        "SELECT member_id, parent_id FROM spike.hier_edge WHERE hier_id = 1 AND member_id = ANY(%s)",
        (ccs,)).fetchall())  # fmt: skip

    def python_rollup() -> dict[tuple[object, object, date], Decimal]:
        totals: dict[tuple[object, object, date], Decimal] = defaultdict(Decimal)
        for cc, period, amount in conn.execute(Q1_WIDE_LEAVES, params):
            totals[(parent[cc], cc, period)] += amount
            totals[(parent[cc], None, period)] += amount
            totals[(None, None, period)] += amount
        return totals

    ms, py = timed(python_rollup)
    out.append(("Q1  leaves + Python subtotals      wide", f"{ms:8.1f} ms"))
    assert {(a, b, c): d for a, b, c, d in wide} == dict(py), "Python subtotals differ"  # type: ignore[misc]

    # Q3: bulk upsert, insert then update path, on a fresh budget version each round.
    accounts = [r[0] for r in conn.execute(
        """SELECT DISTINCT account_id FROM spike.fact_wide
           WHERE version_id = 1 AND cost_center_id = %s ORDER BY 1 LIMIT 7""", (one_cc,))]  # fmt: skip
    entity = conn.execute(
        "SELECT entity_id FROM spike.fact_wide WHERE cost_center_id = %s LIMIT 1", (one_cc,)
    ).fetchone()[0]
    cells: dict[str, list[object]] = {"p": [], "e": [], "c": [], "a": [], "v": []}
    for cc in ccs:
        for acc in accounts:
            for month in range(1, 13):
                for key, value in zip("pecav", (date(2027, month, 1), entity, cc, acc, Decimal("1234.56")), strict=True):
                    cells[key].append(value)
    n = len(cells["p"])

    for label, sql, cleanup in (
        ("wide", UPSERT_WIDE, "DELETE FROM spike.fact_wide WHERE version_id = 2"),
        ("eav", UPSERT_EAV, """DELETE FROM spike.fact_eav_member WHERE fact_id IN
                               (SELECT fact_id FROM spike.fact_eav WHERE version_id = 2);
                               DELETE FROM spike.fact_eav WHERE version_id = 2"""),
    ):
        inserts, updates = [], []
        for _ in range(3):
            conn.execute(cleanup)
            ms_i, _ = timed(lambda: conn.execute(sql, cells), runs=1)  # warm-up inserts, run = update
            inserts.append(ms_i)
        ms_u, _ = timed(lambda: conn.execute(sql, cells), runs=3)
        count = conn.execute(
            "SELECT count(*) FROM spike.fact_wide WHERE version_id = 2" if label == "wide"
            else "SELECT count(*) FROM spike.fact_eav WHERE version_id = 2").fetchone()[0]  # fmt: skip
        assert count == n, f"{label}: {count} rows after upsert, expected {n}"
        out.append((f"Q3  upsert {n:,} cells (update)       {label}", f"{ms_u:8.1f} ms"))
        conn.execute(cleanup)

    # Q4: RLS, as a non-owner role with a subtree grant.
    conn.execute(RLS_SETUP.replace("%(node)s", str(div_pumps)))
    conn.execute("SET ROLE spike_reader; SET app.user_id = '7'")
    rw = bench("Q4  Q1 under RLS                   wide", Q1_WIDE)
    bench("Q4  Q1 under RLS                   eav", Q1_EAV)
    bench("Q4  Q2 under RLS                   wide", Q2_WIDE)
    bench("Q4  Q2 under RLS                   eav", Q2_EAV)
    assert sorted(map(str, rw)) == sorted(map(str, wide)), "RLS changed a fully granted result"
    conn.execute("SET app.user_id = '8'")  # no grants
    hidden = run(Q1_WIDE)
    out.append(("Q4  Q1 for a user without grants   wide", f"{len(hidden)} rows"))  # type: ignore[arg-type]
    conn.execute(RLS_TEARDOWN)
    report(out)


if __name__ == "__main__":
    main()
