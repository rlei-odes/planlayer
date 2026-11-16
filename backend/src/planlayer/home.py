"""The start page (ADR 0021): what is waiting for the user, and what they plan.

- **Tasks:** transfers of positions proposed to a cost center they plan, and the open note
  threads assigned to them (ADR 0017).
- **Planning:** per open round, the cost centers they plan as owner, deputy or capacity planner,
  with their status and the sheets to fill in; the cost centers waiting for their approval; and
  for controllers and viewers, the round's statuses at a glance.
- **HR** (ADR 0022): per open round, the small groups to charge elsewhere and the submitted cost
  centers whose pay waits for HR's review.
"""

from dataclasses import dataclass, field
from datetime import date
from typing import Literal, cast

from sqlalchemy import Engine, select

from planlayer.access import (
    User,
    approvers,
    capacity_rights,
    cost_center_rights,
    pay_cost_centers,
    plans,
)
from planlayer.capacity import productive
from planlayer.domain.workflow import STATUSES, Status, is_overdue
from planlayer.personnel import module_on, needs_review, small_groups
from planlayer.personnel.rights import listed, released
from planlayer.rounds import RoundRef, rounds
from planlayer.store.schema import member, pay_handover, workflow_status, workforce_position
from planlayer.store.uow import unit_of_work
from planlayer.workflow import enabled_members
from planlayer.workforce import mode_of

Sheet = Literal["capacity", "workforce", "personnel", "planning"]


@dataclass
class MyCostCenter:
    code: str
    name: str
    status: Status
    overdue: bool
    sheets: list[Sheet]  # in the usual order of work


@dataclass
class RoundWork:
    round: RoundRef
    cost_centers: list[MyCostCenter] = field(default_factory=list)  # the user plans them
    to_approve: int = 0  # submitted, and the user approves them
    # Controllers and viewers: every planned cost center of the round, per status.
    counts: dict[Status, int] | None = None
    # HR: cost centers with small groups, and submitted ones waiting for HR's review.
    small_groups: list[str] = field(default_factory=list)
    to_review: list[str] = field(default_factory=list)
    # Personnel costs in the round: whether HR has released pay to its readers (ADR 0023).
    pay_released: bool | None = None
    # HR's next step in the round (ADR 0024): release access, then hand pay over per cost center.
    pay_step: Literal["release", "hand_over"] | None = None
    to_hand_over: list[str] = field(default_factory=list)
    # Capex (ADR 0028): open investments and large projects the user confirms.
    to_confirm: int = 0


def my_work(engine: Engine, user: User, today: date) -> list[RoundWork]:
    out: list[RoundWork] = []
    for r in rounds(engine):
        if r.status != "open":
            continue
        with unit_of_work(engine, user) as conn:
            enabled = sorted(m for m, on in enabled_members(conn, r.version_id).items() if on)
            rights = cost_center_rights(conn, user)
            readable = [m for m in enabled if m in rights]
            if not readable and not (user.is_controller or user.is_hr):
                continue
            statuses = {
                cc: cast(Status, s)
                for cc, s in conn.execute(
                    select(workflow_status.c.cost_center_id, workflow_status.c.status).where(
                        workflow_status.c.version_id == r.version_id
                    )
                )
            }
            mine = plans(conn, user, readable)
            # Capacity planners plan capacity only; a controller writes every cost center's but
            # plans none of them as a task of their own.
            capacity = (
                {cc for cc, w in capacity_rights(conn, user).items() if w}
                & productive(conn, r.version_id)
                & set(enabled)
            )
            workforce = mode_of(conn, r.version_id) is not None
            personnel = module_on(conn, r.version_id)
            pay = pay_cost_centers(conn, user, r.version_id) if personnel else set()
            # Listed by HR but not released yet: the sheet shows, inactive (ADR 0023).
            pay_listed = listed(conn, user, r.version_id) if personnel else set()
            pay_released = released(conn, r.version_id) if personnel else None
            shown = sorted(mine | (set() if user.is_controller else capacity))
            names = {
                m.member_id: m
                for m in conn.execute(
                    select(member.c.member_id, member.c.code, member.c.name).where(
                        member.c.member_id.in_(shown)
                    )
                )
            }
            submitted = [m for m in readable if statuses.get(m) == "submitted" and m not in mine]
            assigned = approvers(conn, submitted)
            small: list[str] = []
            review: list[str] = []
            step: Literal["release", "hand_over"] | None = None
            hand_over: list[str] = []
            if user.is_hr and pay:
                waiting = [m for m in enabled if statuses.get(m) == "submitted"]
                codes = dict(
                    conn.execute(
                        select(member.c.member_id, member.c.code).where(
                            member.c.member_id.in_(enabled)
                        )
                    ).all()
                )
                small = sorted(codes[m] for m in small_groups(conn, r.version_id) if m in codes)
                review = sorted(codes[m] for m in needs_review(conn, r.version_id, waiting))
                if not pay_released:
                    step = "release"
                else:
                    handed = set(
                        conn.execute(
                            select(pay_handover.c.cost_center_id).where(
                                pay_handover.c.version_id == r.version_id
                            )
                        ).scalars()
                    )
                    with_positions = set(
                        conn.execute(
                            select(workforce_position.c.cost_center_id).where(
                                workforce_position.c.version_id == r.version_id
                            )
                        ).scalars()
                    )
                    hand_over = sorted(
                        codes[m] for m in enabled if m in with_positions and m not in handed
                    )
                    step = "hand_over" if hand_over else None

            # Imported here: capex planning builds on this module's imports.
            from planlayer.capex.items import waiting_for

            confirm = waiting_for(conn, user, r.version_id)

        work = RoundWork(
            r,
            to_confirm=confirm,
            small_groups=small,
            to_review=review,
            pay_released=pay_released,
            pay_step=step,
            to_hand_over=hand_over,
        )
        for cc in sorted(shown, key=lambda m: names[m].code):
            status = statuses.get(cc, "not_started")
            sheets: list[Sheet] = []
            if cc in capacity:
                sheets.append("capacity")
            if cc in mine:
                if workforce:
                    sheets.append("workforce")
                if cc in pay or cc in pay_listed:
                    sheets.append("personnel")
                sheets.append("planning")
            work.cost_centers.append(
                MyCostCenter(
                    names[cc].code,
                    names[cc].name,
                    status,
                    is_overdue(status, r.deadline, today),
                    sheets,
                )
            )
        work.to_approve = sum(
            1
            for cc in submitted
            if user.is_controller or user.user_id in {u for u, _ in assigned.get(cc, [])}
        )
        if user.is_controller or user.is_viewer:
            counts = dict.fromkeys(STATUSES, 0)
            for cc in readable:
                counts[statuses.get(cc, "not_started")] += 1
            work.counts = counts
        out.append(work)
    return out
