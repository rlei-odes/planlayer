"""Personnel cost endpoints (ADR 0022): a cost center's pay for HR and its owner, HR's review,
the round's rules for HR and controllers, and turning the module on. Amounts are decimal
strings."""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Request
from pydantic import BaseModel

from planlayer import personnel
from planlayer.api.auth import CurrentUser, engine_of
from planlayer.api.capacity import CodeName
from planlayer.api.planning import HANDLED, _errors, _s
from planlayer.domain.workflow import RoundStatus, Status
from planlayer.personnel.rules import Kind
from planlayer.sheet import EditError, parse_amount

router = APIRouter(prefix="/api")


class PayPositionOut(BaseModel):
    position_id: int
    code: str
    title: str
    person_name: str | None
    staff_type: str
    staff_category: str
    entry_date: date | None
    leave_date: date | None
    contract_percent: str | None
    is_plan: bool
    typed: bool | None  # null: the staff type isn't costed in the round
    hr_salary: str | None  # from HR's staff list; read only
    salary: str | None  # planned, monthly; null: HR's
    bonus: str | None  # yearly
    charged_to: str | None  # another cost center carrying the costs
    year_cost: str
    transferred_to: str | None


class PayAccountOut(BaseModel):
    code: str
    name: str
    months: list[str | None]
    small: int | None  # positions behind it, when fewer than the minimum


class PayReviewOut(BaseModel):
    confirmed_by: str
    confirmed_at: datetime


class PayPageOut(BaseModel):
    round_code: str
    round_name: str
    round_status: RoundStatus
    fiscal_year: int
    cost_center_code: str
    cost_center_name: str
    status: Status
    lock_reason: str | None
    periods: list[date]
    closed_months: int
    min_group: int
    positions: list[PayPositionOut]
    accounts: list[PayAccountOut]
    review: PayReviewOut | None
    may_review: bool
    may_charge: bool
    charge_targets: list[CodeName]
    batch_id: str | None = None


class PayUnitOut(BaseModel):
    code: str
    name: str
    status: Status
    positions: int
    small_groups: int
    confirmed: bool


class PayUnitsOut(BaseModel):
    released: bool  # HR has released the round's pay to its readers (ADR 0023)
    units: list[PayUnitOut]


class PayPersonOut(BaseModel):
    login: str
    name: str


class PayRightsRowOut(BaseModel):
    code: str
    name: str
    readers: list[PayPersonOut]
    suggested: list[str]  # logins: the source owner, deputies, approvers above
    source_owner: PayPersonOut | None
    proposal: bool  # the source owner isn't the one HR decided on


class PayLogOut(BaseModel):
    at: datetime
    actor: str | None
    action: str
    round_code: str | None
    cost_center: str | None
    user: str | None


class PayRightsOut(BaseModel):
    round_code: str
    round_status: RoundStatus
    released_by: str | None
    released_at: datetime | None
    rows: list[PayRightsRowOut]
    users: list[PayPersonOut]
    log: list[PayLogOut]


class PayReadersIn(BaseModel):
    readers: list[str]  # logins
    owner_decided: bool = False  # the source's current owner counts as decided on


class PayReleaseIn(BaseModel):
    released: bool


class PayIn(BaseModel):
    salary: str | None = None  # null or blank: HR's
    bonus: str | None = None
    charged_to: str | None = None


class PayReviewIn(BaseModel):
    confirmed: bool


class PayStepIO(BaseModel):
    years: int
    salaries: str | None = None
    amount: str | None = None


class PayRuleIO(BaseModel):
    kind: Kind
    name: str
    account: str
    entity: str | None = None
    salaries: str | None = None  # extra: salaries per year
    percent: str | None = None  # raise, contribution
    ceiling: str | None = None  # contribution: monthly
    starts: date | None = None  # raise: its first month
    steps: list[PayStepIO]  # award; empty for the other kinds


class PayStaffTypeIO(BaseModel):
    code: str
    name: str = ""
    external: bool = False
    account: str | None  # null: not costed
    typed: bool


class PayRulesIO(BaseModel):
    min_group: int
    bonus_account: str | None
    staff_types: list[PayStaffTypeIO]
    rules: list[PayRuleIO]


class PayRulesPageOut(BaseModel):
    round_code: str
    round_status: RoundStatus
    fiscal_year: int
    workforce_positions: bool  # the round plans positions: the module can be on
    on: bool
    may_edit: bool
    periods: list[date]
    rules: PayRulesIO
    accounts: list[CodeName]
    entities: list[CodeName]


def _page_out(p: personnel.PayPage, batch_id: str | None = None) -> PayPageOut:
    return PayPageOut(
        round_code=p.round_code,
        round_name=p.round_name,
        round_status=p.round_status,  # type: ignore[arg-type]
        fiscal_year=p.fiscal_year,
        cost_center_code=p.cost_center_code,
        cost_center_name=p.cost_center_name,
        status=p.status,  # type: ignore[arg-type]
        lock_reason=p.lock_reason,
        periods=p.periods,
        closed_months=p.closed_months,
        min_group=p.min_group,
        positions=[
            PayPositionOut(
                position_id=r.position_id,
                code=r.code,
                title=r.title,
                person_name=r.person_name,
                staff_type=r.staff_type,
                staff_category=r.staff_category,
                entry_date=r.entry_date,
                leave_date=r.leave_date,
                contract_percent=_s(r.contract_percent),
                is_plan=r.is_plan,
                typed=r.typed,
                hr_salary=_s(r.hr_salary),
                salary=_s(r.salary),
                bonus=_s(r.bonus),
                charged_to=r.charged_to,
                year_cost=f"{r.year_cost:.2f}",
                transferred_to=r.transferred_to,
            )
            for r in p.positions
        ],
        accounts=[
            PayAccountOut(code=a.code, name=a.name, months=[_s(v) for v in a.months], small=a.small)
            for a in p.accounts
        ],
        review=PayReviewOut(confirmed_by=p.review.confirmed_by, confirmed_at=p.review.confirmed_at)
        if p.review
        else None,
        may_review=p.may_review,
        may_charge=p.may_charge,
        charge_targets=[CodeName(code=c, name=n) for c, n in p.charge_targets],
        batch_id=batch_id,
    )


def _number(value: str | None, what: str) -> Decimal | None:
    if value is None or not value.strip():
        return None
    text = value.strip().removesuffix("%").strip().replace(",", ".")
    try:
        number = Decimal(text)
    except InvalidOperation:
        raise EditError(f"not a {what}: {value!r}") from None
    if not number.is_finite():
        raise EditError(f"not a {what}: {value!r}")
    return number


def _rules_in(body: PayRulesIO) -> personnel.RulesIO:
    return personnel.RulesIO(
        body.min_group,
        body.bonus_account or None,
        [personnel.StaffTypeIO(t.code, t.account or None, t.typed) for t in body.staff_types],
        [
            personnel.RuleIO(
                r.kind,
                r.name,
                r.account,
                r.entity or None,
                _number(r.salaries, "number of salaries"),
                _number(r.percent, "percentage"),
                parse_amount(r.ceiling),
                r.starts,
                [
                    personnel.StepIO(
                        s.years, _number(s.salaries, "number of salaries"), parse_amount(s.amount)
                    )
                    for s in r.steps
                ],
            )
            for r in body.rules
        ],
    )


def _num(value: Decimal | None) -> str | None:
    return None if value is None else f"{value.normalize():f}"


def _rules_out(p: personnel.RulesPage) -> PayRulesPageOut:
    r = p.rules
    return PayRulesPageOut(
        round_code=p.round_code,
        round_status=p.round_status,  # type: ignore[arg-type]
        fiscal_year=p.fiscal_year,
        workforce_positions=p.workforce_positions,
        on=p.on,
        may_edit=p.may_edit,
        periods=p.periods,
        rules=PayRulesIO(
            min_group=r.min_group,
            bonus_account=r.bonus_account,
            staff_types=[
                PayStaffTypeIO(
                    code=t.code, name=t.name, external=t.external, account=t.account, typed=t.typed
                )
                for t in r.staff_types
            ],
            rules=[
                PayRuleIO(
                    kind=x.kind,
                    name=x.name,
                    account=x.account,
                    entity=x.entity,
                    salaries=_num(x.salaries),
                    percent=_num(x.percent),
                    ceiling=_s(x.ceiling),
                    starts=x.starts,
                    steps=[
                        PayStepIO(years=s.years, salaries=_num(s.salaries), amount=_s(s.amount))
                        for s in x.steps
                    ],
                )
                for x in r.rules
            ],
        ),
        accounts=[CodeName(code=c, name=n) for c, n in p.accounts],
        entities=[CodeName(code=c, name=n) for c, n in p.entities],
    )


V = "/versions/{version_code}/personnel"


@router.get(V, responses={404: {}})
def units(request: Request, user: CurrentUser, version_code: str) -> PayUnitsOut:
    """The cost centers planned in the round whose pay the user reads."""
    try:
        released, found = personnel.units(engine_of(request), user, version_code)
    except HANDLED as e:
        raise _errors(e) from None
    return PayUnitsOut(
        released=released,
        units=[
            PayUnitOut(
                code=u.code,
                name=u.name,
                status=u.status,  # type: ignore[arg-type]
                positions=u.positions,
                small_groups=u.small_groups,
                confirmed=u.confirmed,
            )
            for u in found
        ],
    )


def _rights_out(p: personnel.RightsPage) -> PayRightsOut:
    def person(x: personnel.Person) -> PayPersonOut:
        return PayPersonOut(login=x.login, name=x.name)

    return PayRightsOut(
        round_code=p.round_code,
        round_status=p.round_status,  # type: ignore[arg-type]
        released_by=p.released_by,
        released_at=p.released_at,
        rows=[
            PayRightsRowOut(
                code=r.code,
                name=r.name,
                readers=[person(x) for x in r.readers],
                suggested=r.suggested,
                source_owner=person(r.source_owner) if r.source_owner else None,
                proposal=r.proposal,
            )
            for r in p.rows
        ],
        users=[person(x) for x in p.users],
        log=[PayLogOut.model_validate(e, from_attributes=True) for e in p.log],
    )


@router.get(V + "/access", responses={403: {}, 404: {}})
def rights(request: Request, user: CurrentUser, version_code: str) -> PayRightsOut:
    """Who reads pay in the round, the release and the log (HR)."""
    try:
        return _rights_out(personnel.read_rights(engine_of(request), user, version_code))
    except HANDLED as e:
        raise _errors(e) from None


@router.put(V + "/access/release", responses={403: {}, 404: {}})
def release(
    request: Request, user: CurrentUser, version_code: str, body: PayReleaseIn
) -> PayRightsOut:
    """Release the round's pay to its readers, or take the release back (HR)."""
    engine = engine_of(request)
    try:
        personnel.release(engine, user, version_code, body.released)
        return _rights_out(personnel.read_rights(engine, user, version_code))
    except HANDLED as e:
        raise _errors(e) from None


@router.put(V + "/access/{cc_code}", responses={403: {}, 404: {}, 422: {}})
def set_readers(
    request: Request, user: CurrentUser, version_code: str, cc_code: str, body: PayReadersIn
) -> PayRightsOut:
    """Replace a cost center's readers in the round (HR)."""
    engine = engine_of(request)
    try:
        personnel.set_readers(
            engine, user, version_code, cc_code, body.readers, owner_decided=body.owner_decided
        )
        return _rights_out(personnel.read_rights(engine, user, version_code))
    except HANDLED as e:
        raise _errors(e) from None


@router.get(V + "/rules", responses={403: {}, 404: {}})
def rules(request: Request, user: CurrentUser, version_code: str) -> PayRulesPageOut:
    try:
        return _rules_out(personnel.read_rules(engine_of(request), user, version_code))
    except HANDLED as e:
        raise _errors(e) from None


@router.put(V + "/rules", responses={403: {}, 404: {}, 422: {}, 423: {}})
def save_rules(
    request: Request, user: CurrentUser, version_code: str, body: PayRulesIO
) -> PayRulesPageOut:
    """Replace the round's rules; every cost center's totals are recomputed."""
    engine = engine_of(request)
    try:
        personnel.save_rules(engine, user, version_code, _rules_in(body))
        return _rules_out(personnel.read_rules(engine, user, version_code))
    except HANDLED as e:
        raise _errors(e) from None


@router.get(V + "/{cc_code}", responses={404: {}})
def page(request: Request, user: CurrentUser, version_code: str, cc_code: str) -> PayPageOut:
    try:
        return _page_out(personnel.read_page(engine_of(request), user, version_code, cc_code))
    except HANDLED as e:
        raise _errors(e) from None


@router.put(
    V + "/{cc_code}/positions/{position_id}", responses={403: {}, 404: {}, 422: {}, 423: {}}
)
def save_pay(
    request: Request,
    user: CurrentUser,
    version_code: str,
    cc_code: str,
    position_id: int,
    body: PayIn,
) -> PayPageOut:
    """A position's planned salary, bonus and (HR) the cost center charged."""
    engine = engine_of(request)
    try:
        given = personnel.PayIn(
            parse_amount(body.salary), parse_amount(body.bonus), body.charged_to or None
        )
        batch = personnel.save_pay(engine, user, version_code, cc_code, position_id, given)
        return _page_out(personnel.read_page(engine, user, version_code, cc_code), batch)
    except HANDLED as e:
        raise _errors(e) from None


@router.put(V + "/{cc_code}/review", responses={403: {}, 404: {}, 422: {}})
def review(
    request: Request, user: CurrentUser, version_code: str, cc_code: str, body: PayReviewIn
) -> PayPageOut:
    """HR confirms the cost center's pay, or takes the confirmation back."""
    engine = engine_of(request)
    try:
        personnel.confirm(engine, user, version_code, cc_code, body.confirmed)
        return _page_out(personnel.read_page(engine, user, version_code, cc_code))
    except HANDLED as e:
        raise _errors(e) from None
