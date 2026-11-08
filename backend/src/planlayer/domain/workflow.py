"""Approval workflow rules (ADR 0008): statuses per round x cost center, and round statuses.

Who may take an action (planner, approver, controller) is checked with the roles of D-21; these
rules only decide which transitions exist and what they need.
"""

from datetime import date
from typing import Literal, get_args

Status = Literal["not_started", "in_progress", "submitted", "approved"]
Action = Literal["submit", "approve", "reject", "reopen"]
RoundStatus = Literal["setup", "open", "closed"]

STATUSES: tuple[Status, ...] = get_args(Status)
ACTIONS: tuple[Action, ...] = get_args(Action)
EDITABLE: frozenset[Status] = frozenset({"not_started", "in_progress"})

_TRANSITIONS: dict[Action, tuple[frozenset[Status], Status]] = {
    "submit": (EDITABLE, "submitted"),
    "approve": (frozenset({"submitted"}), "approved"),
    "reject": (frozenset({"submitted"}), "in_progress"),
    "reopen": (frozenset({"approved"}), "in_progress"),
}
COMMENT_REQUIRED: frozenset[Action] = frozenset({"reject", "reopen"})

_ROUND_TRANSITIONS: frozenset[tuple[RoundStatus, RoundStatus]] = frozenset(
    {("setup", "open"), ("open", "closed"), ("closed", "open")}
)


class WorkflowError(ValueError):
    """A transition that doesn't exist, or lacks what it needs."""


def allows(action: Action, status: Status) -> bool:
    return status in _TRANSITIONS[action][0]


def transition(status: Status, action: Action, comment: str | None) -> Status:
    """The status after ``action``; a reject or reopen needs a comment."""
    sources, target = _TRANSITIONS[action]
    if status not in sources:
        raise WorkflowError(f"can't {action} a cost center that is {status.replace('_', ' ')}")
    if action in COMMENT_REQUIRED and not (comment and comment.strip()):
        raise WorkflowError(f"{action} needs a comment")
    return target


def check_not_own(action: Action, actor: str, owner: str | None) -> None:
    """A planner can't approve their own cost center."""
    if action == "approve" and owner is not None and actor == owner:
        raise WorkflowError("you can't approve your own cost center")


def round_transition(current: RoundStatus, target: RoundStatus) -> RoundStatus:
    if (current, target) not in _ROUND_TRANSITIONS:
        raise WorkflowError(f"a round can't go from {current} to {target}")
    return target


def is_overdue(status: Status, deadline: date | None, today: date) -> bool:
    """Past the deadline and not yet submitted."""
    return deadline is not None and today > deadline and status in EDITABLE
