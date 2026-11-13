"""The setup page (E-03, ADR 0007): create rounds, read and save a round's whole setup, and
seasonality profiles. Controllers only."""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from planlayer import rounds
from planlayer.access import AccessError
from planlayer.api.auth import CurrentUser, engine_of
from planlayer.api.planning import Version, version_out
from planlayer.domain.entry_forms import Form
from planlayer.domain.periods import fiscal_year_of
from planlayer.domain.spreading import Profile, SpreadError

router = APIRouter(prefix="/api")


def _http(e: Exception) -> HTTPException:
    match e:
        case AccessError():
            return HTTPException(403, str(e))
        case rounds.NotFoundError():
            return HTTPException(404, str(e))
        case rounds.RoundError() | SpreadError():
            return HTTPException(422, str(e))
    raise e


HANDLED = (AccessError, rounds.NotFoundError, rounds.RoundError, SpreadError)


class RuleIO(BaseModel):
    account_code: str
    form: Form
    profile_code: str | None = None
    reference_version_code: str | None = None


class ComparisonIO(BaseModel):
    kind: Literal["prior_year_actuals", "actuals", "version"]
    version_code: str | None = None
    # The controller's note to planners, shown with the column.
    note: Annotated[str | None, Field(max_length=200)] = None


class AccountSetIO(BaseModel):
    """A named list of accounts and account groups, assigned to cost center nodes or cost
    centers; the nearest assignment above a cost center wins (ADR 0014)."""

    name: str
    accounts: list[str]
    assigned_to: list[str] = []


class SetupIO(BaseModel):
    default_form: Form
    entry_forms: list[RuleIO]
    comparisons: list[ComparisonIO]
    not_planned_accounts: list[str]
    deadline: date | None
    productive_cost_centers: list[str] = []  # capacity planning (ADR 0013)
    # Cost centers planned in the round; null leaves them as they are.
    planned_cost_centers: list[str] | None = None
    account_sets: list[AccountSetIO] = []
    grain: Literal["month", "quarter"] = "month"  # ADR 0015
    actuals_through: date | None = None  # forecasts: the last closed month


class CarryOverIn(BaseModel):
    """What a new round takes from the round it is set up like (ADR 0014)."""

    setup: bool = True
    compare_with_source: bool = True
    scope: bool = True
    accounts: bool = True
    capacity: bool = True
    sheets: bool = True
    forwarding: bool = True  # cost forwardings (ADR 0018)
    values: bool = False
    uplift: Annotated[Decimal, Field(ge=-100, le=1000)] = Decimal(0)  # percent


class CreatedOut(BaseModel):
    """How the round was created; ``carried`` is null for a round set up from the defaults."""

    by: str | None
    at: datetime
    carried: CarryOverIn | None
    cells: int | None  # plan values copied; null: none were carried
    hours: int | None = None  # planned hour cells copied with them (ADR 0016)


class SetupOut(BaseModel):
    round: Version
    copied_from: str | None
    created: CreatedOut | None  # null: created before rounds kept a record
    setup: SetupIO
    actuals_available: bool
    enabled_cost_centers: int
    sample_cost_center: str | None
    periods: list[date]
    latest_actuals: date | None  # the latest month with synced actuals


class RoundIn(BaseModel):
    code: Annotated[str, Field(min_length=1, max_length=20)]
    name: Annotated[str, Field(min_length=1)]
    kind: Literal["budget", "forecast"]
    fiscal_year: Annotated[int, Field(ge=2000, le=2100)]
    deadline: date | None = None
    copy_from: str | None = None
    carry: CarryOverIn = CarryOverIn()
    grain: Literal["month", "quarter"] = "month"
    actuals_through: date | None = None  # forecasts: the last closed month (ADR 0015)


class LatestActualsOut(BaseModel):
    """What a new forecast proposes (ADR 0015), and the fiscal calendar to list its months."""

    month: date | None  # the latest month holding synced actuals; null: none
    fiscal_year: int | None  # the fiscal year of that month
    start_month: int  # the installation's first fiscal month (T-18)


class ScopeRowOut(BaseModel):
    code: str
    name: str
    depth: int
    is_group: bool
    parent: str | None
    planned: bool | None  # leaves only
    new: bool  # a cost center the source round didn't have
    missing: bool  # no longer delivered by the source
    prior_actuals: Decimal | None
    source_plan: Decimal | None


class ScopeOut(BaseModel):
    source_round: str | None
    cost_centers: list[ScopeRowOut]
    accounts: list[ScopeRowOut]


class ProfileIO(BaseModel):
    code: Annotated[str, Field(min_length=1)]
    name: Annotated[str, Field(min_length=1)]
    entered_as: Literal["percent", "relative"]
    values: Annotated[list[Decimal], Field(min_length=12, max_length=12)]


@router.post("/rounds", responses={403: {}, 404: {}, 422: {}})
def create_round(request: Request, user: CurrentUser, body: RoundIn) -> Version:
    engine = engine_of(request)
    try:
        rounds.create_round(
            engine,
            user,
            body.code,
            body.name,
            body.kind,
            body.fiscal_year,
            deadline=body.deadline,
            copy_from=body.copy_from,
            carry=rounds.CarryOver(**body.carry.model_dump()),
            grain=body.grain,
            actuals_through=body.actuals_through,
        )
        with engine.connect() as conn:
            r = rounds.get_round(conn, body.code)
    except HANDLED as e:
        raise _http(e) from None
    return version_out(r)


@router.get("/actuals/latest", responses={403: {}})
def latest_actuals(request: Request, user: CurrentUser) -> LatestActualsOut:
    """What a new forecast proposes as its actuals through (ADR 0015)."""
    try:
        user.require_controller()
        engine = engine_of(request)
        month = rounds.latest_actuals(engine, user)
        start = rounds.start_month(engine)
        return LatestActualsOut(
            month=month,
            fiscal_year=None if month is None else fiscal_year_of(month, start),
            start_month=start,
        )
    except HANDLED as e:
        raise _http(e) from None


@router.get("/rounds/{code}/setup", responses={403: {}, 404: {}})
def setup(request: Request, user: CurrentUser, code: str) -> SetupOut:
    try:
        v = rounds.setup_of(engine_of(request), user, code)
    except HANDLED as e:
        raise _http(e) from None
    r, s = v.round, v.setup
    return SetupOut(
        round=version_out(r),
        copied_from=v.copied_from,
        created=None
        if v.created is None or v.created.at is None
        else CreatedOut(
            by=v.created.by,
            at=v.created.at,
            carried=None
            if v.created.carried is None
            else CarryOverIn.model_validate(v.created.carried, from_attributes=True),
            cells=v.created.cells,
            hours=v.created.hours,
        ),
        setup=SetupIO(
            default_form=s.default_form,
            entry_forms=[
                RuleIO.model_validate(rule, from_attributes=True) for rule in s.entry_forms
            ],
            comparisons=[
                ComparisonIO(kind=c.kind, version_code=c.version_code, note=c.note)
                for c in s.comparisons
            ],
            not_planned_accounts=s.not_planned_accounts,
            deadline=s.deadline,
            productive_cost_centers=s.productive_cost_centers,
            planned_cost_centers=s.planned_cost_centers,
            account_sets=[
                AccountSetIO(name=a.name, accounts=a.accounts, assigned_to=a.assigned_to)
                for a in s.account_sets
            ],
            grain=s.grain,
            actuals_through=s.actuals_through,
        ),
        latest_actuals=v.latest_actuals,
        actuals_available=v.actuals_available,
        enabled_cost_centers=v.enabled_cost_centers,
        sample_cost_center=v.sample_cost_center,
        periods=v.periods,
    )


@router.put("/rounds/{code}/setup", responses={403: {}, 404: {}, 422: {}})
def save_setup(request: Request, user: CurrentUser, code: str, body: SetupIO) -> None:
    """Replace the round's setup; all or nothing."""
    try:
        rounds.save_setup(
            engine_of(request),
            user,
            code,
            rounds.Setup(
                default_form=body.default_form,
                entry_forms=[
                    rounds.EntryFormRule(
                        r.account_code, r.form, r.profile_code, r.reference_version_code
                    )
                    for r in body.entry_forms
                ],
                comparisons=[
                    rounds.Comparison(c.kind, c.version_code, c.note) for c in body.comparisons
                ],
                not_planned_accounts=body.not_planned_accounts,
                deadline=body.deadline,
                productive_cost_centers=body.productive_cost_centers,
                planned_cost_centers=body.planned_cost_centers,
                account_sets=[
                    rounds.AccountSet(a.name, a.accounts, a.assigned_to) for a in body.account_sets
                ],
                grain=body.grain,
                actuals_through=body.actuals_through,
            ),
        )
    except HANDLED as e:
        raise _http(e) from None


@router.get("/rounds/{code}/scope", responses={403: {}, 404: {}})
def scope(request: Request, user: CurrentUser, code: str) -> ScopeOut:
    """The cost center and account trees as the round's scope (ADR 0014)."""
    try:
        sc = rounds.scope_of(engine_of(request), user, code)
    except HANDLED as e:
        raise _http(e) from None
    return ScopeOut(
        source_round=sc.source_round,
        cost_centers=[ScopeRowOut.model_validate(r, from_attributes=True) for r in sc.cost_centers],
        accounts=[ScopeRowOut.model_validate(r, from_attributes=True) for r in sc.accounts],
    )


@router.get("/profiles")
def profiles(request: Request, user: CurrentUser) -> list[ProfileIO]:
    return [
        ProfileIO.model_validate(
            {"code": p.code, "name": p.name, "entered_as": p.entered_as, "values": p.values}
        )
        for p in rounds.profiles(engine_of(request))
    ]


@router.post("/profiles", responses={403: {}, 422: {}})
def add_profile(request: Request, user: CurrentUser, body: ProfileIO) -> None:
    try:
        profile = Profile(tuple(body.values), body.entered_as)
        rounds.add_profile(engine_of(request), user, body.code, body.name, profile)
    except HANDLED as e:
        raise _http(e) from None


@router.put("/profiles/{code}", responses={403: {}, 404: {}, 422: {}})
def update_profile(request: Request, user: CurrentUser, code: str, body: ProfileIO) -> None:
    """Applies to the next spread; months already stored keep their values. The code stays."""
    try:
        if body.code != code:
            raise rounds.RoundError("a profile's code can't change")
        profile = Profile(tuple(body.values), body.entered_as)
        rounds.update_profile(engine_of(request), user, code, body.name, profile)
    except HANDLED as e:
        raise _http(e) from None


@router.delete("/profiles/{code}", responses={403: {}, 404: {}, 422: {}})
def delete_profile(request: Request, user: CurrentUser, code: str) -> None:
    try:
        rounds.delete_profile(engine_of(request), user, code)
    except HANDLED as e:
        raise _http(e) from None


@router.get("/profile-presets/{preset}", responses={403: {}})
def profile_preset(
    request: Request,
    user: CurrentUser,
    preset: rounds.Preset,
    fiscal_year: Annotated[int, Query(ge=1900, le=2200)],
) -> list[Decimal]:
    """Starting values for a new profile, one per fiscal month of ``fiscal_year``."""
    try:
        return rounds.profile_preset(engine_of(request), user, preset, fiscal_year)
    except HANDLED as e:
        raise _http(e) from None
