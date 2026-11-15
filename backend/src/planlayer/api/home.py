"""The start page's endpoint (ADR 0021): the user's tasks and the cost centers they plan."""

from datetime import date
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel

from planlayer import home, notes, workforce
from planlayer.api.auth import CurrentUser, engine_of
from planlayer.api.planning import Version, version_out
from planlayer.api.workforce import TransferOut, transfer_out
from planlayer.domain.workflow import Status

router = APIRouter(prefix="/api")


class MyCostCenterOut(BaseModel):
    code: str
    name: str
    status: Status
    overdue: bool
    sheets: list[Literal["capacity", "workforce", "personnel", "planning"]]


class RoundWorkOut(BaseModel):
    round: Version
    cost_centers: list[MyCostCenterOut]
    to_approve: int  # submitted cost centers the user approves
    counts: dict[Status, int] | None  # controllers and viewers: the round's statuses
    # HR (ADR 0022): cost centers with small groups, and submitted ones waiting for the review.
    small_groups: list[str]
    to_review: list[str]
    # Personnel costs in the round: whether HR released pay to its readers; null without them.
    pay_released: bool | None
    # HR's next step (ADR 0024): release access, then hand pay over; and what is left to hand.
    pay_step: Literal["release", "hand_over"] | None
    to_hand_over: list[str]


class HomeOut(BaseModel):
    # Open note tasks assigned to the user, transfers for them to decide, and rounds with cost
    # centers waiting for their approval: the start page's task list, one entry each.
    tasks: int
    transfers: list[TransferOut]
    rounds: list[RoundWorkOut]


@router.get("/home")
def start(request: Request, user: CurrentUser) -> HomeOut:
    engine = engine_of(request)
    transfers = workforce.my_transfers(engine, user)
    note_tasks = notes.my_notes(engine, user).tasks
    decide = sum(1 for t in transfers if t.status == "proposed" and t.can_decide)
    work = home.my_work(engine, user, date.today())
    return HomeOut(
        tasks=note_tasks
        + decide
        + sum(1 for w in work if w.to_approve)
        + sum(1 for w in work if w.small_groups or w.to_review)
        + sum(1 for w in work if w.pay_step),
        transfers=[transfer_out(t) for t in transfers],
        rounds=[
            RoundWorkOut(
                round=version_out(w.round),
                cost_centers=[
                    MyCostCenterOut(
                        code=c.code,
                        name=c.name,
                        status=c.status,
                        overdue=c.overdue,
                        sheets=c.sheets,
                    )
                    for c in w.cost_centers
                ],
                to_approve=w.to_approve,
                counts=w.counts,
                small_groups=w.small_groups,
                to_review=w.to_review,
                pay_released=w.pay_released,
                pay_step=w.pay_step,
                to_hand_over=w.to_hand_over,
            )
            for w in work
        ],
    )
