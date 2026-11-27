"""Approval workflow rules (ADR 0008): statuses per round x cost center, and round statuses.

Who may take an action (planner, approver, controller; ADR 0009) is checked by
``planlayer.workflow``; these rules decide which transitions exist and what they need.
"""

from datetime import date
from typing import Literal, get_args

Status = Literal["not_started", "in_progress", "submitted", "approved"]
Action = Literal["submit", "recall", "approve", "reject", "reopen"]
RoundStatus = Literal["setup", "open", "closed"]

STATUSES: tuple[Status, ...] = get_args(Status)
ACTIONS: tuple[Action, ...] = get_args(Action)
EDITABLE: frozenset[Status] = frozenset({"not_started", "in_progress"})

_TRANSITIONS: dict[Action, tuple[frozenset[Status], Status]] = {
    "submit": (EDITABLE, "submitted"),
    "recall": (frozenset({"submitted"}), "in_progress"),
    "approve": (frozenset({"submitted"}), "approved"),
    "reject": (frozenset({"submitted"}), "in_progress"),
    "reopen": (frozenset({"approved"}), "in_progress"),
}
COMMENT_REQUIRED: frozenset[Action] = frozenset({"reject", "reopen"})

# Back to setup only while no cost center has started: checked where the statuses are known.
_ROUND_TRANSITIONS: frozenset[tuple[RoundStatus, RoundStatus]] = frozenset(
    {("setup", "open"), ("open", "setup"), ("open", "closed"), ("closed", "open")}
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


def refusal(
    action: Action, *, can_write: bool, approves: bool, controller: bool, plans_it: bool
) -> str | None:
    """Why a user may not take ``action`` on a cost center (ADR 0009), or None.

    ``can_write``: owner, deputy or controller; ``approves``: assigned to the cost center's
    nearest approver node; ``plans_it``: owner or deputy, who can't approve their own. A recall
    takes the planner's own submission back; a controller rejects instead, with a comment.
    """
    match action:
        case "submit":
            return None if can_write else "you don't plan this cost center"
        case "recall":
            return None if plans_it else "only who plans the cost center may recall it"
        case "approve" | "reject":
            if plans_it:
                return f"you can't {action} a cost center you plan"
            return None if controller or approves else "you aren't its approver"
        case "reopen":
            return None if controller else "only a controller may reopen"


def round_transition(current: RoundStatus, target: RoundStatus) -> RoundStatus:
    if (current, target) not in _ROUND_TRANSITIONS:
        raise WorkflowError(f"a round can't go from {current} to {target}")
    return target


def is_overdue(status: Status, deadline: date | None, today: date) -> bool:
    """Past the deadline and not yet submitted."""
    return deadline is not None and today > deadline and status in EDITABLE
