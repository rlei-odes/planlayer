"""Loading into Postgres and reading the gold views. Needs PLANLAYER_TEST_DATABASE_URL."""

import os
import secrets
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import psycopg
import pyarrow.parquet as pq
import pytest

from gen_testdata import master as m
from gen_testdata import warehouse
from gen_testdata.actuals import Rows
from gen_testdata.calendar import FIXED_ANCHOR, Calendar

URL = os.environ.get("PLANLAYER_TEST_DATABASE_URL", "").replace("postgresql+psycopg", "postgresql")


@pytest.fixture(scope="module")
def loaded() -> Iterator[tuple[psycopg.Connection, str]]:
    if not URL:
        pytest.skip("PLANLAYER_TEST_DATABASE_URL is not set")
    suffix = secrets.token_hex(4)
    bronze, gold = f"test_bronze_{suffix}", f"test_gold_{suffix}"
    conn = psycopg.connect(URL)
    conn.execute(f"CREATE SCHEMA {bronze}; CREATE SCHEMA {gold}")
    variant = m.VARIANTS["standard"]
    master = m.build(variant, Calendar(FIXED_ANCHOR, 1, variant.full_years), 42)
    warehouse.load(conn, master, Rows(master, 42), "fixed", bronze, gold)
    try:
        yield conn, gold
    finally:
        conn.rollback()
        conn.execute(f"DROP SCHEMA {gold} CASCADE; DROP SCHEMA {bronze} CASCADE")
        conn.commit()
        conn.close()


def one(conn: psycopg.Connection, query: str) -> tuple[object, ...]:
    row = conn.execute(query).fetchone()
    assert row is not None
    return tuple(row)


def test_natural_signs_flip_revenue_only(loaded: tuple[psycopg.Connection, str]) -> None:
    conn, g = loaded
    ledger, natural = one(
        conn,
        f"""
        SELECT sum(l.amount), sum(n.amount)
        FROM {g}.v_gl_actuals_ledger l
        JOIN {g}.v_gl_actuals_natural n USING (period, entity_code, cost_center_code, account_code)
        JOIN {g}.v_accounts a USING (account_code)
        WHERE a.account_type = 'revenue'""",
    )
    assert isinstance(ledger, Decimal) and isinstance(natural, Decimal)
    assert ledger < 0 and natural == -ledger
    (cost_differs,) = one(
        conn,
        f"""
        SELECT count(*) FROM {g}.v_gl_actuals_ledger l
        JOIN {g}.v_gl_actuals_natural n USING (period, entity_code, cost_center_code, account_code)
        JOIN {g}.v_accounts a USING (account_code)
        WHERE a.account_type = 'cost' AND l.amount <> n.amount""",
    )
    assert cost_differs == 0


def test_zero_and_missing_are_distinct(loaded: tuple[psycopg.Connection, str]) -> None:
    conn, g = loaded
    zero, negative = one(
        conn,
        f"""
        SELECT count(*) FILTER (WHERE amount = 0), count(*) FILTER (WHERE amount < 0)
        FROM {g}.v_gl_actuals_natural""",
    )
    assert isinstance(zero, int) and isinstance(negative, int)
    assert zero > 0 and negative > 0
    # Missing: some series lack months although the cost center was active.
    (gaps,) = one(
        conn,
        f"""
        SELECT count(*) FROM (
            SELECT cost_center_code, account_code, count(*) AS months
            FROM {g}.v_gl_actuals_natural GROUP BY 1, 2) s
        JOIN {g}.v_cost_centers c USING (cost_center_code)
        WHERE c.valid_from < '2023-01-01' AND c.valid_to > '2026-06-30' AND s.months < 42""",
    )
    assert isinstance(gaps, int) and gaps > 0


def test_gl_revenue_reconciles_with_sales(loaded: tuple[psycopg.Connection, str]) -> None:
    conn, g = loaded
    (diff,) = one(
        conn,
        f"""
        SELECT max(abs(gl - sales)) FROM (
            SELECT period, entity_code, sum(amount) AS gl FROM {g}.v_gl_actuals_natural
            WHERE account_code IN ('4000', '4010', '4020', '4030', '4040') GROUP BY 1, 2) a
        JOIN (
            SELECT period, entity_code, sum(value) AS sales FROM {g}.v_sales_actuals
            WHERE measure = 'revenue' GROUP BY 1, 2) b USING (period, entity_code)""",
    )
    # FX rounding: converted per GL month total versus per billing line.
    assert isinstance(diff, Decimal) and diff < 1


def test_hierarchy_is_current_as_of_anchor(loaded: tuple[psycopg.Connection, str]) -> None:
    conn, g = loaded
    rows = dict(
        conn.execute(f"""
        SELECT member_name, parent_code FROM {g}.v_cost_center_hierarchy
        WHERE member_name IN ('Quality Assurance', 'Legacy Assembly', 'Executive Office')""")
    )
    assert rows == {
        "Quality Assurance": "DEP_PUMP_ENG",
        "Legacy Assembly": "DEP_VALVE_US",
        "Executive Office": "DIV_CORP",
    }
    (name,) = one(
        conn,
        f"""
        SELECT member_name FROM {g}.v_cost_center_hierarchy WHERE member_code = 'DEP_PUMP_PL'""",
    )
    assert name == m.RENAMED_TO
    (roots,) = one(
        conn, f"SELECT count(*) FROM {g}.v_cost_center_hierarchy WHERE parent_code IS NULL"
    )
    assert roots == 1


def test_new_and_closed_cost_centers_have_actuals_only_while_valid(
    loaded: tuple[psycopg.Connection, str],
) -> None:
    conn, g = loaded
    first_new, last_closed = one(
        conn,
        f"""
        SELECT min(a.period) FILTER (WHERE c.cost_center_name = 'Digital Service'),
               max(a.period) FILTER (WHERE c.cost_center_name = 'Legacy Assembly')
        FROM {g}.v_gl_actuals_natural a JOIN {g}.v_cost_centers c USING (cost_center_code)""",
    )
    assert str(first_new) == "2025-07-01"
    assert str(last_closed) == "2025-12-01"


def test_every_hierarchy_has_one_root_and_known_parents(
    loaded: tuple[psycopg.Connection, str],
) -> None:
    conn, g = loaded
    for view in ("cost_center", "cost_center_function", "account", "product", "country"):
        roots, orphans = one(
            conn,
            f"""
            SELECT count(*) FILTER (WHERE parent_code IS NULL),
                   count(*) FILTER (WHERE parent_code IS NOT NULL AND parent_code NOT IN (
                       SELECT member_code FROM {g}.v_{view}_hierarchy))
            FROM {g}.v_{view}_hierarchy""",
        )
        assert (view, roots, orphans) == (view, 1, 0)


def test_export_keeps_decimals(loaded: tuple[psycopg.Connection, str], tmp_path: Path) -> None:
    conn, g = loaded
    warehouse.export(conn, tmp_path, g)
    table = pq.read_table(tmp_path / "v_gl_actuals_natural.parquet")
    assert str(table.schema.field("amount").type) == "decimal128(18, 2)"
    assert str(table.schema.field("period").type) == "date32[day]"
    (count,) = one(conn, f"SELECT count(*) FROM {g}.v_gl_actuals_natural")
    assert table.num_rows == count
