# /// script
# requires-python = ">=3.13"
# dependencies = ["psycopg[binary]==3.3.8"]
# ///
"""S-01 follow-up on fact_wide:
- Q2 against actuals synced at the plan grain (product group instead of SKU).
- RLS policy with the scope as an ARRAY(...) init plan instead of an IN subquery.
"""

from common import connect, report, timed

from s01_bench import Q1_WIDE, Q2_WIDE, RLS_TEARDOWN, YEAR

GRAIN = """
    DROP TABLE IF EXISTS spike.fact_wide_group;
    CREATE TABLE spike.fact_wide_group AS
    SELECT f.version_id, f.layer_id, f.measure_id, f.period, f.entity_id, f.country_id,
           e.parent_id AS product_id, sum(f.amount) AS amount
    FROM spike.fact_wide f JOIN spike.hier_edge e ON e.hier_id = 5 AND e.member_id = f.product_id
    WHERE f.product_id IS NOT NULL
    GROUP BY 1, 2, 3, 4, 5, 6, 7;
    CREATE INDEX ON spike.fact_wide_group (version_id, period);
    ANALYZE spike.fact_wide_group;
"""

Q2_GROUP = """
    SELECT country_id, product_id, period, sum(amount)
    FROM spike.fact_wide_group
    WHERE version_id = 1 AND measure_id = 1 AND period >= %(start)s AND period < %(end)s
    GROUP BY 1, 2, 3"""

RLS_ARRAY = """
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
        cost_center_id IS NULL OR cost_center_id = ANY (ARRAY(
            SELECT c.descendant_id FROM spike.user_grant g
            JOIN spike.closure c ON c.hier_id = 1 AND c.ancestor_id = g.node_id
            WHERE g.user_id = current_setting('app.user_id')::int)));
    GRANT SELECT ON ALL TABLES IN SCHEMA spike TO spike_reader;
"""


def main() -> None:
    conn = connect()
    conn.execute(RLS_TEARDOWN)
    div = conn.execute(
        "SELECT member_id FROM spike.member WHERE dim_id = 2 AND code = 'DIV_PUMPS'"
    ).fetchone()[0]
    ccs = [r[0] for r in conn.execute(
        """SELECT m.member_id FROM spike.closure c JOIN spike.member m ON m.member_id = c.descendant_id
           WHERE c.hier_id = 1 AND c.ancestor_id = %s AND NOT m.is_group
           ORDER BY m.code LIMIT 60""", (div,))]  # fmt: skip
    params = {"ccs": ccs, "start": YEAR[0], "end": YEAR[1]}
    out = []
    conn.execute(GRAIN)
    n = conn.execute("SELECT count(*) FROM spike.fact_wide_group").fetchone()[0]
    sku_ms, sku = timed(lambda: conn.execute(Q2_WIDE, params).fetchall())
    grp_ms, grp = timed(lambda: conn.execute(Q2_GROUP, params).fetchall())
    assert sorted(map(str, sku)) == sorted(map(str, grp)), "plan-grain sync changes Q2"
    out.append(("Q2 at SKU grain", f"{sku_ms:8.1f} ms"))
    out.append((f"Q2 at group grain ({n:,} rows synced)", f"{grp_ms:8.1f} ms"))

    plain_ms, plain = timed(lambda: conn.execute(Q1_WIDE, params).fetchall())
    conn.execute(RLS_ARRAY.replace("%(node)s", str(div)))
    conn.execute("SET ROLE spike_reader; SET app.user_id = '7'")
    rls_ms, rls = timed(lambda: conn.execute(Q1_WIDE, params).fetchall())
    assert sorted(map(str, rls)) == sorted(map(str, plain))
    out.append(("Q1 without RLS", f"{plain_ms:8.1f} ms"))
    out.append(("Q1 with RLS, ARRAY(...) scope", f"{rls_ms:8.1f} ms"))
    conn.execute(RLS_TEARDOWN)
    report(out)


if __name__ == "__main__":
    main()
