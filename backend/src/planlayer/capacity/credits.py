"""The credits capacity gives the cost center: hours x plan rate per activity type and month,
on the activity type's account, recomputed with every change (ADR 0013)."""

from collections import defaultdict
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import Connection, and_, delete, select, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert

from planlayer.capacity.common import CENT, _Context, _hours
from planlayer.sheet import (
    MONTHS,
)
from planlayer.store.schema import (
    ACCOUNT,
    AMOUNT,
    BASE_LAYER,
    capacity_line,
    credit_account,
    fact_cost_center,
    member,
)


def _stored_credits(conn: Connection, ctx: _Context) -> dict[str, list[Decimal | None]]:
    """The credits stored on the cost center, per account code."""
    f = fact_cost_center
    index = {p: i for i, p in enumerate(ctx.periods)}
    out: dict[str, list[Decimal | None]] = defaultdict(lambda: [None] * MONTHS)
    for code, p, amount in conn.execute(
        select(member.c.code, f.c.period, f.c.amount)
        .join(member, member.c.member_id == f.c.dim_account)
        .where(
            f.c.version_id == ctx.version_id,
            f.c.layer_id == BASE_LAYER,
            f.c.measure_id == AMOUNT,
            f.c.dim_cost_center == ctx.cost_center_id,
            f.c.dim_account.in_(select(credit_account.c.account_id)),
            f.c.period.in_(ctx.periods),
        )
    ):
        out[code][index[p]] = amount
    return dict(out)


def _follow(conn: Connection, ctx: _Context) -> None:
    """New credits change what is left on the cost center, which its full forwardings pass on
    (ADR 0018). Imported here: forwarding builds on planning, which this module builds on."""
    from planlayer.forwarding import follow

    follow(conn, ctx.round, ctx.cost_center_id)


def _write_credits(conn: Connection, ctx: _Context) -> None:
    """Credits = hours x rate of the active lines, per activity type's account and month; the
    cost center's credit cells are replaced with them (blank where nothing is credited)."""
    rows = conn.execute(
        select(
            capacity_line.c.work_center_id,
            capacity_line.c.activity_type_id,
            capacity_line.c.rate,
            capacity_line.c.variable_rate,
            member.c.account_code,
            member.c.variable_account_code,
        )
        .join(member, member.c.member_id == capacity_line.c.activity_type_id)
        .where(
            capacity_line.c.version_id == ctx.version_id,
            capacity_line.c.cost_center_id == ctx.cost_center_id,
            capacity_line.c.active.is_(True),
        )
    ).all()
    codes = {c for r in rows for c in (r.account_code, r.variable_account_code) if c}
    account_ids = dict(
        conn.execute(
            select(member.c.code, member.c.member_id).where(
                member.c.dim_id == ACCOUNT, member.c.code.in_(codes)
            )
        ).all()
    )
    hours = _hours(conn, ctx.version_id, ctx.cost_center_id, ctx.periods)
    credit: dict[tuple[int, date], Decimal] = defaultdict(Decimal)
    for r in rows:
        cells = hours.get((r.work_center_id, r.activity_type_id))
        if cells is None:
            continue
        parts = ((r.account_code, r.rate), (r.variable_account_code, r.variable_rate))
        for code, rate in parts:
            if code is None or rate is None or code not in account_ids:
                continue
            for p, c in zip(ctx.periods, cells, strict=True):
                if c.value is not None:
                    credit[(account_ids[code], p)] += c.value * rate
    wanted = {
        k: v.quantize(CENT, ROUND_HALF_UP)
        for k, v in credit.items()
        if k[1] in ctx.periods[ctx.closed :]
    }

    f = fact_cost_center
    scope = and_(
        f.c.version_id == ctx.version_id,
        f.c.layer_id == BASE_LAYER,
        f.c.measure_id == AMOUNT,
        f.c.dim_cost_center == ctx.cost_center_id,
        f.c.period.in_(ctx.periods[ctx.closed :]),  # a forecast's closed months hold actuals
    )
    stored = {
        (a, p): v
        for a, p, v in conn.execute(
            select(f.c.dim_account, f.c.period, f.c.amount)
            .where(scope, f.c.dim_account.in_(select(credit_account.c.account_id)))
            .with_for_update()
        )
    }
    gone = [k for k in stored if k not in wanted]
    if gone:
        conn.execute(delete(f).where(scope, tuple_(f.c.dim_account, f.c.period).in_(gone)))
    changed = [
        {
            "version_id": ctx.version_id,
            "layer_id": BASE_LAYER,
            "measure_id": AMOUNT,
            "period": p,
            "dim_entity": ctx.entity_id,
            "dim_cost_center": ctx.cost_center_id,
            "dim_account": a,
            "amount": v,
        }
        for (a, p), v in wanted.items()
        if stored.get((a, p)) != v
    ]
    if changed:
        stmt = pg_insert(f).values(changed)
        conn.execute(
            stmt.on_conflict_do_update(
                index_elements=list(f.primary_key.columns),
                set_={"amount": stmt.excluded.amount, "row_version": f.c.row_version + 1},
            )
        )
