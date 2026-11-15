"""Personnel cost rules (ADR 0022): pro rata base pay, extras, raises, awards, bonus,
contributions with ceilings, typed positions and group sizes."""

from datetime import date
from decimal import Decimal

from planlayer.domain.periods import fiscal_year_periods
from planlayer.domain.personnel import (
    Award,
    Contribution,
    Extra,
    PayPosition,
    Raise,
    Rules,
    Step,
    active_share,
    costs,
    group_counts,
    percent_as_salaries,
    salaries_as_percent,
)

YEAR = fiscal_year_periods(2027, 1)
SALARY, EXTRA, RAISE, AWARD, BONUS, PENSION, HEALTH, FEES = range(1, 9)
DE, NO = 100, 101
D = Decimal


def person(salary: str = "5000", **kw: object) -> PayPosition:
    return PayPosition(DE, SALARY, False, D(salary), **kw)  # type: ignore[arg-type]


def year(c: dict[int, list[Decimal]], account: int) -> Decimal:
    return sum(c.get(account, []), D(0))


def test_active_share_counts_days_from_entry_to_leave() -> None:
    april = date(2027, 4, 1)
    assert active_share(april, None, None) == 1
    assert active_share(april, date(2027, 4, 16), None) == D(15) / D(30)
    assert active_share(april, None, date(2027, 4, 10)) == D(10) / D(30)
    assert active_share(april, date(2027, 5, 1), None) == 0
    assert active_share(april, None, date(2027, 3, 31)) == 0
    assert active_share(april, date(2020, 1, 1), date(2030, 1, 1)) == 1


def test_base_pay_starts_with_the_entry_date() -> None:
    c = costs(person(entry_date=date(2027, 7, 1)), Rules(), YEAR)
    assert c[SALARY][:6] == [0] * 6
    assert c[SALARY][6:] == [D(5000)] * 6


def test_a_13th_salary_is_spread_over_the_year() -> None:
    c = costs(person(), Rules(extras=(Extra(EXTRA, D(1)),)), YEAR)
    assert round(year(c, EXTRA), 2) == D(5000)
    assert c[EXTRA][0] == D(5000) / 12


def test_holiday_pay_as_a_percentage_is_the_same_rule() -> None:
    assert percent_as_salaries(D("10.2")) == D("1.224")
    assert salaries_as_percent(D("1.224")) == D("10.2")
    c = costs(person("1000"), Rules(extras=(Extra(EXTRA, percent_as_salaries(D("10.2"))),)), YEAR)
    assert round(year(c, EXTRA), 2) == D("1224.00")


def test_raise_compounds_on_base_and_extras_from_its_month() -> None:
    rules = Rules(
        extras=(Extra(EXTRA, D(1)),),
        raises=(
            Raise(RAISE, D(1), date(2027, 10, 1)),
            Raise(RAISE, D(2), date(2027, 4, 1)),
        ),
    )
    c = costs(person("1200"), rules, YEAR)
    monthly = D(1200) * (1 + D(1) / 12)  # base and the 13th: 1300
    assert c[RAISE][2] == 0
    assert c[RAISE][3] == monthly * D("0.02")
    assert c[RAISE][9] == monthly * (D("1.02") * D("1.01") - 1)
    assert c[SALARY][9] == 1200  # the salary itself stays as planned


def test_award_in_the_anniversary_month() -> None:
    rules = Rules(awards=(Award(AWARD, (Step(10, salaries=D(1)), Step(25, amount=D(500)))),))
    c = costs(person(entry_date=date(2017, 5, 20)), rules, YEAR)
    assert c[AWARD][4] == D(5000)
    assert year(c, AWARD) == D(5000)
    c = costs(person(entry_date=date(2002, 3, 1)), rules, YEAR)
    assert c[AWARD][2] == D(500)
    assert AWARD not in costs(person(entry_date=date(2020, 5, 1)), rules, YEAR)
    assert AWARD not in costs(person(), rules, YEAR)


def test_bonus_spread_over_the_months_worked() -> None:
    rules = Rules(bonus_account=BONUS)
    c = costs(person(bonus=D(6000), leave_date=date(2027, 6, 30)), rules, YEAR)
    assert c[BONUS][:6] == [D(1000)] * 6
    assert year(c, BONUS) == D(6000)
    assert BONUS not in costs(person(bonus=D(6000)), Rules(), YEAR)


def test_contribution_stops_at_its_ceiling() -> None:
    rules = Rules(
        contributions=(
            Contribution(PENSION, D("9.3"), ceiling=D(8450)),
            Contribution(HEALTH, D("7.3"), ceiling=D("5812.50")),
        )
    )
    low = costs(person("4000"), rules, YEAR)
    assert low[PENSION][0] == D(4000) * D("0.093")
    high = costs(person("10000"), rules, YEAR)
    assert high[PENSION][0] == D(8450) * D("0.093")
    assert high[HEALTH][0] == D("5812.50") * D("0.073")
    # A partial month has a partial ceiling.
    half = costs(person("10000", entry_date=date(2027, 4, 16)), rules, YEAR)
    assert half[PENSION][3] == D(8450) * D(15) / D(30) * D("0.093")


def test_contribution_on_gross_pay_with_extras_and_bonus() -> None:
    rules = Rules(
        extras=(Extra(EXTRA, D(1)),),
        bonus_account=BONUS,
        contributions=(Contribution(PENSION, D(10)),),
    )
    c = costs(person("1200", bonus=D(1200)), rules, YEAR)
    assert c[PENSION][0] == (D(1200) + D(100) + D(100)) * D("0.1")


def test_rules_of_another_entity_dont_apply() -> None:
    rules = Rules(extras=(Extra(EXTRA, D(1), entity=NO),))
    assert EXTRA not in costs(person(), rules, YEAR)


def test_typed_position_is_its_amount_only() -> None:
    rules = Rules(
        extras=(Extra(EXTRA, D(1)),),
        raises=(Raise(RAISE, D(2), date(2027, 1, 1)),),
        contributions=(Contribution(PENSION, D(10)),),
    )
    c = costs(PayPosition(DE, FEES, True, D(3000), entry_date=date(2027, 4, 16)), rules, YEAR)
    assert set(c) == {FEES}
    assert c[FEES][3] == D(1500)
    assert c[FEES][4] == D(3000)


def test_group_counts_per_account() -> None:
    rules = Rules(contributions=(Contribution(PENSION, D(10)),))
    employees = [costs(person(), rules, YEAR) for _ in range(2)]
    freelancer = costs(PayPosition(DE, FEES, True, D(3000)), rules, YEAR)
    vacancy = costs(person("0"), rules, YEAR)
    assert group_counts([*employees, freelancer, vacancy]) == {SALARY: 2, PENSION: 2, FEES: 1}
