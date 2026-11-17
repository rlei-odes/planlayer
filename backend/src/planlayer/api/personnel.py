"""Personnel cost endpoints (ADR 0022): a cost center's pay for HR and its owner, HR's review,
the round's rules for HR and controllers, and turning the module on. Amounts are decimal
strings."""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, Literal, cast

from fastapi import APIRouter, Body, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from planlayer import personnel
from planlayer.api.auth import CurrentUser, engine_of
from planlayer.api.capacity import CodeName
from planlayer.api.limits import MANY, Code, Name, Number
from planlayer.api.planning import HANDLED, _errors, _s
from planlayer.domain.workflow import RoundStatus, Status
from planlayer.personnel import upload
from planlayer.personnel.rules import Kind
from planlayer.personnel.salary_files import MEDIA
from planlayer.sheet import EditError, fits, parse_amount

router = APIRouter(prefix="/api")


class PayPartOut(BaseModel):
    source: Literal["pay", "bonus", "rule"]
    rule_id: int | None
    year: str


class PayRuleRefOut(BaseModel):
    id: int
    kind: Kind
    name: str
    account: str
    entity: str | None


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
    entity: str  # of its cost center; entity-limited rules apply by it
    salary_account: str | None  # of its staff type; null: not costed
    excluded_rules: list[int]  # rules HR excluded it from (ADR 0026)
    parts: list[PayPartOut]  # its costs over the year by where they come from


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
    released: bool  # access control released (ADR 0023)
    handover: PayReviewOut | None  # HR handed the pay over to its readers (ADR 0024)
    review_required: bool  # approval waits for HR's review
    rules: list[PayRuleRefOut]  # the round's rules, for the positions' breakdown
    bonus_account: str | None
    batch_id: str | None = None


class PayUnitOut(BaseModel):
    code: str
    name: str
    status: Status
    positions: int
    small_groups: int
    confirmed: bool
    handed_over: bool


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
    readers: Annotated[list[Code], MANY]  # logins
    owner_decided: bool = False  # the source's current owner counts as decided on


class PayReleaseIn(BaseModel):
    released: bool


class PayIn(BaseModel):
    salary: Number | None = None  # null or blank: HR's
    bonus: Number | None = None
    charged_to: Code | None = None
    # HR only (ADR 0023); left out, HR's salary stays as it is.
    hr_salary: Number | None = None
    # HR only (ADR 0026); left out, the exclusions stay as they are.
    excluded_rules: Annotated[list[int], MANY] | None = None


class PayReviewIn(BaseModel):
    confirmed: bool


class PayHandoverIn(BaseModel):
    handed_over: bool


class PayStepIO(BaseModel):
    years: int
    salaries: Number | None = None
    amount: Number | None = None


class PayRuleIO(BaseModel):
    id: int | None = None  # the stored rule; null: a new one
    kind: Kind
    name: Name
    account: Code
    entity: Code | None = None
    salaries: Number | None = None  # extra: salaries per year
    percent: Number | None = None  # raise, contribution
    ceiling: Number | None = None  # contribution: monthly
    starts: date | None = None  # raise: its first month
    steps: Annotated[list[PayStepIO], MANY]  # award; empty for the other kinds


class PayStaffTypeIO(BaseModel):
    code: Code
    name: Name = ""
    external: bool = False
    account: Code | None  # null: not costed
    typed: bool


class PayRulesIO(BaseModel):
    min_group: int
    review_required: bool  # HR's review before approval; HR switches it (ADR 0024)
    bonus_account: Code | None
    staff_types: Annotated[list[PayStaffTypeIO], MANY]
    rules: Annotated[list[PayRuleIO], MANY]


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


class SalaryUploadInfoOut(BaseModel):
    round_code: str
    round_status: RoundStatus
    pay_from_source: bool  # the sync reads HR's salaries; HR doesn't upload them
    people: int  # rows of the template
    with_salary: int  # the round's positions from HR that have HR's salary
    reason: str | None  # why HR can't upload now


class SalaryUploadRowOut(BaseModel):
    line: int
    number: str | None
    name: str | None
    cost_center: str | None
    file_cost_center: str | None  # the file's, when it differs
    old: str | None
    new: str | None
    outcome: Literal["changed", "unchanged", "blank", "not_in_round", "unknown", "failed"]
    message: str | None
    old_bonus: str | None  # the position's yearly bonus now
    new_bonus: str | None


class SalaryUploadOut(BaseModel):
    round_code: str
    applied: bool  # False: the check, nothing written
    rows: list[SalaryUploadRowOut]
    ignored: list[str]
    positions: int  # positions whose HR salary or bonus changes
    staff_list: int  # people whose salary in HR's staff list changes
    batch_id: str | None = None


def _upload_out(r: upload.UploadResult) -> SalaryUploadOut:
    return SalaryUploadOut(
        round_code=r.round_code,
        applied=r.applied,
        rows=[
            SalaryUploadRowOut(
                line=x.line,
                number=x.number,
                name=x.name,
                cost_center=x.cost_center,
                file_cost_center=x.file_cost_center,
                old=_s(x.old),
                new=_s(x.new),
                outcome=x.outcome,
                message=x.message,
                old_bonus=_s(x.old_bonus),
                new_bonus=_s(x.new_bonus),
            )
            for x in r.rows
        ],
        ignored=r.ignored,
        positions=r.positions,
        staff_list=r.staff_list,
        batch_id=r.batch_id,
    )


# The uploaded file as the request body, as it is.
FileBody = Annotated[bytes, Body(media_type="application/octet-stream")]
FileName = Annotated[str, Field(max_length=255)]  # the uploaded file's name


def _position_out(r: personnel.PayRow) -> PayPositionOut:
    return PayPositionOut(
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
        entity=r.entity,
        salary_account=r.salary_account,
        excluded_rules=r.excluded_rules,
        parts=[
            PayPartOut(source=x.source, rule_id=x.rule_id, year=f"{x.year:.2f}") for x in r.parts
        ],
    )


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
        positions=[_position_out(r) for r in p.positions],
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
        released=p.released,
        handover=(
            PayReviewOut(confirmed_by=p.handover.confirmed_by, confirmed_at=p.handover.confirmed_at)
            if p.handover
            else None
        ),
        review_required=p.review_required,
        rules=[
            PayRuleRefOut(
                id=x.rule_id,
                kind=cast(Kind, x.kind),
                name=x.name,
                account=x.account,
                entity=x.entity,
            )
            for x in p.rules
        ],
        bonus_account=p.bonus_account,
        batch_id=batch_id,
    )


def _number(value: str | None, what: str, precision: int) -> Decimal | None:
    if value is None or not value.strip():
        return None
    text = value.strip().removesuffix("%").strip().replace(",", ".")
    try:
        number = Decimal(text)
    except InvalidOperation:
        raise EditError(f"not a {what}: {value!r}") from None
    if not number.is_finite():
        raise EditError(f"not a {what}: {value!r}")
    if not fits(number, precision, 4):
        raise EditError(f"{what} {value} is too large")
    return number


def _rules_in(body: PayRulesIO) -> personnel.RulesIO:
    return personnel.RulesIO(
        min_group=body.min_group,
        review_required=body.review_required,
        bonus_account=body.bonus_account or None,
        staff_types=[
            personnel.StaffTypeIO(t.code, t.account or None, t.typed) for t in body.staff_types
        ],
        rules=[
            personnel.RuleIO(
                r.kind,
                r.name,
                r.account,
                r.entity or None,
                _number(r.salaries, "number of salaries", 8),
                _number(r.percent, "percentage", 7),
                parse_amount(r.ceiling),
                r.starts,
                [
                    personnel.StepIO(
                        s.years,
                        _number(s.salaries, "number of salaries", 8),
                        parse_amount(s.amount),
                    )
                    for s in r.steps
                ],
                r.id,
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
            review_required=r.review_required,
            bonus_account=r.bonus_account,
            staff_types=[
                PayStaffTypeIO(
                    code=t.code, name=t.name, external=t.external, account=t.account, typed=t.typed
                )
                for t in r.staff_types
            ],
            rules=[
                PayRuleIO(
                    id=x.id,
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
                handed_over=u.handed_over,
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


# HR's salary upload (ADR 0025); before the cost center's routes, whose code would match.


@router.get(V + "/salaries", responses={403: {}, 404: {}})
def salary_upload(request: Request, user: CurrentUser, version_code: str) -> SalaryUploadInfoOut:
    try:
        i = upload.info(engine_of(request), user, version_code)
    except HANDLED as e:
        raise _errors(e) from None
    return SalaryUploadInfoOut(
        round_code=i.round_code,
        round_status=i.round_status,  # type: ignore[arg-type]
        pay_from_source=i.pay_from_source,
        people=i.people,
        with_salary=i.with_salary,
        reason=i.reason,
    )


@router.get(V + "/salaries/template.{fmt}", responses={403: {}, 404: {}})
def salary_template(
    request: Request, user: CurrentUser, version_code: str, fmt: Literal["csv", "xlsx", "parquet"]
) -> Response:
    """The round's people with a blank salary column, for HR to fill in."""
    try:
        body = upload.template_file(engine_of(request), user, version_code, fmt)
    except HANDLED as e:
        raise _errors(e) from None
    name = f"{version_code}-salaries.{fmt}"
    return Response(
        body,
        media_type=MEDIA[fmt],
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.post(V + "/salaries/check", responses={403: {}, 404: {}, 422: {}})
def check_salaries(
    request: Request, user: CurrentUser, version_code: str, filename: FileName, file: FileBody
) -> SalaryUploadOut:
    """What the uploaded file would change; writes nothing."""
    try:
        return _upload_out(upload.check(engine_of(request), user, version_code, file, filename))
    except HANDLED as e:
        raise _errors(e) from None


@router.post(V + "/salaries/apply", responses={403: {}, 404: {}, 422: {}, 423: {}})
def apply_salaries(
    request: Request, user: CurrentUser, version_code: str, filename: FileName, file: FileBody
) -> SalaryUploadOut:
    """Write the uploaded file's salaries as the check shows them; failed rows are left out."""
    try:
        return _upload_out(upload.apply(engine_of(request), user, version_code, file, filename))
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
        batch = personnel.save_pay(engine, user, version_code, cc_code, position_id, _pay_in(body))
        return _page_out(personnel.read_page(engine, user, version_code, cc_code), batch)
    except HANDLED as e:
        raise _errors(e) from None


@router.post(
    V + "/{cc_code}/positions/{position_id}/preview", responses={403: {}, 404: {}, 422: {}}
)
def preview_pay(
    request: Request,
    user: CurrentUser,
    version_code: str,
    cc_code: str,
    position_id: int,
    body: PayIn,
) -> PayPositionOut:
    """The position's costs as they would be with ``body``; writes nothing."""
    try:
        row = personnel.preview(
            engine_of(request), user, version_code, cc_code, position_id, _pay_in(body)
        )
    except HANDLED as e:
        raise _errors(e) from None
    return _position_out(row)


def _pay_in(body: PayIn) -> personnel.PayIn:
    return personnel.PayIn(
        parse_amount(body.salary),
        parse_amount(body.bonus),
        body.charged_to or None,
        parse_amount(body.hr_salary),
        "hr_salary" in body.model_fields_set,
        body.excluded_rules,
    )


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


@router.put(V + "/{cc_code}/handover", responses={403: {}, 404: {}, 422: {}})
def handover(
    request: Request, user: CurrentUser, version_code: str, cc_code: str, body: PayHandoverIn
) -> PayPageOut:
    """HR hands the cost center's pay over to its readers, or takes it back (ADR 0024)."""
    engine = engine_of(request)
    try:
        personnel.hand_over(engine, user, version_code, cc_code, body.handed_over)
        return _page_out(personnel.read_page(engine, user, version_code, cc_code))
    except HANDLED as e:
        raise _errors(e) from None
