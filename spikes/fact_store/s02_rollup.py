# /// script
# requires-python = ">=3.13"
# dependencies = ["psycopg[binary]==3.3.8"]
# ///
"""S-02: hierarchy roll-up by closure table vs recursive CTE (D-02).

Correctness per slice (node x account x period), against sums computed in Python from leaf
values and the parent map. Covers the ragged cost center tree, the alternate function
hierarchy and the account tree. Run load.py first.
"""

import time
from collections import defaultdict
from decimal import Decimal

from common import H_ACCOUNT, H_CC, H_CC_FUNCTION, connect, report, timed

YEAR = ("2025-01-01", "2026-01-01")

CLOSURE = """
    SELECT c.ancestor_id, f.{other}, f.period, sum(f.amount)
    FROM spike.fact_wide f
    JOIN spike.closure c ON c.hier_id = %(hier)s AND c.descendant_id = f.{dim}
    WHERE f.version_id = 1 AND f.measure_id = 1 AND f.cost_center_id IS NOT NULL
      AND f.period >= %(start)s AND f.period < %(end)s
    GROUP BY 1, 2, 3"""

RECURSIVE = """
    WITH RECURSIVE anc (leaf, node) AS (
        SELECT member_id, member_id FROM spike.hier_edge WHERE hier_id = %(hier)s
        UNION ALL
        SELECT a.leaf, e.parent_id FROM anc a
        JOIN spike.hier_edge e ON e.hier_id = %(hier)s AND e.member_id = a.node
        WHERE e.parent_id IS NOT NULL)
    SELECT anc.node, f.{other}, f.period, sum(f.amount)
    FROM spike.fact_wide f JOIN anc ON anc.leaf = f.{dim}
    WHERE f.version_id = 1 AND f.measure_id = 1 AND f.cost_center_id IS NOT NULL
      AND f.period >= %(start)s AND f.period < %(end)s
    GROUP BY 1, 2, 3"""

LEAVES = """
    SELECT f.{dim}, f.{other}, f.period, sum(f.amount)
    FROM spike.fact_wide f
    WHERE f.version_id = 1 AND f.measure_id = 1 AND f.cost_center_id IS NOT NULL
      AND f.period >= %(start)s AND f.period < %(end)s
    GROUP BY 1, 2, 3"""

# One node, one row per period: what a grid header row needs.
NODE_CLOSURE = """
    SELECT f.period, sum(f.amount) FROM spike.fact_wide f
    JOIN spike.closure c ON c.hier_id = 1 AND c.descendant_id = f.cost_center_id
    WHERE c.ancestor_id = %(node)s AND f.version_id = 1 AND f.measure_id = 1
      AND f.period >= %(start)s AND f.period < %(end)s GROUP BY 1"""

NODE_RECURSIVE = """
    WITH RECURSIVE sub (member_id) AS (
        SELECT %(node)s::int
        UNION ALL
        SELECT e.member_id FROM spike.hier_edge e JOIN sub ON e.parent_id = sub.member_id
        WHERE e.hier_id = 1)
    SELECT f.period, sum(f.amount) FROM spike.fact_wide f JOIN sub ON sub.member_id = f.cost_center_id
    WHERE f.version_id = 1 AND f.measure_id = 1
      AND f.period >= %(start)s AND f.period < %(end)s GROUP BY 1"""


def golden(conn, hier: int, dim: str, other: str) -> dict[tuple, Decimal]:  # type: ignore[no-untyped-def]
    parent = dict(conn.execute(
        "SELECT member_id, parent_id FROM spike.hier_edge WHERE hier_id = %s", (hier,)))  # fmt: skip
    ancestors: dict[int, list[int]] = {}
    for member in parent:
        chain, node, seen = [], member, set()
        while node is not None:
            if node in seen:
                raise ValueError(f"cycle at {node}")
            seen.add(node)
            chain.append(node)
            node = parent.get(node)
        ancestors[member] = chain
    totals: dict[tuple, Decimal] = defaultdict(Decimal)
    sql = LEAVES.format(dim=dim, other=other)
    for leaf, key, period, amount in conn.execute(sql, {"start": YEAR[0], "end": YEAR[1]}):
        for node in ancestors[leaf]:
            totals[(node, key, period)] += amount
    return dict(totals)


def main() -> None:
    conn = connect()
    out: list[tuple[str, str]] = []
    params = {"start": YEAR[0], "end": YEAR[1]}
    roots: dict[int, Decimal] = {}

    cases = [
        ("cost centers x accounts (ragged)", H_CC, "cost_center_id", "account_id"),
        ("function hierarchy x accounts", H_CC_FUNCTION, "cost_center_id", "account_id"),
        ("accounts x cost centers", H_ACCOUNT, "account_id", "cost_center_id"),
    ]
    for label, hier, dim, other in cases:
        p = {**params, "hier": hier}
        start = time.perf_counter()
        expected = golden(conn, hier, dim, other)
        py_ms = (time.perf_counter() - start) * 1000
        c_ms, closure = timed(lambda: conn.execute(CLOSURE.format(dim=dim, other=other), p).fetchall(), runs=3)
        r_ms, recursive = timed(lambda: conn.execute(RECURSIVE.format(dim=dim, other=other), p).fetchall(), runs=3)
        assert {(a, b, c): d for a, b, c, d in closure} == expected, f"{label}: closure differs"
        assert {(a, b, c): d for a, b, c, d in recursive} == expected, f"{label}: recursive differs"
        out.append((f"{label}: {len(expected):,} slices", "match"))
        out.append(("  closure table", f"{c_ms:8.1f} ms"))
        out.append(("  recursive CTE", f"{r_ms:8.1f} ms"))
        out.append(("  Python from leaves", f"{py_ms:8.1f} ms"))
        root = conn.execute(
            "SELECT member_id FROM spike.hier_edge WHERE hier_id = %s AND parent_id IS NULL", (hier,)
        ).fetchone()[0]
        roots[hier] = sum(v for (n, _, _), v in expected.items() if n == root)

    assert roots[H_CC] == roots[H_CC_FUNCTION] == roots[H_ACCOUNT], "root totals differ"
    out.append(("root totals of all three hierarchies", f"equal ({roots[H_CC]:,})"))

    node = conn.execute(
        "SELECT member_id FROM spike.member WHERE dim_id = 2 AND code = 'DIV_PUMPS'"
    ).fetchone()[0]
    p = {**params, "node": node}
    c_ms, a = timed(lambda: conn.execute(NODE_CLOSURE, p).fetchall())
    r_ms, b = timed(lambda: conn.execute(NODE_RECURSIVE, p).fetchall())
    assert sorted(a) == sorted(b)
    out.append(("one node x 12 months: closure", f"{c_ms:8.1f} ms"))
    out.append(("one node x 12 months: recursive CTE", f"{r_ms:8.1f} ms"))

    depth = conn.execute(
        "SELECT hier_id, max(depth), count(*) FROM spike.closure GROUP BY 1 ORDER BY 1").fetchall()  # fmt: skip
    out.append(("closure depth / rows per hierarchy", ", ".join(f"h{h}: {d}/{n:,}" for h, d, n in depth)))
    start = time.perf_counter()
    conn.execute("""
        CREATE TEMP TABLE rebuild AS
        WITH RECURSIVE c AS (
            SELECT hier_id, member_id AS anc, member_id AS des, 0 AS depth FROM spike.hier_edge
            UNION ALL
            SELECT c.hier_id, e.parent_id, c.des, c.depth + 1
            FROM c JOIN spike.hier_edge e ON e.hier_id = c.hier_id AND e.member_id = c.anc
            WHERE e.parent_id IS NOT NULL)
        SELECT * FROM c""")
    out.append(("closure rebuild, all hierarchies", f"{(time.perf_counter() - start) * 1000:8.1f} ms"))
    report(out)


if __name__ == "__main__":
    main()
