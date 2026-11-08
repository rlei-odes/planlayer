from datetime import date
from decimal import Decimal

import pytest

from planlayer.domain.entry_forms import EntryForm, resolve, weights
from planlayer.domain.spreading import Profile
from planlayer.domain.workflow import (
    WorkflowError,
    check_not_own,
    is_overdue,
    round_transition,
    transition,
)


def test_transitions() -> None:
    assert transition("not_started", "submit", None) == "submitted"
    assert transition("submitted", "approve", None) == "approved"
    assert transition("submitted", "reject", "why") == "in_progress"
    assert transition("approved", "reopen", "why") == "in_progress"
    with pytest.raises(WorkflowError, match="needs a comment"):
        transition("submitted", "reject", "  ")
    with pytest.raises(WorkflowError, match="can't approve a cost center that is in progress"):
        transition("in_progress", "approve", None)
    with pytest.raises(WorkflowError):
        check_not_own("approve", "anna", "anna")
    check_not_own("submit", "anna", "anna")


def test_round_transitions_and_overdue() -> None:
    assert round_transition("closed", "open") == "open"
    with pytest.raises(WorkflowError):
        round_transition("open", "setup")
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
