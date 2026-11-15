"""Workforce in a round's life (ADR 0020): its mode, and carrying it into a new round."""

from collections.abc import Iterable
from typing import cast

from sqlalchemy import (
    Connection,
    delete,
    func,
    insert,
    literal,
    select,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert

from planlayer.domain.workforce import (
    MODES,
    Mode,
    measures,
)
from planlayer.personnel.common import module_on
from planlayer.rounds import RoundError, RoundRef, fiscal_periods
from planlayer.store.bulk import insert_all
from planlayer.store.schema import (
    BASE_LAYER,
    MODULE_COST_CENTER,
    MODULE_WORKFORCE,
    fact_workforce,
    round_member,
    round_module,
    workforce_line,
    workforce_position,
)
from planlayer.workforce.common import MEASURE_ID, _plain_context, mode_of
from planlayer.workforce.positions import write_rollup


def has_workforce(conn: Connection, version_id: int) -> bool:
    """Whether the round holds any workforce values, lines or positions."""
    found = 0
    for table, column in (
        (fact_workforce, fact_workforce.c.version_id),
        (workforce_line, workforce_line.c.version_id),
        (workforce_position, workforce_position.c.version_id),
    ):
        found += conn.execute(
            select(func.count()).select_from(table).where(column == version_id)
        ).scalar_one()
    return found > 0


def set_mode(conn: Connection, r: RoundRef, mode: Mode | None) -> None:
    """Turn workforce planning on with a mode, change the mode, or turn it off (``None``). A
    change clears the round's workforce in setup; an open round with any is refused."""
    if mode is not None and mode not in MODES:
        raise RoundError(f"unknown workforce mode {mode}")
    now = mode_of(conn, r.version_id)
    if now == mode:
        return
    if mode != "positions" and module_on(conn, r.version_id):
        raise RoundError("personnel costs need positions: turn them off first")
    if now is not None and has_workforce(conn, r.version_id):
        if r.status != "setup":
            raise RoundError(
                "the round has workforce values or positions; its workforce mode can change "
                "only in setup, where they are cleared"
            )
        for table, column in (
            (fact_workforce, fact_workforce.c.version_id),
            (workforce_line, workforce_line.c.version_id),
            (workforce_position, workforce_position.c.version_id),
        ):
            conn.execute(delete(table).where(column == r.version_id))
    if mode is None:
        conn.execute(
            delete(round_module).where(
                round_module.c.version_id == r.version_id,
                round_module.c.module == MODULE_WORKFORCE,
            )
        )
        return
    stmt = pg_insert(round_module).values(
        version_id=r.version_id, module=MODULE_WORKFORCE, mode=mode
    )
    conn.execute(
        stmt.on_conflict_do_update(
            index_elements=[round_module.c.version_id, round_module.c.module],
            set_={"mode": stmt.excluded.mode},
        )
    )


def carry(
    conn: Connection, source: RoundRef, target: RoundRef, planned: Iterable[int], values: bool
) -> int:
    """A new round's workforce from the round it is set up like (ADR 0020): the lines added to
    sheets; positions that haven't left before the new year, rolled up again; with ``values``,
    typed FTE and headcount month by month into the target's open months. Returns the number of
    positions and value cells copied. Runs in the round's creating transaction."""
    target_mode, source_mode = mode_of(conn, target.version_id), mode_of(conn, source.version_id)
    ids = set(planned)
    if target_mode is None or source_mode is None or not ids:
        return 0
    old, new = fiscal_periods(conn, source.fiscal_year), fiscal_periods(conn, target.fiscal_year)
    line_columns = [
        c for c in workforce_line.c if c.name not in ("version_id", "changed_by", "changed_at")
    ]
    conn.execute(
        insert(workforce_line).from_select(
            ["version_id", *(c.name for c in line_columns)],
            select(
                literal(target.version_id, workforce_line.c.version_id.type), *line_columns
            ).where(
                workforce_line.c.version_id == source.version_id,
                workforce_line.c.cost_center_id.in_(ids),
            ),
        )
    )
    copied = 0
    if target_mode == "positions" and source_mode == "positions":
        kept = [
            c
            for c in workforce_position.c
            if c.name not in ("position_id", "version_id", "changed_at")
        ]
        rows = [
            {"version_id": target.version_id, **{c.name: r._mapping[c.name] for c in kept}}
            for r in conn.execute(
                select(*kept).where(
                    workforce_position.c.version_id == source.version_id,
                    workforce_position.c.cost_center_id.in_(ids),
                    workforce_position.c.leave_date.is_(None)
                    | (workforce_position.c.leave_date >= new[0]),
                )
            )
        ]
        insert_all(conn, workforce_position, rows)
        for cc_id in sorted({r["cost_center_id"] for r in rows}):
            write_rollup(conn, _plain_context(conn, target, "positions", cast(int, cc_id)))
        copied += len(rows)
    elif values and target_mode != "positions" and source_mode != "positions":
        wanted = [MEASURE_ID[m] for m in measures(target_mode)]
        moved = {
            o: n
            for o, n in zip(old, new, strict=True)
            if target.actuals_through is None or n > target.actuals_through
        }
        f = fact_workforce
        rows = [
            {
                "version_id": target.version_id,
                "layer_id": BASE_LAYER,
                "measure_id": r.measure_id,
                "period": moved[r.period],
                "dim_entity": r.dim_entity,
                "dim_cost_center": r.dim_cost_center,
                "dim_staff_type": r.dim_staff_type,
                "dim_staff_category": r.dim_staff_category,
                "amount": r.amount,
            }
            for r in conn.execute(
                select(f).where(
                    f.c.version_id == source.version_id,
                    f.c.layer_id == BASE_LAYER,
                    f.c.measure_id.in_(wanted),
                    f.c.period.in_(list(moved)),
                    f.c.dim_cost_center.in_(ids),
                )
            )
        ]
        insert_all(conn, f, rows)
        copied += len(rows)
    return copied


def planned_cost_centers(conn: Connection, version_id: int) -> set[int]:
    return set(
        conn.execute(
            select(round_member.c.member_id).where(
                round_member.c.version_id == version_id,
                round_member.c.module == MODULE_COST_CENTER,
                round_member.c.enabled.is_(True),
            )
        ).scalars()
    )
