# /// script
# requires-python = ">=3.13"
# dependencies = ["psycopg[binary]==3.3.8"]
# ///
"""S-01 follow-up: one fact table per planning area (cost centers), RLS without a NULL branch."""

from common import connect, report, timed

from s01_bench import Q1_WIDE, RLS_TEARDOWN, YEAR

SETUP = """
    DROP TABLE IF EXISTS spike.fact_cc;
    DROP TABLE IF EXISTS spike.user_grant CASCADE;
    CREATE TABLE spike.fact_cc AS
    SELECT version_id, layer_id, measure_id, period, entity_id, cost_center_id, account_id,
           amount, row_version
    FROM spike.fact_wide WHERE cost_center_id IS NOT NULL;
    CREATE UNIQUE INDEX ON spike.fact_cc
        (version_id, layer_id, measure_id, period, entity_id, cost_center_id, account_id);
    CREATE INDEX ON spike.fact_cc (version_id, cost_center_id, period);
    ANALYZE spike.fact_cc;
    DO $$ BEGIN
        IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'spike_reader') THEN
            DROP OWNED BY spike_reader; DROP ROLE spike_reader;
        END IF;
    END $$;
    CREATE ROLE spike_reader;
    GRANT USAGE ON SCHEMA spike TO spike_reader;
    CREATE TABLE spike.user_grant (user_id int, node_id int);
    INSERT INTO spike.user_grant VALUES (7, %(node)s);
    ALTER TABLE spike.fact_cc ENABLE ROW LEVEL SECURITY;
    CREATE POLICY scope ON spike.fact_cc FOR SELECT TO spike_reader USING (
        cost_center_id = ANY (ARRAY(
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
    q1 = Q1_WIDE.replace("spike.fact_wide", "spike.fact_cc")
    wide_ms, wide = timed(lambda: conn.execute(Q1_WIDE, params).fetchall())
    conn.execute(SETUP.replace("%(node)s", str(div)))
    plain_ms, plain = timed(lambda: conn.execute(q1, params).fetchall())
    conn.execute("SET ROLE spike_reader; SET app.user_id = '7'")
    rls_ms, rls = timed(lambda: conn.execute(q1, params).fetchall())
    assert sorted(map(str, rls)) == sorted(map(str, plain)) == sorted(map(str, wide))
    conn.execute("RESET ROLE; DROP TABLE spike.user_grant CASCADE; DROP OWNED BY spike_reader; DROP ROLE spike_reader")
    report([
        ("Q1 one table for all areas, no RLS", f"{wide_ms:8.1f} ms"),
        ("Q1 cost center table, no RLS", f"{plain_ms:8.1f} ms"),
        ("Q1 cost center table, RLS", f"{rls_ms:8.1f} ms"),
    ])


if __name__ == "__main__":
    main()
