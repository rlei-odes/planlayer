"""Actuals (TD-07) as raw ERP rows: billing lines, GL lines and monthly FX rates.

Amounts are integer cents internally. GL lines are ledger-signed (debit positive): costs are
positive, revenue negative. Every series has its own random generator, seeded from the series
key, so changing one part of the data does not shift the rest.

Patterns: growth, seasonality (August shutdown, Q4 sales peak, bonus months for personnel),
noise, one-off spikes, negative corrections, months netting to exactly zero (a posting and its
reversal) and months with no posting at all, kept distinct.
"""

import math
import random
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date

from gen_testdata.master import LINE_REVENUE_ACCOUNT, Account, CostCenter, Master

BASE_RATE_MICRO = {"EUR": 1_000_000, "CHF": 1_050_000, "PLN": 232_000, "USD": 920_000}

# Factor per calendar month (index 1..12).
SEASON = {
    "production": {8: 0.55, 12: 0.85},
    "sales": {1: 0.85, 8: 0.7, 10: 1.1, 11: 1.2, 12: 1.3},
    "admin": {},
}
BONUS_MONTHS = {6: 1.6, 12: 1.6}  # personnel: 13th salary and bonus

# Probabilities per series month, cumulative.
P_MISSING = 0.03
P_ZERO = 0.035
P_SPIKE = 0.04
P_CORRECTION = 0.043
P_SPLIT = 0.2


def fx_rate_micro(currency: str, period_index: int) -> int:
    """Rate local -> EUR in millionths, drifting smoothly per month."""
    if currency == "EUR":
        return 1_000_000
    return round(BASE_RATE_MICRO[currency] * (1 + 0.03 * math.sin(period_index / 4)))


def cents(value: int) -> str:
    sign = "-" if value < 0 else ""
    value = abs(value)
    return f"{sign}{value // 100}.{value % 100:02d}"


def micro(value: int) -> str:
    return f"{value // 1_000_000}.{value % 1_000_000:06d}"


@dataclass
class Rows:
    """Row generators in the column order of the bronze tables."""

    master: Master
    seed: int

    def __post_init__(self) -> None:
        self.periods = self.master.calendar.periods
        self.currency = {e.code: e.currency for e in self.master.entities}
        self._doc: dict[str, int] = defaultdict(int)
        # Sales revenue to book in the GL: (entity, cost center, period, account) -> local cents.
        self._revenue: dict[tuple[str, str, date, str], int] = defaultdict(int)
        self._sales_done = False

    def capacity_hours(self) -> Iterator[tuple[object, ...]]:
        """(periode, arbpl, lstar, menge): actual hours per work center, activity type and
        month, with the production season (August shutdown) and noise."""
        for wc in self.master.work_centers:
            for activity, _rate, hours in wc.outputs:
                rng = random.Random(f"{self.seed}:hours:{wc.code}:{activity}")
                for period in self.periods:
                    factor = SEASON["production"].get(period.month, 1.0) * rng.uniform(0.9, 1.1)
                    yield (period, wc.code, activity, f"{hours * factor:.2f}")

    def fx_rates(self) -> Iterator[tuple[object, ...]]:
        for i, period in enumerate(self.periods):
            for currency in sorted(set(self.currency.values())):
                yield (period, currency, micro(fx_rate_micro(currency, i)))

    def _to_local(self, eur_cents: float, entity: str, period_index: int) -> int:
        rate = fx_rate_micro(self.currency[entity], period_index)
        return round(eur_cents * 1_000_000 / rate)

    def _doc_no(self, entity: str) -> str:
        self._doc[entity] += 1
        return f"{entity}{self._doc[entity]:09d}"

    def billing_lines(self) -> Iterator[tuple[object, ...]]:
        """(vbeln, posnr, fkdat, bukrs, land1, matnr, fkimg, netwr_lc, waers)"""
        countries = {c.code: c for c in self.master.countries}
        sales_cc = {c.code: self._sales_cc(c.sales_cc).code for c in self.master.countries}
        for product in self.master.products:
            for country_code in product.countries:
                country = countries[country_code]
                entity = country.entity
                rng = random.Random(f"{self.seed}:sales:{product.code}:{country_code}")
                base = product.monthly_qty * rng.uniform(0.5, 1.5)
                discount = rng.uniform(0.88, 1.0)
                for i, period in enumerate(self.periods):
                    factor = SEASON["sales"].get(period.month, 1.0) * 1.03 ** (i / 12)
                    qty = round(base * factor * rng.uniform(0.75, 1.25))
                    r = rng.random()
                    lines: list[int] = []
                    if r < 0.003:
                        lines = [-rng.randint(1, 2)]  # return
                    elif r < 0.006:
                        lines = [max(qty, 1), -max(qty, 1)]  # invoice and its credit note
                    elif qty > 0:
                        lines = [qty]
                    for q in lines:
                        eur = q * product.price_eur_cents * discount
                        local = self._to_local(eur, entity, i)
                        account = LINE_REVENUE_ACCOUNT[product.line]
                        self._revenue[(entity, sales_cc[country_code], period, account)] -= local
                        day = date(period.year, period.month, rng.randint(1, 28))
                        doc = self._doc_no(entity)
                        yield (
                            doc,
                            1,
                            day,
                            entity,
                            country_code,
                            product.code,
                            q,
                            cents(local),
                            self.currency[entity],
                        )
        self._sales_done = True

    def gl_lines(self) -> Iterator[tuple[object, ...]]:
        """(belnr, buzei, bukrs, budat, kostl, saknr, betrag_lc, waers). Needs billing first."""
        if not self._sales_done:
            raise RuntimeError("billing_lines() must be consumed before gl_lines()")
        for (entity, cc_code, period, account_code), local in sorted(self._revenue.items()):
            day = date(period.year, period.month, 28)
            yield (
                self._doc_no(entity),
                1,
                entity,
                day,
                cc_code,
                account_code,
                cents(local),
                self.currency[entity],
            )
        for cc in self.master.cost_centers:
            for account in self.master.accounts:
                yield from self._cost_series(cc, account)

    def _cost_series(self, cc: CostCenter, account: Account) -> Iterator[tuple[object, ...]]:
        if cc.function not in account.functions:
            return
        rng = random.Random(f"{self.seed}:gl:{cc.code}:{account.code}")
        if rng.random() > self.master.variant.account_share:
            return  # not every cost center uses every eligible account
        base = account.monthly_eur * 100 * rng.lognormvariate(0, 0.5)
        flat = account.group == "DEPRECIATION"
        for i, period in enumerate(self.periods):
            if not cc.valid_from <= period <= cc.valid_to:
                continue
            factor = 1.04 ** (i / 12)
            if not flat:
                factor *= SEASON[cc.function].get(period.month, 1.0) * rng.uniform(0.92, 1.08)
                if account.group == "PERSONNEL":
                    factor *= BONUS_MONTHS.get(period.month, 1.0)
            r = rng.random()
            if r < P_MISSING:
                continue
            # Ledger signs: revenue accounts are credited (negative), cost accounts debited.
            eur = base * factor * (-1 if account.account_type == "revenue" else 1)
            amounts: list[float]
            if r < P_ZERO:
                amounts = [eur, -eur]
            elif r < P_SPIKE:
                amounts = [eur * 3]
            elif r < P_CORRECTION:
                amounts = [-eur * 0.3]
            elif rng.random() < P_SPLIT:
                amounts = [eur * 0.6, eur * 0.4]
            else:
                amounts = [eur]
            locals_ = [self._to_local(a, cc.entity, i) for a in amounts]
            if r < P_ZERO:
                locals_[1] = -locals_[0]  # exact reversal, so the month nets to 0.00
            doc = self._doc_no(cc.entity)
            day = date(period.year, period.month, rng.randint(1, 28))
            for line, local in enumerate(locals_, start=1):
                yield (
                    doc,
                    line,
                    cc.entity,
                    day,
                    cc.code,
                    account.code,
                    cents(local),
                    self.currency[cc.entity],
                )

    def _sales_cc(self, name: str) -> CostCenter:
        return next(
            cc
            for cc in self.master.cost_centers
            if cc.name == name or cc.name.startswith(name + " ")
        )
