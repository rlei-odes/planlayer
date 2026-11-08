# /// script
# requires-python = ">=3.13"
# dependencies = ["psycopg[binary]==3.3.8"]
# ///
"""S-01/S-02 setup: copy the large Nordwerk dataset (large_gold) into schema ``spike``.

Two fact layouts from D-01:
- fact_wide: one column per dimension (options b and c; c only differs in how columns are added).
- fact_eav: generic, one fact row plus one member row per dimension (option a).
Plus members, hierarchy edges and a closure table for S-02.
"""

import sys
import time

from common import connect, report

GOLD = sys.argv[1] if len(sys.argv) > 1 else "large_gold"

STEPS = [
    ("schema", """
        DROP SCHEMA IF EXISTS spike CASCADE;
        CREATE SCHEMA spike;
        CREATE TABLE spike.member (
            member_id int GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            dim_id smallint NOT NULL, code text NOT NULL, name text, is_group bool NOT NULL,
            UNIQUE (dim_id, code));
        CREATE TABLE spike.hier_edge (
            hier_id smallint, member_id int, parent_id int, PRIMARY KEY (hier_id, member_id));
        CREATE TABLE spike.closure (
            hier_id smallint, ancestor_id int, descendant_id int, depth smallint,
            PRIMARY KEY (hier_id, ancestor_id, descendant_id));
    """),
    ("members", """
        INSERT INTO spike.member (dim_id, code, name, is_group)
        SELECT 1, entity_code, entity_name, false FROM {g}.v_entities
        UNION ALL SELECT 2, member_code, member_name, is_group FROM {g}.v_cost_center_hierarchy
        UNION ALL SELECT 2, member_code, member_name, is_group
                  FROM {g}.v_cost_center_function_hierarchy WHERE is_group
        UNION ALL SELECT 3, member_code, member_name, is_group FROM {g}.v_account_hierarchy
        UNION ALL SELECT 4, member_code, member_name, is_group FROM {g}.v_country_hierarchy
        UNION ALL SELECT 5, member_code, member_name, is_group FROM {g}.v_product_hierarchy;
    """),
    ("hierarchy edges", """
        INSERT INTO spike.hier_edge
        SELECT h.hier, m.member_id, p.member_id
        FROM (SELECT 1 AS hier, 2 AS dim, member_code, parent_code FROM {g}.v_cost_center_hierarchy
              UNION ALL SELECT 2, 2, member_code, parent_code
                        FROM {g}.v_cost_center_function_hierarchy
              UNION ALL SELECT 3, 3, member_code, parent_code FROM {g}.v_account_hierarchy
              UNION ALL SELECT 4, 4, member_code, parent_code FROM {g}.v_country_hierarchy
              UNION ALL SELECT 5, 5, member_code, parent_code FROM {g}.v_product_hierarchy) h
        JOIN spike.member m ON m.dim_id = h.dim AND m.code = h.member_code
        LEFT JOIN spike.member p ON p.dim_id = h.dim AND p.code = h.parent_code;
    """),
    ("closure", """
        INSERT INTO spike.closure
        WITH RECURSIVE c AS (
            SELECT hier_id, member_id AS anc, member_id AS des, 0 AS depth FROM spike.hier_edge
            UNION ALL
            SELECT c.hier_id, e.parent_id, c.des, c.depth + 1
            FROM c JOIN spike.hier_edge e ON e.hier_id = c.hier_id AND e.member_id = c.anc
            WHERE e.parent_id IS NOT NULL)
        SELECT hier_id, anc, des, depth FROM c;
        CREATE INDEX ON spike.closure (hier_id, descendant_id);
    """),
    ("fact_wide load", """
        CREATE TABLE spike.fact_wide (
            version_id smallint NOT NULL, layer_id smallint NOT NULL, measure_id smallint NOT NULL,
            period date NOT NULL,
            entity_id int, cost_center_id int, account_id int, country_id int, product_id int,
            amount numeric(18,2) NOT NULL, row_version bigint NOT NULL DEFAULT 1);
        INSERT INTO spike.fact_wide
            (version_id, layer_id, measure_id, period, entity_id, cost_center_id, account_id, amount)
        SELECT 1, 1, 1, g.period, e.member_id, c.member_id, a.member_id, g.amount
        FROM {g}.v_gl_actuals_natural g
        JOIN spike.member e ON e.dim_id = 1 AND e.code = g.entity_code
        JOIN spike.member c ON c.dim_id = 2 AND c.code = g.cost_center_code
        JOIN spike.member a ON a.dim_id = 3 AND a.code = g.account_code;
        INSERT INTO spike.fact_wide
            (version_id, layer_id, measure_id, period, entity_id, country_id, product_id, amount)
        SELECT 1, 1, CASE s.measure WHEN 'revenue' THEN 1 ELSE 2 END, s.period,
               e.member_id, c.member_id, p.member_id, s.value
        FROM {g}.v_sales_actuals s
        JOIN spike.member e ON e.dim_id = 1 AND e.code = s.entity_code
        JOIN spike.member c ON c.dim_id = 4 AND c.code = s.country_code
        JOIN spike.member p ON p.dim_id = 5 AND p.code = s.product_code;
    """),
    ("fact_wide indexes", """
        CREATE UNIQUE INDEX fact_wide_grain ON spike.fact_wide
            (version_id, layer_id, measure_id, period, entity_id, cost_center_id, account_id,
             country_id, product_id) NULLS NOT DISTINCT;
        CREATE INDEX ON spike.fact_wide (version_id, cost_center_id, period);
        CREATE INDEX ON spike.fact_wide (version_id, product_id, period);
        ANALYZE spike.fact_wide;
    """),
    ("fact_eav load", """
        CREATE TABLE spike.fact_eav (
            fact_id bigint PRIMARY KEY, version_id smallint NOT NULL, layer_id smallint NOT NULL,
            measure_id smallint NOT NULL, period date NOT NULL, amount numeric(18,2) NOT NULL,
            row_version bigint NOT NULL DEFAULT 1);
        CREATE TABLE spike.fact_eav_member (
            fact_id bigint, dim_id smallint, member_id int, PRIMARY KEY (fact_id, dim_id));
        CREATE TEMP TABLE w AS SELECT row_number() OVER () AS fact_id, * FROM spike.fact_wide;
        INSERT INTO spike.fact_eav
        SELECT fact_id, version_id, layer_id, measure_id, period, amount, row_version FROM w;
        INSERT INTO spike.fact_eav_member
        SELECT w.fact_id, d.dim_id, d.member_id
        FROM w CROSS JOIN LATERAL (VALUES (1, entity_id), (2, cost_center_id), (3, account_id),
                                          (4, country_id), (5, product_id)) AS d(dim_id, member_id)
        WHERE d.member_id IS NOT NULL;
        CREATE SEQUENCE spike.fact_seq;
        SELECT setval('spike.fact_seq', (SELECT max(fact_id) FROM spike.fact_eav));
    """),
    ("fact_eav indexes", """
        CREATE INDEX ON spike.fact_eav (version_id, period);
        CREATE INDEX ON spike.fact_eav_member (dim_id, member_id, fact_id);
        ANALYZE spike.fact_eav; ANALYZE spike.fact_eav_member;
    """),
]


def main() -> None:
    rows = []
    with connect() as conn:
        for name, sql in STEPS:
            start = time.perf_counter()
            conn.execute(sql.replace("{g}", GOLD))
            rows.append((name, f"{time.perf_counter() - start:7.1f} s"))
        for table in ("fact_wide", "fact_eav", "fact_eav_member", "closure"):
            n, size = conn.execute(
                f"SELECT count(*), pg_size_pretty(pg_total_relation_size('spike.{table}')) "
                f"FROM spike.{table}"
            ).fetchone()
            rows.append((f"{table} rows / size", f"{n:,} / {size}"))
    report(rows)


if __name__ == "__main__":
    main()
