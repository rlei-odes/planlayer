"""Cost forwarding endpoints (ADR 0018): a round's forwardings, saved and deleted one at a time.
Amounts, rates, shares and quantities are decimal strings."""

from decimal import Decimal, InvalidOperation
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from planlayer import forwarding
from planlayer.api.auth import CurrentUser, engine_of
from planlayer.api.planning import HANDLED, _errors, _s
from planlayer.domain.workflow import RoundStatus, Status

router = APIRouter(prefix="/api")

Method = Literal["full", "fixed", "rate"]
Shares = Literal["percent", "portion"]


class AccountIO(BaseModel):
    code: str
    name: str


class ReceiverIn(BaseModel):
    cost_center_code: str
    quantity: str  # a share (percent or portion) or a quantity of the unit


class ForwardingIn(BaseModel):
    code: str
    name: str
    method: Method
    source_code: str
    group_code: str  # where new accounts go
    charge_account: AccountIO
    credit_account: AccountIO
    receivers: list[ReceiverIn]
    shares: Shares | None = None  # full and fixed
    amount: str | None = None  # fixed: the year's amount
    rate: str | None = None  # rate: per unit and year
    unit: str | None = None  # rate: what a quantity counts, e.g. m³


class ReceiverOut(BaseModel):
    cost_center_code: str
    cost_center_name: str
    quantity: str
    percent: str | None  # of what the forwarding passes on; null for a rate forwarding
    year: str | None  # charged in the round
    status: Status


class ForwardingOut(BaseModel):
    code: str
    name: str
    method: Method
    source_code: str
    source_name: str
    shares: Shares | None
    amount: str | None
    rate: str | None
    unit: str | None
    group_code: str | None
    charge_account: AccountIO
    credit_account: AccountIO
    receivers: list[ReceiverOut]
    passed_on: str | None  # credited to the source in the round
    changed_by: str | None


class SourceOut(BaseModel):
    cost_center_code: str
    cost_center_name: str
    costs: str | None  # planned costs in the round, charges received included
    other_credits: str | None  # revenue and capacity credits
    passed_on: str | None
    remaining: str | None  # what stays on the source: for rate forwardings, the coverage gap


class CodeNameOut(BaseModel):
    code: str
    name: str


class GroupOut(BaseModel):
    code: str
    name: str
    depth: int


class ForwardingPageOut(BaseModel):
    version_code: str
    version_name: str
    fiscal_year: int
    round_status: RoundStatus
    lock_reason: str | None
    closed_months: int
    forwardings: list[ForwardingOut]
    sources: list[SourceOut]
    cost_centers: list[CodeNameOut]
    groups: list[GroupOut]
    proposed_codes: list[str]  # free codes for a new forwarding's charge and credit accounts
    batch_id: str | None = None


def _decimal(value: str | None, what: str) -> Decimal | None:
    if value is None or not value.strip():
        return None
    try:
        d = Decimal(value.strip())
    except InvalidOperation:
        raise HTTPException(422, f"{what}: not a number: {value!r}") from None
    if not d.is_finite():
        raise HTTPException(422, f"{what}: not a number: {value!r}")
    return d


def _rate_text(value: Decimal | None) -> str | None:
    """Rates, shares and quantities without trailing zeros beyond the cents."""
    if value is None:
        return None
    text = f"{value:f}"
    if "." in text:
        whole, frac = text.split(".")
        frac = frac.rstrip("0")
        text = f"{whole}.{frac.ljust(2, '0')}" if frac else whole
    return text


def _page_out(p: forwarding.ForwardingPage, batch_id: str | None = None) -> ForwardingPageOut:
    return ForwardingPageOut(
        version_code=p.version_code,
        version_name=p.version_name,
        fiscal_year=p.fiscal_year,
        round_status=p.round_status,
        lock_reason=p.lock_reason,
        closed_months=p.closed_months,
        forwardings=[
            ForwardingOut(
                code=f.code,
                name=f.name,
                method=f.method,
                source_code=f.source_code,
                source_name=f.source_name,
                shares=f.shares,
                amount=_s(f.amount),
                rate=_rate_text(f.rate),
                unit=f.unit,
                group_code=f.group_code,
                charge_account=AccountIO(code=f.charge_account.code, name=f.charge_account.name),
                credit_account=AccountIO(code=f.credit_account.code, name=f.credit_account.name),
                receivers=[
                    ReceiverOut(
                        cost_center_code=r.cost_center_code,
                        cost_center_name=r.cost_center_name,
                        quantity=_rate_text(r.quantity) or "0",
                        percent=_s(r.percent),
                        year=_s(r.year),
                        status=r.status,
                    )
                    for r in f.receivers
                ],
                passed_on=_s(f.passed_on),
                changed_by=f.changed_by,
            )
            for f in p.forwardings
        ],
        sources=[
            SourceOut(
                cost_center_code=s.cost_center_code,
                cost_center_name=s.cost_center_name,
                costs=_s(s.costs),
                other_credits=_s(s.other_credits),
                passed_on=_s(s.passed_on),
                remaining=_s(s.remaining),
            )
            for s in p.sources
        ],
        cost_centers=[CodeNameOut(code=c, name=n) for c, n in p.cost_centers],
        groups=[GroupOut(code=g.code, name=g.name, depth=g.depth) for g in p.groups],
        proposed_codes=list(p.proposed_codes),
        batch_id=batch_id,
    )


def _given(body: ForwardingIn) -> forwarding.ForwardingIn:
    receivers = []
    for r in body.receivers:
        q = _decimal(r.quantity, r.cost_center_code)
        receivers.append(forwarding.ReceiverIn(r.cost_center_code, q or Decimal(0)))
    return forwarding.ForwardingIn(
        code=body.code,
        name=body.name,
        method=body.method,
        source_code=body.source_code,
        group_code=body.group_code,
        charge_account=forwarding.AccountIn(body.charge_account.code, body.charge_account.name),
        credit_account=forwarding.AccountIn(body.credit_account.code, body.credit_account.name),
        receivers=receivers,
        shares=body.shares,
        amount=_decimal(body.amount, "amount"),
        rate=_decimal(body.rate, "rate"),
        unit=body.unit,
    )


V = "/versions/{version_code}/forwarding"


@router.get(V, responses={403: {}, 404: {}})
def page(request: Request, user: CurrentUser, version_code: str) -> ForwardingPageOut:
    try:
        return _page_out(forwarding.read_page(engine_of(request), user, version_code))
    except HANDLED as e:
        raise _errors(e) from None


@router.post(V, responses={403: {}, 404: {}, 422: {}, 423: {}})
def create(
    request: Request, user: CurrentUser, version_code: str, body: ForwardingIn
) -> ForwardingPageOut:
    """A new forwarding; every forwarding of the round is recomputed in the same batch."""
    engine = engine_of(request)
    try:
        batch = forwarding.save(engine, user, version_code, _given(body), None)
        return _page_out(forwarding.read_page(engine, user, version_code), batch)
    except HANDLED as e:
        raise _errors(e) from None


@router.put(V + "/{code}", responses={403: {}, 404: {}, 422: {}, 423: {}})
def change(
    request: Request, user: CurrentUser, version_code: str, code: str, body: ForwardingIn
) -> ForwardingPageOut:
    engine = engine_of(request)
    try:
        batch = forwarding.save(engine, user, version_code, _given(body), code)
        return _page_out(forwarding.read_page(engine, user, version_code), batch)
    except HANDLED as e:
        raise _errors(e) from None


@router.delete(V + "/{code}", responses={403: {}, 404: {}, 423: {}})
def remove(request: Request, user: CurrentUser, version_code: str, code: str) -> ForwardingPageOut:
    engine = engine_of(request)
    try:
        batch = forwarding.delete_forwarding(engine, user, version_code, code)
        return _page_out(forwarding.read_page(engine, user, version_code), batch)
    except HANDLED as e:
        raise _errors(e) from None
