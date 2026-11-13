"""Generator tests that need no database."""

import hashlib
from datetime import date

import pytest

from gen_testdata import master as m
from gen_testdata.actuals import Rows
from gen_testdata.calendar import FIXED_ANCHOR, Calendar, relative_anchor


def build(variant: str = "standard", start_month: int = 1, seed: int = 42) -> m.Master:
    v = m.VARIANTS[variant]
    return m.build(v, Calendar(FIXED_ANCHOR, start_month, v.full_years), seed)


def digest(master: m.Master, seed: int = 42) -> str:
    rows = Rows(master, seed)
    h = hashlib.sha256()
    for part in (rows.fx_rates(), rows.billing_lines(), rows.gl_lines()):
        for row in part:
            h.update(repr(row).encode())
    return h.hexdigest()


def test_same_seed_same_data() -> None:
    assert digest(build("small")) == digest(build("small"))


def test_other_seed_other_data() -> None:
    assert digest(build("small", seed=1), 1) != digest(build("small"))


def test_fixed_calendar() -> None:
    periods = Calendar(FIXED_ANCHOR, 1, 3).periods
    assert (periods[0], periods[-1], len(periods)) == (date(2023, 1, 1), date(2026, 6, 1), 42)


def test_april_fiscal_year() -> None:
    cal = Calendar(FIXED_ANCHOR, 4, 1)
    assert cal.current_fy_start == date(2026, 4, 1)
    assert cal.periods[0] == date(2025, 4, 1)


def test_relative_anchor_is_end_of_last_month() -> None:
    assert relative_anchor(date(2026, 11, 8)) == date(2026, 10, 31)
    assert relative_anchor(date(2027, 1, 1)) == date(2026, 12, 31)


def test_standard_sizes() -> None:
    master = build()
    assert len(master.entities) == 4
    assert 55 <= len(master.cost_centers) <= 65
    assert len(master.accounts) == 158  # 157 booked, plus the new one (NEW_ACCOUNT)
    assert len(master.account_groups) == 14
    assert len(master.products) == 400
    assert len(master.product_groups) == 40
    assert len(master.countries) == 12
    assert len({c.region for c in master.countries}) == 4


def test_products_are_sparse_over_countries() -> None:
    master = build()
    pairs = sum(len(p.countries) for p in master.products)
    assert pairs < len(master.products) * len(master.countries) * 0.75


def test_tree_is_ragged() -> None:
    master = build()
    parents = {a.parent for a in master.cc_assignments}
    assert "DIV_CORP" in parents  # leaves directly under a division
    assert any(p.startswith("DEP_") for p in parents)


@pytest.mark.parametrize("variant", ["small", "standard"])
def test_special_cost_centers(variant: str) -> None:
    master = build(variant)
    cal = master.calendar
    moved = master.cc_by_name(m.MOVED_CC)
    tranches = [a for a in master.cc_assignments if a.code == moved.code]
    assert [t.parent for t in tranches] == ["DEP_PUMP_PROD", m.MOVED_TO]
    assert tranches[1].valid_from == cal.prior_fy_start
    assert master.cc_by_name(m.NEW_CC).valid_from == date(2025, 7, 1)
    assert master.cc_by_name(m.NEW_CC).owner is None
    if variant == "standard":
        assert master.cc_by_name(m.CLOSED_CC).valid_to == date(2025, 12, 31)
        assert master.cc_by_name(m.UNMATCHED_OWNER_CC).owner == m.UNMATCHED_OWNER
        renamed = [n.name for n in master.cc_nodes if n.code == m.RENAMED_DEPARTMENT]
        assert renamed == ["Pump Plant Poland", m.RENAMED_TO]
        # The next fiscal year: created ahead of it, closed at the end of the current one.
        new = master.cc_by_name(m.NEXT_NEW_CC[0])
        assert (new.code, new.valid_from) == ("DE1290", date(2027, 1, 1))
        assert master.cc_by_name(m.NEXT_CLOSED_CC).valid_to == date(2026, 12, 31)
        assert master.cc_by_name("Machining").code == "DE1010"  # existing codes stay


def test_owners_are_users_except_the_unmatched_one() -> None:
    owners = {c.owner for c in build().cost_centers} - {None}
    assert owners - set(m.USERS) == {m.UNMATCHED_OWNER}


def test_small_variant() -> None:
    master = build("small")
    assert [e.code for e in master.entities] == ["DE"]
    assert len(master.cost_centers) <= 16
    # Capped per group, except the credit and forwarding accounts.
    assert len([a for a in master.accounts if a.group not in ("CREDITS", "FORWARDED")]) <= 27
    assert len([a for a in master.accounts if a.group == "CREDITS"]) == 4
    assert master.calendar.periods[0] == date(2025, 1, 1)


def test_ledger_signs() -> None:
    master = build("small")
    rows = Rows(master, 42)
    list(rows.billing_lines())
    kinds = {a.code: "credit" if a.group == "CREDITS" else a.account_type for a in master.accounts}
    totals: dict[str, float] = {"revenue": 0.0, "cost": 0.0, "credit": 0.0}
    for row in rows.gl_lines():
        totals[kinds[str(row[5])]] += float(str(row[6]))
    # Revenue and activity credits are credit-side, other costs debit-side.
    assert totals["revenue"] < 0 < totals["cost"] and totals["credit"] < 0


def test_gl_needs_billing_first() -> None:
    with pytest.raises(RuntimeError):
        next(Rows(build("small"), 42).gl_lines())


def test_work_centers_of_production_cost_centers() -> None:
    master = build()
    by_code = {c.code: c for c in master.cost_centers}
    assert len(master.work_centers) == 32
    assert all(by_code[w.cost_center].function == "production" for w in master.work_centers)
    machining = [w for w in master.work_centers if by_code[w.cost_center].name == "Machining"]
    assert [w.name for w in machining] == ["Machining centre 1", "Machining centre 2", "Lathe"]
    credited = {a.code for a in master.accounts if a.group == "CREDITS"}
    assert {account for _, _, account in m.ACTIVITY_TYPES} == credited
    # Credit accounts are booked only by the activity credits, not as cost series.
    assert all(not a.functions for a in master.accounts if a.group == "CREDITS")

    rows = Rows(master, 42)
    hours = list(rows.capacity_hours())
    outputs = sum(len(w.outputs) for w in master.work_centers)
    assert len(hours) == outputs * len(master.calendar.periods)
    assert hours == list(Rows(master, 42).capacity_hours())  # deterministic


def test_credits_are_actual_hours_times_rate() -> None:
    """TD-21: each work center's activity is credited to its cost center, hours x rate."""
    master = build()
    rows = Rows(master, 42)
    list(rows.billing_lines())
    credits = {a.code for a in master.accounts if a.group == "CREDITS"}
    lathe = next(w for w in master.work_centers if w.name == "Lathe")
    first = master.calendar.periods[0]
    hours = next(
        float(str(h)) for p, wc, _, h in rows.capacity_hours() if p == first and wc == lathe.code
    )
    booked = [
        row
        for row in rows.gl_lines()
        if row[5] in credits and row[4] == lathe.cost_center
        and str(row[3])[:7] == first.isoformat()[:7]
    ]  # fmt: skip
    # The lathe's machine hours, plus the other machining centres on the same cost center.
    assert any(abs(-float(str(r[6])) - hours * 80) < 0.02 for r in booked)
    assert all(float(str(r[6])) < 0 for r in booked)


def test_cost_forwarding_balances_and_passes_on_toolings_costs() -> None:
    """TD-22: forwarding documents net to zero; tooling passes on all it spends."""
    master = build()
    rows = Rows(master, 42)
    list(rows.billing_lines())
    tooling = master.cc_by_name("Tooling").code
    first = master.calendar.periods[0].isoformat()[:7]
    month = [r for r in rows.gl_lines() if str(r[3])[:7] == first]
    forwarded = [r for r in month if r[5] in m.FORWARDED_ACCOUNT_NAMES]
    docs: dict[object, float] = {}
    for r in forwarded:
        docs[r[0]] = docs.get(r[0], 0.0) + float(str(r[6]))
    assert len(docs) == len(m.FORWARDINGS) and all(abs(v) < 0.005 for v in docs.values())
    left = sum(float(str(r[6])) for r in month if r[4] == tooling and r[5] != "8800")
    passed = -sum(float(str(r[6])) for r in forwarded if r[4] == tooling and r[5] == "8800")
    assert abs(passed - left) < 0.02  # 40 + 25 + 20 + 15 %
