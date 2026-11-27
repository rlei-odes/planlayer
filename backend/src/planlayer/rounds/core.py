"""Rounds themselves (ADR 0007, ADR 0008): finding one, its fiscal periods and closed months,
the valid cost centers of a year, the latest actuals, and the round's status, deadline and
planned cost centers."""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Literal

from sqlalchemy import (
    Connection,
    Engine,
    func,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert

from planlayer.access import User
from planlayer.domain.periods import (
    closed_months,
    fiscal_year_periods,
)
from planlayer.domain.workflow import RoundStatus, round_transition
from planlayer.store.schema import (
    ACCOUNT,
    AMOUNT,
    COST_CENTER,
    H_ACCOUNT,
    H_COST_CENTER,
    MODULE_COST_CENTER,
    account_set_account,
    account_set_assignment,
    closure,
    fact_cost_center,
    installation,
    member,
    planning_round,
    round_member,
    version,
    workflow_status,
)
from planlayer.store.uow import system_rights, unit_of_work

ComparisonKind = Literal["prior_year_actuals", "actuals", "version", "projection"]


Grain = Literal["month", "quarter"]  # how a round is planned (ADR 0015)


GRAINS: tuple[Grain, ...] = ("month", "quarter")


class NotFoundError(LookupError):
    pass


class RoundError(ValueError):
    """A setup change that can't be made as asked; nothing was changed."""


@dataclass
class RoundRef:
    version_id: int
    code: str
    name: str
    kind: str
    fiscal_year: int
    status: RoundStatus
    deadline: date | None
    grain: Grain = "month"
    # A forecast's last closed month (ADR 0015); None: no month is closed.
    actuals_through: date | None = None


_ROUNDS = select(
    version.c.version_id,
    version.c.code,
    version.c.name,
    version.c.kind,
    planning_round.c.fiscal_year,
    planning_round.c.status,
    planning_round.c.deadline,
    planning_round.c.grain,
    planning_round.c.actuals_through,
).join(planning_round, planning_round.c.version_id == version.c.version_id)


def get_round(
    conn: Connection, code: str, lock: Literal["share", "update"] | None = None
) -> RoundRef:
    query = _ROUNDS.where(version.c.code == code)
    if lock is not None:
        query = query.with_for_update(read=lock == "share", of=planning_round)
    row = conn.execute(query).first()
    if row is None:
        raise NotFoundError(f"planning round {code} not found")
    return RoundRef(**row._mapping)


def fiscal_periods(conn: Connection, fiscal_year: int) -> list[date]:
    start = conn.execute(select(installation.c.fiscal_year_start_month)).scalar_one()
    return fiscal_year_periods(fiscal_year, start)


def valid_cost_centers(conn: Connection, fiscal_year: int) -> list[int]:
    """Leaf cost centers valid at some point of the fiscal year."""
    periods = fiscal_periods(conn, fiscal_year)
    return list(
        conn.execute(
            select(member.c.member_id).where(
                member.c.dim_id == COST_CENTER,
                member.c.is_group.is_(False),
                member.c.valid_from.is_(None) | (member.c.valid_from <= periods[-1]),
                member.c.valid_to.is_(None) | (member.c.valid_to >= periods[0]),
            )
        ).scalars()
    )


def _member_id(conn: Connection, dim_id: int, code: str, *, leaf: bool | None = None) -> int:
    query = select(member.c.member_id).where(member.c.dim_id == dim_id, member.c.code == code)
    if leaf is not None:
        query = query.where(member.c.is_group.is_(not leaf))
    found = conn.execute(query).scalar()
    if found is None:
        raise NotFoundError(f"{'account' if dim_id == ACCOUNT else 'member'} {code} not found")
    return int(found)


def _version_id(conn: Connection, code: str) -> int:
    found = conn.execute(select(version.c.version_id).where(version.c.code == code)).scalar()
    if found is None:
        raise NotFoundError(f"version {code} not found")
    return int(found)


def closed_periods(conn: Connection, r: RoundRef) -> list[date]:
    """The round's closed months (ADR 0015): a forecast's months up to its actuals through."""
    periods = fiscal_periods(conn, r.fiscal_year)
    return periods[: closed_months(periods, r.actuals_through)]


def _check_periods(
    conn: Connection, kind: str, fiscal_year: int, grain: str, actuals_through: date | None
) -> None:
    """A round's grain and closed months as ADR 0015 allows them."""
    if grain not in GRAINS:
        raise RoundError(f"unknown grain {grain}")
    if actuals_through is None:
        return
    if kind != "forecast":
        raise RoundError("only a forecast closes months")
    periods = fiscal_periods(conn, fiscal_year)
    if actuals_through not in periods[:-1]:
        raise RoundError(
            "actuals through must be a month of the fiscal year before its last, "
            "as the first day of the month"
        )
    if grain == "quarter" and closed_months(periods, actuals_through) % 3:
        raise RoundError(
            "a forecast planned by quarter closes whole quarters; plan it by month to close "
            f"{closed_months(periods, actuals_through)} months"
        )


def start_month(engine: Engine) -> int:
    """The installation's first fiscal month (T-18)."""
    with engine.connect() as conn:
        return int(conn.execute(select(installation.c.fiscal_year_start_month)).scalar_one())


def latest_actuals(engine: Engine, user: User) -> date | None:
    """The latest month holding synced actuals the user may read: what a forecast proposes as
    its actuals through (ADR 0015)."""
    f = fact_cost_center
    with unit_of_work(engine, user) as conn:
        found: date | None = conn.execute(
            select(func.max(f.c.period))
            .join(version, version.c.version_id == f.c.version_id)
            .where(version.c.kind == "actuals", f.c.measure_id == AMOUNT)
        ).scalar()
    return found


def account_scopes(
    conn: Connection, version_id: int, cost_centers: Iterable[int]
) -> dict[int, set[int] | None]:
    """Per cost center, the leaf accounts of its account set: the nearest assignment on it or a
    node above it (ADR 0014). None: no set, every account."""
    ids = list(cost_centers)
    if not ids:
        return {}
    a = account_set_assignment
    nearest: dict[int, tuple[int, int]] = {}
    for cc_id, depth, set_id in conn.execute(
        select(closure.c.descendant_id, closure.c.depth, a.c.set_id)
        .join(a, a.c.member_id == closure.c.ancestor_id)
        .where(
            closure.c.hier_id == H_COST_CENTER,
            closure.c.descendant_id.in_(ids),
            a.c.version_id == version_id,
            a.c.module == MODULE_COST_CENTER,
        )
    ):
        if cc_id not in nearest or depth < nearest[cc_id][0]:
            nearest[cc_id] = (depth, set_id)
    leaves: dict[int, set[int]] = defaultdict(set)
    used = {set_id for _, set_id in nearest.values()}
    if used:
        for set_id, account_id in conn.execute(
            select(account_set_account.c.set_id, closure.c.descendant_id)
            .join(closure, closure.c.ancestor_id == account_set_account.c.account_id)
            .join(member, member.c.member_id == closure.c.descendant_id)
            .where(
                closure.c.hier_id == H_ACCOUNT,
                account_set_account.c.set_id.in_(used),
                member.c.is_group.is_(False),
            )
        ):
            leaves[set_id].add(account_id)
    return {cc: (leaves[nearest[cc][1]] if cc in nearest else None) for cc in ids}


def not_open(status: str) -> str:
    """Why a round that isn't open takes no plan values, in the planner's terms."""
    match status:
        case "setup":
            return (
                "the round is still being set up; planners plan once the controller opens it "
                "on the Status page"
            )
        case "closed":
            return "the round is closed"
    return f"the planning round is {status}"


def set_round_status(
    engine: Engine, actor: User, code: str, status: RoundStatus, *, unapproved_ok: bool = False
) -> None:
    """Open, close or reopen a round, or take an open one back to setup. Closing with cost
    centers not approved needs ``unapproved_ok``; back to setup only while none has started."""
    actor.require_controller()
    with unit_of_work(engine, actor) as conn:
        r = get_round(conn, code, lock="update")
        round_transition(r.status, status)
        # Round-wide questions: every cost center, whatever the controller's own rights reach.
        if (r.status, status) == ("open", "setup"):
            with system_rights(conn, actor=actor.login):
                started = _codes(conn, r.version_id, workflow_status.c.status != "not_started")
            if started:
                raise RoundError(
                    f"{len(started)} cost center(s) are already being planned "
                    f"({_listed(started)}); a round goes back to setup only before anyone starts"
                )
        if status == "closed":
            # No total over fewer people than the minimum stays in a closed round (ADR 0022).
            # Imported here: personnel costs build on rounds.
            from planlayer.personnel import small_groups

            small = small_groups(conn, r.version_id)
            if small:
                raise RoundError(
                    f"{len(small)} cost center(s) have personnel costs of fewer people than the "
                    "minimum; HR charges them to other cost centers first"
                )
            if not unapproved_ok:
                with system_rights(conn, actor=actor.login):
                    enabled = set(
                        conn.execute(
                            select(member.c.code)
                            .join(round_member, round_member.c.member_id == member.c.member_id)
                            .where(
                                round_member.c.version_id == r.version_id,
                                round_member.c.module == MODULE_COST_CENTER,
                                round_member.c.enabled.is_(True),
                            )
                        ).scalars()
                    )
                    approved = set(
                        _codes(conn, r.version_id, workflow_status.c.status == "approved")
                    )
                open_ones = sorted(enabled - approved)
                if open_ones:
                    raise UnapprovedError(open_ones)
        conn.execute(
            update(planning_round)
            .where(planning_round.c.version_id == r.version_id)
            .values(status=status)
        )


class UnapprovedError(RoundError):
    """Closing a round with cost centers not approved, without saying so is intended."""

    def __init__(self, codes: list[str]) -> None:
        self.codes = codes
        super().__init__(
            f"{len(codes)} cost center(s) aren't approved ({_listed(codes)}); close anyway to "
            "end the round with them as they are"
        )


def _codes(conn: Connection, version_id: int, condition: object) -> list[str]:
    return list(
        conn.execute(
            select(member.c.code)
            .join(workflow_status, workflow_status.c.cost_center_id == member.c.member_id)
            .where(workflow_status.c.version_id == version_id, condition)  # type: ignore[arg-type]
            .order_by(member.c.code)
        ).scalars()
    )


def _listed(codes: list[str], most: int = 5) -> str:
    return ", ".join(codes[:most]) + (f" and {len(codes) - most} more" if len(codes) > most else "")


def set_deadline(engine: Engine, actor: User, code: str, deadline: date | None) -> None:
    actor.require_controller()
    with unit_of_work(engine, actor) as conn:
        r = get_round(conn, code, lock="update")
        conn.execute(
            update(planning_round)
            .where(planning_round.c.version_id == r.version_id)
            .values(deadline=deadline)
        )


def set_member_enabled(engine: Engine, actor: User, code: str, cc_code: str, enabled: bool) -> None:
    actor.require_controller()
    with unit_of_work(engine, actor) as conn:
        r = get_round(conn, code)
        cc_id = _member_id(conn, COST_CENTER, cc_code, leaf=True)
        stmt = pg_insert(round_member).values(
            version_id=r.version_id, module=MODULE_COST_CENTER, member_id=cc_id, enabled=enabled
        )
        conn.execute(
            stmt.on_conflict_do_update(
                index_elements=list(round_member.primary_key.columns),
                set_={"enabled": stmt.excluded.enabled},
            )
        )


def rounds(engine: Engine) -> list[RoundRef]:
    with engine.connect() as conn:
        rows = conn.execute(
            _ROUNDS.order_by(planning_round.c.fiscal_year.desc(), version.c.code)
        ).all()
    return [RoundRef(**r._mapping) for r in rows]
