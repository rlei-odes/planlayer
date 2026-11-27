from datetime import date
from decimal import Decimal

import pytest

from planlayer.domain.entry_forms import EntryForm, resolve, weights
from planlayer.domain.periods import calendar_days, working_days
from planlayer.domain.spreading import Profile
from planlayer.domain.workflow import (
    WorkflowError,
    is_overdue,
    refusal,
    round_transition,
    transition,
)


def test_transitions() -> None:
    assert transition("not_started", "submit", None) == "submitted"
    assert transition("submitted", "approve", None) == "approved"
    assert transition("submitted", "recall", None) == "in_progress"
    assert transition("submitted", "reject", "why") == "in_progress"
    assert transition("approved", "reopen", "why") == "in_progress"
    with pytest.raises(WorkflowError, match="needs a comment"):
        transition("submitted", "reject", "  ")
    with pytest.raises(WorkflowError, match="can't approve a cost center that is in progress"):
        transition("in_progress", "approve", None)


def test_who_may_act() -> None:
    def why(action: str, **kw: bool) -> str | None:
        args = {"can_write": False, "approves": False, "controller": False, "plans_it": False}
        return refusal(action, **{**args, **kw})  # type: ignore[arg-type]

    assert why("submit", can_write=True) is None
    assert why("recall", plans_it=True) is None
    assert why("recall", can_write=True, controller=True) == (
        "only who plans the cost center may recall it"
    )
    assert why("submit") == "you don't plan this cost center"
    assert why("approve", approves=True) is None
    assert why("approve", controller=True) is None
    assert (
        why("approve", controller=True, plans_it=True) == "you can't approve a cost center you plan"
    )
    assert why("reject") == "you aren't its approver"
    assert why("reopen", approves=True) == "only a controller may reopen"


def test_round_transitions_and_overdue() -> None:
    assert round_transition("closed", "open") == "open"
    assert round_transition("open", "setup") == "setup"  # while no cost center has started
    with pytest.raises(WorkflowError):
        round_transition("setup", "closed")
    with pytest.raises(WorkflowError):
        round_transition("closed", "setup")
    assert is_overdue("in_progress", date(2026, 1, 31), date(2026, 2, 1))
    assert not is_overdue("submitted", date(2026, 1, 31), date(2026, 2, 1))
    assert not is_overdue("not_started", date(2026, 1, 31), date(2026, 1, 31))


def test_entry_forms() -> None:
    group, default = EntryForm("annual_even"), EntryForm("monthly")
    assert resolve([None, group, EntryForm("annual")], default) == group
    assert resolve([None, None], default) == default
    assert (group.months_visible, group.months_editable, group.basis) == (True, False, "even")
    assert not EntryForm("annual").months_visible
    assert EntryForm("monthly", profile_id=1).basis == "profile"
    assert EntryForm("annual_reference").basis == "reference"


def test_weights_fallback() -> None:
    w = weights("reference", 12, None, [Decimal(-5)] + [None] * 11)
    assert w.fallback and w.weights == [Decimal(1)] * 12
    assert not weights("reference", 12, None, [Decimal(3)] + [None] * 11).fallback
    profile = Profile.percentages([10] * 10 + [0, 0])
    assert weights("profile", 12, profile, []).weights[-1] == 0


def test_working_and_calendar_days() -> None:
    # 2026 starts on a Thursday: 52 weeks and one Thursday, 261 working days.
    assert working_days(2026, 1) == [22, 20, 22, 22, 21, 22, 23, 21, 22, 22, 21, 23]
    assert sum(working_days(2026, 1)) == 261
    # April start: April 2026 to March 2027; February 2027 is four whole weeks.
    assert working_days(2026, 4)[-2:] == [20, 23]
    assert calendar_days(2028, 1)[1] == 29 and calendar_days(2027, 1)[1] == 28
    assert calendar_days(2026, 4)[0] == 30 and sum(calendar_days(2026, 4)) == 365
