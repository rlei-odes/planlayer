"""Capacity in a round's life (ADR 0016): carrying planned hours into a new round."""

from collections.abc import Iterable

from sqlalchemy import Connection, select

from planlayer.capacity.common import _plain_context
from planlayer.capacity.credits import _write_credits
from planlayer.rounds import RoundRef, fiscal_periods
from planlayer.store.bulk import insert_all
from planlayer.store.schema import (
    BASE_LAYER,
    HOURS,
    fact_capacity,
)


def carry_hours(
    conn: Connection, source: RoundRef, target: RoundRef, cost_centers: Iterable[int]
) -> int:
    """A new round's planned hours from the round it is set up like (ADR 0016): month by month
    into the new year, without uplift, for the given productive cost centers, into the target's
    open months only. Credits are recomputed from them and the target's plan rates. Returns the
    number of hour cells written. Runs in the round's creating transaction and change batch."""
    ids = set(cost_centers)
    if not ids:
        return 0
    old, new = fiscal_periods(conn, source.fiscal_year), fiscal_periods(conn, target.fiscal_year)
    moved = {
        o: n
        for o, n in zip(old, new, strict=True)
        if target.actuals_through is None or n > target.actuals_through
    }
    f = fact_capacity
    rows = [
        {
            "version_id": target.version_id,
            "layer_id": BASE_LAYER,
            "measure_id": HOURS,
            "period": moved[r.period],
            "dim_entity": r.dim_entity,
            "dim_cost_center": r.dim_cost_center,
            "dim_work_center": r.dim_work_center,
            "dim_activity_type": r.dim_activity_type,
            "amount": r.amount,
        }
        for r in conn.execute(
            select(f).where(
                f.c.version_id == source.version_id,
                f.c.layer_id == BASE_LAYER,
                f.c.measure_id == HOURS,
                f.c.period.in_(list(moved)),
                f.c.dim_cost_center.in_(ids),
            )
        )
    ]
    insert_all(conn, f, rows)
    for cc_id in sorted({r["dim_cost_center"] for r in rows}):
        _write_credits(conn, _plain_context(conn, target, cc_id))
    return len(rows)
