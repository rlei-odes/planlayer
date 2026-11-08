# /// script
# requires-python = ">=3.13"
# dependencies = ["sqlalchemy==2.1.8", "psycopg[binary]==3.3.8", "pyarrow==26.0.1", "duckdb==2.0.0"]
# ///
"""S-05: both adapters read the same datasets identically and write back idempotently.

Needs the standard dataset in dwh.gold and its export in tools/gen_testdata/out/standard
(gen-testdata --out). The large dataset (dwh.large_gold) is used for throughput.
"""

import shutil
import time
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
from adapters import ParquetAdapter, PostgresAdapter, Query, Relation
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[2]
PARQUET = ROOT / "tools/gen_testdata/out/standard"
PG = "postgresql+psycopg://postgres:postgres@localhost:5432/dwh"

DATASETS = ["v_cost_centers", "v_cost_center_hierarchy", "v_accounts", "v_gl_actuals_natural", "v_sales_actuals"]
# Sales at plan grain (product group): the same query text works in both dialects.
PLAN_GRAIN = """
    SELECT s.period, s.entity_code, s.country_code, p.group_code, s.measure, sum(s.value) AS value
    FROM {s} s JOIN {p} p ON p.product_code = s.product_code
    GROUP BY 1, 2, 3, 4, 5"""


def table(batches) -> pa.Table:  # type: ignore[no-untyped-def]
    return pa.Table.from_batches(list(batches))


def same(a: pa.Table, b: pa.Table) -> bool:
    keys = [(n, "ascending") for n in a.schema.names]
    return a.sort_by(keys).equals(b.sort_by(keys))


def main() -> None:
    pg, pq_ = PostgresAdapter(PG), ParquetAdapter(PARQUET)
    out: list[tuple[str, str]] = []
    for a in (pg, pq_):
        a.test_connection()

    for name in DATASETS:
        # The contract schema: here the generator's declared types (the Parquet export).
        contract = pq_.schema(Relation(name))
        t1 = table(pg.read(Relation(name, "gold"), cast=contract))
        t2 = table(pq_.read(Relation(name), cast=contract))
        assert t1.schema == t2.schema, f"{name}: {t1.schema} vs {t2.schema}"
        assert same(t1, t2), f"{name}: contents differ"
        out.append((f"read {name}", f"{t1.num_rows:,} rows, same schema and rows"))
    inferred = pg.schema(Relation("v_gl_actuals_natural", "gold")).field("amount").type
    out.append(("amount as inferred from the view / contract", f"{inferred} / decimal128(18, 2)"))
    try:
        table(pg.read(Query("SELECT 1.005::numeric AS amount"), cast=pa.schema([("amount", pa.decimal128(18, 2))])))
        raise AssertionError("a lossy cast must fail")
    except pa.ArrowInvalid:
        out.append(("value with more decimals than the contract", "refused"))

    q1 = table(pg.read(Query(PLAN_GRAIN.format(s="gold.v_sales_actuals", p="gold.v_products"))))
    q2 = table(pq_.read(Query(PLAN_GRAIN.format(s="v_sales_actuals", p="v_products"))))
    # DuckDB widens decimal sums; cast to the Postgres result schema before comparing.
    assert same(q1.cast(q2.schema), q2), "plan-grain query differs"
    out.append(("admin query at plan grain (both dialects)", f"{q1.num_rows:,} rows, same"))
    out.append(("  result types differ by dialect", f"pg {q1.schema.field('value').type} / duckdb {q2.schema.field('value').type}"))

    # Write back: publish a version twice; the second run replaces, it does not add.
    plan = pa.table({
        "version_id": pa.array([2] * 3, pa.int16()),
        "period": pa.array(["2027-01-01", "2027-02-01", "2027-03-01"], pa.string()).cast(pa.date32()),
        "cost_center_code": ["DE1010", "DE1010", "DE1010"],
        "account_code": ["6000", "6000", "6000"],
        "amount": pa.array([Decimal("100.00"), Decimal("0.00"), Decimal("-5.50")], pa.decimal128(18, 2)),
    })  # fmt: skip
    with pg.engine.begin() as conn:
        conn.execute(text("""
            DROP SCHEMA IF EXISTS s05_out CASCADE; CREATE SCHEMA s05_out;
            CREATE TABLE s05_out.plan (version_id smallint, period date, cost_center_code text,
                                       account_code text, amount numeric(18,2));
            INSERT INTO s05_out.plan VALUES (1, '2026-01-01', 'X', 'Y', 1)"""))
    for _ in range(2):
        pg.replace(Relation("plan", "s05_out"), plan, {"version_id": 2})
    back = table(pg.read(Query("SELECT * FROM s05_out.plan WHERE version_id = 2")))
    assert same(back, plan), "postgres write-back differs"
    total = table(pg.read(Query("SELECT count(*)::int AS n FROM s05_out.plan"))).column("n")[0].as_py()
    assert total == 4, "replace touched another partition or duplicated rows"
    out.append(("postgres replace x2 (other partition kept)", "idempotent, zero kept as 0.00"))

    tmp = PARQUET / "_s05_plan"
    shutil.rmtree(tmp, ignore_errors=True)
    for _ in range(2):
        pq_.replace(Relation("_s05_plan"), plan, {"version_id": 2})
    assert same(table(ParquetAdapter(PARQUET).read(Relation("_s05_plan"))), plan)
    shutil.rmtree(tmp)
    out.append(("parquet replace x2", "idempotent"))

    # Throughput on the large dataset.
    for label, source in (
        ("large GL actuals (row level)", Relation("v_gl_actuals_natural", "large_gold")),
        ("large sales at plan grain", Query(PLAN_GRAIN.format(s="large_gold.v_sales_actuals", p="large_gold.v_products"))),
    ):
        start = time.perf_counter()
        n = sum(b.num_rows for b in pg.read(source))
        s = time.perf_counter() - start
        out.append((f"read {label}", f"{n:,} rows in {s:.1f} s ({n / s:,.0f} rows/s)"))

    width = max(len(k) for k, _ in out)
    for k, v in out:
        print(f"{k:<{width}}  {v}")


if __name__ == "__main__":
    main()
