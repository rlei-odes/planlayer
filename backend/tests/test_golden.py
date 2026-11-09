"""Golden cases: the seasonality profiles of TD-17 and the spreading part of TD-14.

The expected values in ``golden/`` were worked out by hand, not taken from the engine; each case's
comment shows the arithmetic. The first test checks the fixtures themselves.
"""

import tomllib
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from planlayer.domain.entry_forms import weights as line_weights
from planlayer.domain.periods import fiscal_year_periods
from planlayer.domain.spreading import (
    Profile,
    SpreadError,
    from_reference,
    spread,
    spread_cells,
)

GOLDEN = Path(__file__).parent / "golden"
Case = dict[str, Any]


def _load(name: str) -> dict[str, list[Case]]:
    with (GOLDEN / name).open("rb") as f:
        return tomllib.load(f)


PROFILES = {p["code"]: p for p in _load("profiles.toml")["profile"]}
SPREADS = _load("spreads.toml")


def _cases(*kinds: str) -> list[Any]:
    return [pytest.param(c, id=c["id"]) for kind in kinds for c in SPREADS[kind]]


def _amounts(values: list[str]) -> list[Decimal]:
    return [Decimal(v) for v in values]


def _reference(values: list[str]) -> list[Decimal | None]:
    return [None if v == "" else Decimal(v) for v in values]


def _expected(case: Case) -> list[Decimal]:
    expected = case["expected"]
    return _amounts(list(expected.values()) if isinstance(expected, dict) else expected)


def _profile(code: str) -> Profile:
    p = PROFILES[code]
    return Profile(tuple(_amounts(p["values"])), p["entered_as"])


@pytest.mark.parametrize("case", [c for c in _cases(*SPREADS) if "expected" in c.values[0]])
def test_fixture_ties_to_its_total(case: Case) -> None:
    assert sum(_expected(case)) == Decimal(case["total"])


@pytest.mark.parametrize("p", [pytest.param(p, id=code) for code, p in PROFILES.items()])
def test_profile_validation(p: Case) -> None:
    if "error" in p:
        with pytest.raises(SpreadError, match=p["error"]):
            _profile(p["code"])
    else:
        assert _profile(p["code"]).values == tuple(_amounts(p["values"]))


@pytest.mark.parametrize("case", _cases("profile_spread"))
def test_profile_spread(case: Case) -> None:
    months = case.get("months")
    profile = _profile(case["profile"])
    w = profile.weights(range(months[0], months[1] + 1) if months else None)
    assert spread(Decimal(case["total"]), w) == _expected(case)


@pytest.mark.parametrize("case", _cases("fiscal_start"))
def test_profile_over_fiscal_periods(case: Case) -> None:
    periods = fiscal_year_periods(case["year"], case["start_month"])
    w = dict(zip(periods, _profile(case["profile"]).weights(), strict=True))
    result = spread_cells(Decimal(case["total"]), w)
    assert {p.isoformat(): v for p, v in result.items()} == {
        k: Decimal(v) for k, v in case["expected"].items()
    }


@pytest.mark.parametrize("case", _cases("line"))
def test_line(case: Case) -> None:
    profile = _profile(case["profile"]) if "profile" in case else None
    w = line_weights(case["basis"], 12, profile, _reference(case.get("reference", [])))
    assert w.fallback == case["fallback"]
    assert spread(Decimal(case["total"]), w.weights) == _expected(case)


@pytest.mark.parametrize("case", _cases("reference_spread"))
def test_reference_spread(case: Case) -> None:
    reference = case["reference"]
    keys = list(reference) if isinstance(reference, dict) else list(range(len(reference)))
    values = _reference(list(reference.values()) if isinstance(reference, dict) else reference)
    policies = {k: case[k] for k in ("negative", "if_zero") if k in case}
    if "error" in case:
        with pytest.raises(SpreadError, match=case["error"]):
            from_reference(values, **policies)
        return
    w = dict(zip(keys, from_reference(values, **policies), strict=True))
    assert list(spread_cells(Decimal(case["total"]), w).values()) == _expected(case)
