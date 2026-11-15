"""What the personnel cost modules share (ADR 0022): whether a round has the module, its rules,
the context a cost center's pay is read and written in, and the change batch every write runs
in."""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import Connection, Engine, Row, select
from sqlalchemy.exc import DBAPIError

from planlayer.access import User, pay_cost_centers
from planlayer.domain.periods import closed_months
from planlayer.domain.personnel import Award, Contribution, Extra, Raise, Rules, Step
from planlayer.domain.workflow import EDITABLE, Status
from planlayer.rounds import NotFoundError, RoundRef, fiscal_periods, get_round, not_open
from planlayer.sheet import LockedError, locked_error
from planlayer.store.schema import (
    COST_CENTER,
    MODULE_PERSONNEL,
    member,
    pay_account,
    pay_handover,
    pay_rule,
    pay_rule_step,
    pay_setting,
    pay_staff_type,
    round_module,
)
from planlayer.store.uow import unit_of_work
from planlayer.workflow import enabled_members, status_of

DEFAULT_MIN_GROUP = 3


def module_on(conn: Connection, version_id: int) -> bool:
    return (
        conn.execute(
            select(round_module.c.module).where(
                round_module.c.version_id == version_id,
                round_module.c.module == MODULE_PERSONNEL,
            )
        ).first()
        is not None
    )


def pay_accounts(conn: Connection, version_id: int) -> set[int]:
    return set(
        conn.execute(
            select(pay_account.c.account_id).where(pay_account.c.version_id == version_id)
        ).scalars()
    )


@dataclass(frozen=True)
class StaffTypePay:
    account_id: int
    typed: bool


@dataclass(frozen=True)
class RoundRules:
    rules: Rules
    staff_types: dict[int, StaffTypePay]
    min_group: int
    review_required: bool = False


def load_rules(conn: Connection, version_id: int) -> RoundRules:
    setting = conn.execute(
        select(pay_setting).where(pay_setting.c.version_id == version_id)
    ).first()
    staff_types = {
        r.staff_type_id: StaffTypePay(r.account_id, r.typed)
        for r in conn.execute(
            select(pay_staff_type).where(pay_staff_type.c.version_id == version_id)
        )
    }
    rows = list(conn.execute(select(pay_rule).where(pay_rule.c.version_id == version_id)))
    steps: dict[int, list[Step]] = {}
    for s in conn.execute(
        select(pay_rule_step)
        .where(pay_rule_step.c.rule_id.in_([r.rule_id for r in rows]))
        .order_by(pay_rule_step.c.years)
    ):
        steps.setdefault(s.rule_id, []).append(Step(s.years, s.salaries, s.amount))
    extras, raises, awards, contributions = [], [], [], []
    for r in rows:
        match r.kind:
            case "extra":
                extras.append(Extra(r.account_id, r.salaries, r.entity_id, r.rule_id))
            case "raise":
                raises.append(Raise(r.account_id, r.percent, r.starts, r.entity_id, r.rule_id))
            case "award":
                awards.append(
                    Award(r.account_id, tuple(steps.get(r.rule_id, [])), r.entity_id, r.rule_id)
                )
            case "contribution":
                contributions.append(
                    Contribution(r.account_id, r.percent, r.ceiling, r.entity_id, r.rule_id)
                )
    return RoundRules(
        Rules(
            tuple(extras),
            tuple(raises),
            tuple(awards),
            tuple(contributions),
            setting.bonus_account_id if setting else None,
        ),
        staff_types,
        setting.min_group if setting else DEFAULT_MIN_GROUP,
        setting.review_required if setting else False,
    )


@dataclass
class PayContext:
    round: RoundRef
    cost_center_id: int
    cost_center_code: str
    cost_center_name: str
    status: Status
    lock_reason: str | None
    periods: list[date]

    @property
    def version_id(self) -> int:
        return self.round.version_id

    @property
    def closed(self) -> int:
        return closed_months(self.periods, self.round.actuals_through)


def pay_round(conn: Connection, version_code: str) -> RoundRef:
    r = get_round(conn, version_code)
    if not module_on(conn, r.version_id):
        raise NotFoundError(f"{version_code} has no personnel costs")
    return r


def pay_context(conn: Connection, user: User, version_code: str, cc_code: str) -> PayContext:
    """A cost center whose pay the user reads; another is reported as not found (ADR 0022)."""
    r = pay_round(conn, version_code)
    cc = conn.execute(
        select(member).where(
            member.c.dim_id == COST_CENTER, member.c.code == cc_code, member.c.is_group.is_(False)
        )
    ).first()
    if cc is None or cc.member_id not in pay_cost_centers(conn, user, r.version_id):
        raise NotFoundError(f"cost center {cc_code} not found")
    status = status_of(conn, r.version_id, cc.member_id)
    if r.status != "open" and not (r.status == "setup" and (user.is_hr or user.is_controller)):
        reason: str | None = not_open(r.status)
    elif not enabled_members(conn, r.version_id).get(cc.member_id, False):
        reason = "the cost center is not enabled in this round"
    elif status not in EDITABLE:
        reason = f"the cost center is {status}"
    elif not user.is_hr and not _handed_over(conn, r.version_id, cc.member_id):
        reason = "HR hasn't handed this cost center's pay over yet"
    else:
        reason = None
    return PayContext(
        r, cc.member_id, cc.code, cc.name, status, reason, fiscal_periods(conn, r.fiscal_year)
    )


def _handed_over(conn: Connection, version_id: int, cc_id: int) -> bool:
    return (
        conn.execute(
            select(pay_handover.c.version_id).where(
                pay_handover.c.version_id == version_id, pay_handover.c.cost_center_id == cc_id
            )
        ).first()
        is not None
    )


def run(engine: Engine, actor: User, work: Callable[[Connection], None]) -> str:
    """Run ``work`` in one transaction under a new change batch; returns the batch id."""
    batch_id = uuid.uuid4().hex
    try:
        with unit_of_work(engine, actor, batch_id) as conn:
            work(conn)
    except DBAPIError as e:
        locked = locked_error(e)
        if locked is None:
            raise
        raise locked from None
    return batch_id


def editable(ctx: PayContext) -> None:
    if ctx.lock_reason is not None:
        raise LockedError(ctx.lock_reason)


def member_row(conn: Connection, dim: int, code: str) -> Row[Any] | None:
    return conn.execute(select(member).where(member.c.dim_id == dim, member.c.code == code)).first()
