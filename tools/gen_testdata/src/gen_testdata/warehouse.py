"""Writing the simulated warehouse: raw tables in bronze, contract views in gold (TD-10).

Bronze looks like an ERP extract (German and English column names, local currency, ledger
signs, dated tranches). Gold is what an admin would point Planlayer at: one view per dataset,
in EUR, current hierarchy as of the generator's anchor date.
"""

from collections.abc import Iterable
from pathlib import Path

import psycopg
from psycopg import sql

from gen_testdata.actuals import Rows
from gen_testdata.master import ACTIVITY_TYPES, FUNCTION_ROOT, FUNCTIONS, REGIONS, Master

BRONZE_TABLES = {
    "generator_run": "variant text, seed int, date_mode text, anchor date, fiscal_start_month int",
    "buchungskreis": "bukrs text PRIMARY KEY, butxt text, land1 text, waers text",
    "fx_rates": (
        "period date, currency text, rate_to_eur numeric(18,6), PRIMARY KEY (period, currency)"
    ),
    "region": "region_code text PRIMARY KEY, region_name text",
    "land": "land1 text PRIMARY KEY, landx text, region_code text, bukrs text",
    "kostenstelle": (
        "kostl text, ktext text, ltext text, bukrs text, verantwortlich text, funktion text, "
        "gueltig_ab date, gueltig_bis date, PRIMARY KEY (kostl, gueltig_ab)"
    ),
    "cc_hierarchy_node": (
        "node_id text, node_text text, parent_node text, valid_from date, valid_to date, "
        "PRIMARY KEY (node_id, valid_from)"
    ),
    "cc_hierarchy_assignment": (
        "kostl text, node_id text, valid_from date, valid_to date, PRIMARY KEY (kostl, valid_from)"
    ),
    "account_hierarchy_node": (
        "node_id text PRIMARY KEY, node_text text, parent_node text, sort int"
    ),
    # kontoart: E = Ertrag (revenue, credit side), A = Aufwand (cost, debit side)
    "sachkonto": "saknr text PRIMARY KEY, txt50 text, kontoart text, node_id text",
    "product_line": "line_code text PRIMARY KEY, line_name text",
    "product_group": "group_code text PRIMARY KEY, group_name text, line_code text",
    "material": "matnr text PRIMARY KEY, maktx text, group_code text, list_price_eur numeric(18,2)",
    "billing_lines": (
        "vbeln text, posnr int, fkdat date, bukrs text, land1 text, matnr text, "
        "fkimg numeric(18,3), netwr_lc numeric(18,2), waers text"
    ),
    "gl_lines": (
        "belnr text, buzei int, bukrs text, budat date, kostl text, saknr text, "
        "betrag_lc numeric(18,2), waers text"
    ),
    # Capacity planning (ADR 0013): activity types (Leistungsarten), work centers
    # (Arbeitsplätze), their rates (Tarife) and actual hours (Ist-Leistung).
    "leistungsart": "lstar text PRIMARY KEY, ltext text, saknr text",
    "arbeitsplatz": "arbpl text PRIMARY KEY, ktext text, kostl text",
    "tarif": "arbpl text, lstar text, tarif_eur numeric(18,4), PRIMARY KEY (arbpl, lstar)",
    "leistung_ist": (
        "periode date, arbpl text, lstar text, menge numeric(18,2), "
        "PRIMARY KEY (periode, arbpl, lstar)"
    ),
}

# Gold views. {b} is the bronze schema, {g} the gold schema.
GOLD_VIEWS = {
    "v_entities": """
        SELECT bukrs AS entity_code, butxt AS entity_name, land1 AS country_code,
               waers AS local_currency
        FROM {b}.buchungskreis""",
    "v_cost_centers": """
        SELECT DISTINCT ON (kostl) kostl AS cost_center_code, ktext AS cost_center_name,
               ltext AS description, bukrs AS entity_code, verantwortlich AS owner,
               funktion AS function, gueltig_ab AS valid_from, gueltig_bis AS valid_to
        FROM {b}.kostenstelle
        ORDER BY kostl, gueltig_ab DESC""",
    # Parent-child, groups and leaves, as valid at the anchor date (closed leaves: their last
    # parent; leaves created ahead of time: their first). Shape shared by all hierarchy views:
    # member, name, parent, is_group, sort.
    "v_cost_center_hierarchy": """
        WITH run AS (SELECT anchor FROM {b}.generator_run),
        nodes AS (
            SELECT DISTINCT ON (n.node_id) n.node_id, n.node_text, n.parent_node
            FROM {b}.cc_hierarchy_node n, run
            WHERE n.valid_from <= run.anchor
            ORDER BY n.node_id, n.valid_from DESC),
        leaves AS (
            SELECT DISTINCT ON (a.kostl) a.kostl, k.cost_center_name AS ktext, a.node_id
            FROM {b}.cc_hierarchy_assignment a
            JOIN {g}.v_cost_centers k ON k.cost_center_code = a.kostl, run
            ORDER BY a.kostl, a.valid_from <= run.anchor DESC,
                     CASE WHEN a.valid_from <= run.anchor THEN a.valid_from END DESC,
                     a.valid_from)
        SELECT node_id AS member_code, node_text AS member_name, parent_node AS parent_code,
               true AS is_group, row_number() OVER (ORDER BY node_id)::int AS sort_order
        FROM nodes
        UNION ALL
        SELECT kostl, ktext, node_id, false, row_number() OVER (ORDER BY kostl)::int
        FROM leaves""",
    # Alternate hierarchy over the same leaves (TD-02): cost centers by function.
    "v_cost_center_function_hierarchy": """
        SELECT '{func_root}' AS member_code, 'By function' AS member_name,
               NULL::text AS parent_code, true AS is_group, 0 AS sort_order
        UNION ALL
        SELECT f.code, f.name, '{func_root}', true, f.sort
        FROM (VALUES {functions}) AS f(code, name, sort)
        UNION ALL
        SELECT cost_center_code, cost_center_name, 'FUNC_' || upper(function), false,
               row_number() OVER (ORDER BY cost_center_code)::int
        FROM {g}.v_cost_centers""",
    "v_accounts": """
        SELECT saknr AS account_code, txt50 AS account_name,
               CASE kontoart WHEN 'E' THEN 'revenue' ELSE 'cost' END AS account_type
        FROM {b}.sachkonto""",
    "v_account_hierarchy": """
        SELECT node_id AS member_code, node_text AS member_name, parent_node AS parent_code,
               true AS is_group, sort AS sort_order
        FROM {b}.account_hierarchy_node
        UNION ALL
        SELECT saknr, txt50, node_id, false, row_number() OVER (ORDER BY saknr)::int
        FROM {b}.sachkonto""",
    "v_products": """
        SELECT m.matnr AS product_code, m.maktx AS product_name, g.group_code, g.line_code
        FROM {b}.material m JOIN {b}.product_group g USING (group_code)""",
    "v_product_hierarchy": """
        SELECT 'ALL' AS member_code, 'All products' AS member_name, NULL::text AS parent_code,
               true AS is_group, 0 AS sort_order
        UNION ALL
        SELECT line_code, line_name, 'ALL', true, row_number() OVER (ORDER BY line_code)::int
        FROM {b}.product_line
        UNION ALL
        SELECT group_code, group_name, line_code, true,
               row_number() OVER (ORDER BY group_code)::int
        FROM {b}.product_group
        UNION ALL
        SELECT matnr, maktx, group_code, false, row_number() OVER (ORDER BY matnr)::int
        FROM {b}.material""",
    "v_countries": """
        SELECT land1 AS country_code, landx AS country_name, region_code, bukrs AS entity_code
        FROM {b}.land""",
    "v_country_hierarchy": """
        SELECT 'WORLD' AS member_code, 'World' AS member_name, NULL::text AS parent_code,
               true AS is_group, 0 AS sort_order
        UNION ALL
        SELECT region_code, region_name, 'WORLD', true,
               row_number() OVER (ORDER BY region_code)::int
        FROM {b}.region
        UNION ALL
        SELECT land1, landx, region_code, false, row_number() OVER (ORDER BY land1)::int
        FROM {b}.land""",
    # Monthly GL actuals in EUR, ledger signs (debit positive: revenue is negative).
    "v_gl_actuals_ledger": """
        SELECT date_trunc('month', l.budat)::date AS period, l.bukrs AS entity_code,
               l.kostl AS cost_center_code, l.saknr AS account_code,
               round(sum(l.betrag_lc * r.rate_to_eur), 2) AS amount
        FROM {b}.gl_lines l
        JOIN {b}.fx_rates r ON r.period = date_trunc('month', l.budat)::date
                           AND r.currency = l.waers
        GROUP BY 1, 2, 3, 4""",
    # The same in natural signs: revenue and cost both positive (T-7).
    "v_gl_actuals_natural": """
        SELECT a.period, a.entity_code, a.cost_center_code, a.account_code,
               CASE s.kontoart WHEN 'E' THEN -a.amount ELSE a.amount END AS amount
        FROM {g}.v_gl_actuals_ledger a JOIN {b}.sachkonto s ON s.saknr = a.account_code""",
    # Monthly sales in EUR, long format: one row per measure (T-23).
    "v_sales_actuals": """
        WITH monthly AS (
            SELECT date_trunc('month', l.fkdat)::date AS period, l.bukrs, l.land1, l.matnr,
                   sum(l.fkimg) AS quantity, round(sum(l.netwr_lc * r.rate_to_eur), 2) AS revenue
            FROM {b}.billing_lines l
            JOIN {b}.fx_rates r ON r.period = date_trunc('month', l.fkdat)::date
                               AND r.currency = l.waers
            GROUP BY 1, 2, 3, 4)
        SELECT period, bukrs AS entity_code, land1 AS country_code, matnr AS product_code,
               m.measure, m.value
        FROM monthly
        CROSS JOIN LATERAL (VALUES ('quantity', quantity), ('revenue', revenue))
            AS m(measure, value)""",
    "v_activity_types": """
        SELECT lstar AS activity_type_code, ltext AS activity_type_name, saknr AS account_code,
               NULL::text AS variable_account_code
        FROM {b}.leistungsart""",
    "v_work_centers": """
        SELECT arbpl AS work_center_code, ktext AS work_center_name, kostl AS cost_center_code
        FROM {b}.arbeitsplatz""",
    "v_work_center_rates": """
        SELECT arbpl AS work_center_code, lstar AS activity_type_code, tarif_eur AS rate
        FROM {b}.tarif""",
    "v_capacity_actuals": """
        SELECT periode AS period, arbpl AS work_center_code, lstar AS activity_type_code,
               menge AS hours
        FROM {b}.leistung_ist""",
}

# Views exported to files, with the order that makes files reproducible.
EXPORT_ORDER = {
    "v_entities": "entity_code",
    "v_cost_centers": "cost_center_code",
    "v_cost_center_hierarchy": "member_code",
    "v_cost_center_function_hierarchy": "member_code",
    "v_accounts": "account_code",
    "v_account_hierarchy": "member_code",
    "v_products": "product_code",
    "v_product_hierarchy": "member_code",
    "v_countries": "country_code",
    "v_country_hierarchy": "member_code",
    "v_gl_actuals_ledger": "period, entity_code, cost_center_code, account_code",
    "v_gl_actuals_natural": "period, entity_code, cost_center_code, account_code",
    "v_sales_actuals": "period, entity_code, country_code, product_code, measure",
    "v_activity_types": "activity_type_code",
    "v_work_centers": "work_center_code",
    "v_work_center_rates": "work_center_code, activity_type_code",
    "v_capacity_actuals": "period, work_center_code, activity_type_code",
}


def load(
    conn: psycopg.Connection,
    master: Master,
    rows: Rows,
    date_mode: str,
    bronze: str = "bronze",
    gold: str = "gold",
) -> dict[str, int]:
    """Replace the generator's tables and views; returns row counts per bronze table."""
    b, g = sql.Identifier(bronze), sql.Identifier(gold)
    with conn.cursor() as cur:
        for view in reversed(GOLD_VIEWS):
            cur.execute(
                sql.SQL("DROP VIEW IF EXISTS {}.{} CASCADE").format(g, sql.Identifier(view))
            )
        for table, columns in BRONZE_TABLES.items():
            name = sql.Identifier(table)
            cur.execute(sql.SQL("DROP TABLE IF EXISTS {}.{} CASCADE").format(b, name))
            cur.execute(sql.SQL("CREATE TABLE {}.{} (" + columns + ")").format(b, name))

        cal = master.calendar
        data: dict[str, Iterable[tuple[object, ...]]] = {
            "generator_run": [
                (master.variant.name, rows.seed, date_mode, cal.anchor, cal.fiscal_start_month)
            ],
            "buchungskreis": [(e.code, e.name, e.country, e.currency) for e in master.entities],
            "region": sorted({(c.region, REGIONS[c.region]) for c in master.countries}),
            "land": [(c.code, c.name, c.region, c.entity) for c in master.countries],
            "kostenstelle": [
                (
                    c.code,
                    c.name,
                    c.description,
                    c.entity,
                    c.owner,
                    c.function,
                    c.valid_from,
                    c.valid_to,
                )
                for c in master.cost_centers
            ],
            "cc_hierarchy_node": [
                (n.code, n.name, n.parent, n.valid_from, n.valid_to) for n in master.cc_nodes
            ],
            "cc_hierarchy_assignment": [
                (a.code, a.parent, a.valid_from, a.valid_to) for a in master.cc_assignments
            ],
            "account_hierarchy_node": _account_nodes(master),
            "sachkonto": [
                (a.code, a.name, "E" if a.account_type == "revenue" else "A", a.group)
                for a in master.accounts
            ],
            "product_line": sorted(master.product_lines.items()),
            "product_group": [(k, n, line) for k, (n, line) in master.product_groups.items()],
            "material": [
                (p.code, p.name, p.group, f"{p.price_eur_cents / 100:.2f}") for p in master.products
            ],
            "fx_rates": rows.fx_rates(),
            # Billing first: it collects the revenue that gl_lines books.
            "billing_lines": rows.billing_lines(),
            "gl_lines": rows.gl_lines(),
            "leistungsart": [(code, name, account) for code, name, account in ACTIVITY_TYPES],
            "arbeitsplatz": [(w.code, w.name, w.cost_center) for w in master.work_centers],
            "tarif": [
                (w.code, activity, rate)
                for w in master.work_centers
                for activity, rate, _hours in w.outputs
            ],
            "leistung_ist": rows.capacity_hours(),
        }
        counts: dict[str, int] = {}
        for table, table_rows in data.items():
            counts[table] = _copy(cur, b, table, table_rows)

        functions = ", ".join(
            f"('FUNC_{key.upper()}', '{name}', {i})"
            for i, (key, name) in enumerate(FUNCTIONS.items(), 1)
        )
        for view, body in GOLD_VIEWS.items():
            text = body.format(b=bronze, g=gold, func_root=FUNCTION_ROOT, functions=functions)
            cur.execute(
                sql.SQL("CREATE VIEW {}.{} AS ").format(g, sql.Identifier(view)) + sql.SQL(text)
            )
    conn.commit()
    return counts


def _copy(
    cur: psycopg.Cursor, schema: sql.Identifier, table: str, rows: Iterable[tuple[object, ...]]
) -> int:
    n = 0
    stmt = sql.SQL("COPY {}.{} FROM STDIN").format(schema, sql.Identifier(table))
    with cur.copy(stmt) as copy:
        for row in rows:
            copy.write_row(row)
            n += 1
    return n


def _account_nodes(master: Master) -> list[tuple[object, ...]]:
    nodes: list[tuple[object, ...]] = [("PL", "Profit and loss", None, 0)]
    for i, (code, name) in enumerate(master.account_sections.items(), 1):
        nodes.append((code, name, "PL", i))
    for i, (code, (name, section)) in enumerate(master.account_groups.items(), 1):
        nodes.append((code, name, section, i))
    return nodes


def export(conn: psycopg.Connection, out: Path, gold: str = "gold") -> list[Path]:
    """Write every gold view as CSV and Parquet (TD-11 (b)); amounts stay decimal."""
    import pyarrow as pa
    import pyarrow.csv as pacsv
    import pyarrow.parquet as pq

    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    with conn.cursor() as cur:
        for view, order in EXPORT_ORDER.items():
            csv_path = out / f"{view}.csv"
            query = sql.SQL(
                "COPY (SELECT * FROM {}.{} ORDER BY {}) TO STDOUT WITH CSV HEADER"
            ).format(sql.Identifier(gold), sql.Identifier(view), sql.SQL(order))
            with csv_path.open("wb") as f, cur.copy(query) as copy:
                for chunk in copy:
                    f.write(chunk)
            cur.execute(
                sql.SQL("SELECT * FROM {}.{} LIMIT 0").format(
                    sql.Identifier(gold), sql.Identifier(view)
                )
            )
            types = {}
            for col in cur.description or []:
                if col.name in ("amount", "value", "rate_to_eur"):
                    types[col.name] = pa.decimal128(18, 3 if col.name == "value" else 2)
                elif col.name in ("period", "valid_from", "valid_to"):
                    types[col.name] = pa.date32()
                elif col.name == "is_group":
                    types[col.name] = pa.bool_()
                elif col.name == "sort_order":
                    types[col.name] = pa.int32()
                else:
                    types[col.name] = pa.string()
            table = pacsv.read_csv(
                csv_path,
                convert_options=pacsv.ConvertOptions(
                    column_types=types,
                    strings_can_be_null=True,
                    # Postgres CSV writes booleans as t/f.
                    true_values=["t"],
                    false_values=["f"],
                ),
            )
            parquet_path = out / f"{view}.parquet"
            pq.write_table(table, parquet_path)
            written += [csv_path, parquet_path]
    return written
