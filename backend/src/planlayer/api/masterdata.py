"""Master data endpoints (ADR 0012): dataset mappings and previews (admin), sync plan and run
(admin, controller), the member tree with manual members and overrides (controller, admin)."""

from datetime import date
from typing import Annotated, Any, Literal

import pyarrow as pa
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from planlayer import masterdata, sync
from planlayer.access import AccessError, User
from planlayer.adapters.source import Query, SourceAdapter
from planlayer.api.auth import CurrentUser, engine_of

router = APIRouter(prefix="/api")
Dataset = Literal[
    "entities",
    "cost_centers",
    "accounts",
    "actuals",
    "activity_types",
    "work_centers",
    "work_center_rates",
    "capacity_actuals",
    "staff_types",
    "staff_categories",
    "staff",
    "workforce_actuals",
]
MemberDataset = Literal[
    "entities",
    "cost_centers",
    "accounts",
    "activity_types",
    "work_centers",
    "staff_types",
    "staff_categories",
]
REFERENCE_FIELDS = ("cost_center_code", "account_code", "variable_account_code")
PREVIEW_ROWS = 20


def _source(request: Request) -> SourceAdapter:
    source: SourceAdapter = request.app.state.source
    return source


def _http(e: Exception) -> HTTPException:
    match e:
        case AccessError():
            return HTTPException(403, str(e))
        case sync.MappingError() | masterdata.MasterDataError():
            return HTTPException(422, str(e))
        case sync.SyncError():
            return HTTPException(422, {"problems": e.problems})
    raise e


HANDLED = (AccessError, sync.MappingError, masterdata.MasterDataError, sync.SyncError)


def _admin(user: User) -> None:
    user.require_admin()


def _syncer(user: User) -> None:
    if not (user.is_admin or user.is_controller):
        raise AccessError("only an admin or controller may sync")


# --- Datasets and mappings ------------------------------------------------------------------------


class FieldOut(BaseModel):
    name: str
    type: str  # what the source column must hold, e.g. "date", "text", "decimal"
    required: bool


def _type_text(t: pa.DataType) -> str:
    """A contract type as the admin reads it."""
    if pa.types.is_date(t):
        return "date"
    if pa.types.is_decimal(t):
        return "decimal"
    if pa.types.is_integer(t):
        return "whole number"
    if pa.types.is_boolean(t):
        return "true/false"
    return "text"


class MappingIO(BaseModel):
    query: str
    fields: dict[str, str]
    hierarchy: dict[str, Any] | None = None


class WindowOut(BaseModel):
    """Fiscal years and the periods they span: from ``period_from`` up to, not including,
    ``before``."""

    first: int
    last: int
    period_from: date
    before: date


def _window(w: sync.Window | None) -> WindowOut | None:
    if w is None:
        return None
    return WindowOut(first=w.first, last=w.last, period_from=w.period_from, before=w.after)


class DatasetOut(BaseModel):
    dataset: Dataset
    fields: list[FieldOut]
    has_hierarchy: bool
    mapping: MappingIO | None
    # Values per period: the window a sync reads by default, filtered on the period column.
    window: WindowOut | None = None
    # Staff (ADR 0025): its pay fields, and whether the sync reads them; off, HR uploads pay.
    pay_fields: list[str]
    pay_from_source: bool | None


class PayFromSourceIn(BaseModel):
    on: bool


class QueryPreviewIn(MappingIO):
    mapped: bool = False  # False: the query's own columns; True: cast to the fields


class QueryPreviewOut(BaseModel):
    columns: list[str]
    rows: list[list[str | None]]
    error: str | None = None


@router.get("/datasets", responses={403: {}})
def datasets(request: Request, user: CurrentUser) -> list[DatasetOut]:
    try:
        _admin(user)
    except HANDLED as e:
        raise _http(e) from None
    with engine_of(request).connect() as conn:
        maps = sync.mappings(conn)
        default = _window(sync.window_of(conn, None))
        pay = sync.pay_from_source(conn)
    return [
        DatasetOut(
            dataset=d,  # type: ignore[arg-type]
            fields=[
                FieldOut(name=f, type=_type_text(t), required=f in sync.REQUIRED[d])
                for f, t in sync.FIELDS[d].items()
            ],
            has_hierarchy=sync.DIMENSIONS.get(d, (0, None))[1] is not None,
            mapping=MappingIO(
                query=maps[d].query, fields=maps[d].fields, hierarchy=maps[d].hierarchy
            )
            if d in maps
            else None,
            window=default if d in sync.PERIOD_DATASETS else None,
            pay_fields=list(sync.PAY_FIELDS) if d == "staff" else [],
            pay_from_source=pay if d == "staff" else None,
        )
        for d in sync.DATASETS
    ]


@router.put("/datasets/staff/pay-from-source", responses={403: {}})
def set_pay_from_source(request: Request, user: CurrentUser, body: PayFromSourceIn) -> None:
    """Whether the sync reads HR's salaries with the staff list; off, HR uploads them."""
    try:
        _admin(user)
    except HANDLED as e:
        raise _http(e) from None
    sync.set_pay_from_source(engine_of(request), user, body.on)


@router.put("/datasets/{dataset}/mapping", responses={403: {}, 422: {}})
def save_mapping(request: Request, user: CurrentUser, dataset: Dataset, body: MappingIO) -> None:
    try:
        _admin(user)
        sync.save_mapping(
            engine_of(request),
            user,
            sync.Mapping(
                dataset, body.query, {f: c for f, c in body.fields.items() if c}, body.hierarchy
            ),
        )
    except HANDLED as e:
        raise _http(e) from None


@router.delete("/datasets/{dataset}/mapping", responses={403: {}})
def delete_mapping(request: Request, user: CurrentUser, dataset: Dataset) -> None:
    try:
        _admin(user)
    except HANDLED as e:
        raise _http(e) from None
    sync.delete_mapping(engine_of(request), user, dataset)


@router.post("/datasets/{dataset}/preview", responses={403: {}})
def preview(
    request: Request, user: CurrentUser, dataset: Dataset, body: QueryPreviewIn
) -> QueryPreviewOut:
    """The query's first rows; with ``mapped``, as the sync would read them. A source error is
    returned as ``error``, so the admin can fix the query."""
    try:
        _admin(user)
    except HANDLED as e:
        raise _http(e) from None
    source = _source(request)
    try:
        if not body.mapped:
            columns, rows = source.preview(Query(body.query), PREVIEW_ROWS)
            return QueryPreviewOut(columns=columns, rows=rows)
        m = sync.Mapping(
            dataset, body.query, {f: c for f, c in body.fields.items() if c}, body.hierarchy
        )
        m.validate()
        # Values per period read the default window, as a sync would; pay only when the sync
        # reads it (ADR 0025).
        with engine_of(request).connect() as conn:
            window = sync.window_of(conn, None)
            if dataset == "staff":
                m = sync.staff_mapping(m, sync.pay_from_source(conn))
        read = sync.read(source, m, PREVIEW_ROWS, window)
        columns = list(m.columns())
        return QueryPreviewOut(columns=columns, rows=[[_text(r[c]) for c in columns] for r in read])
    except sync.MappingError as e:
        return QueryPreviewOut(columns=[], rows=[], error=str(e))
    except sync.SOURCE_ERRORS as e:
        return QueryPreviewOut(
            columns=[], rows=[], error=str(getattr(e, "orig", None) or e).splitlines()[0]
        )


def _text(value: object) -> str | None:
    if value is None:
        return None
    return value.isoformat() if isinstance(value, date) else str(value)


# --- Sync -----------------------------------------------------------------------------------------


class YearsIO(BaseModel):
    """Fiscal years ``first`` to ``last``, both included."""

    first: int
    last: int


class YearOut(BaseModel):
    fiscal_year: int
    stored: int  # actual cells stored now


class SyncYearsOut(BaseModel):
    default: WindowOut | None  # None: no round in setup or open, so every year
    years: list[YearOut]


@router.get("/sync/years", responses={403: {}})
def sync_years(request: Request, user: CurrentUser) -> SyncYearsOut:
    """The default window of a sync, and the fiscal years to choose from, with their actuals."""
    try:
        _syncer(user)
    except HANDLED as e:
        raise _http(e) from None
    y = sync.years(engine_of(request))
    return SyncYearsOut(
        default=_window(y.default),
        years=[YearOut(fiscal_year=k, stored=n) for k, n in sorted(y.stored.items())],
    )


class SyncIn(BaseModel):
    entity: str | None = None
    # Fiscal years whose actuals and actual hours are replaced; None: the default window, the
    # years the setup and open rounds compare with.
    years: YearsIO | None = None

    def window(self) -> tuple[int, int] | None:
        return (self.years.first, self.years.last) if self.years else None


class SyncRunIn(SyncIn):
    confirm: list[str] = []


class YearCountOut(BaseModel):
    fiscal_year: int
    stored: int  # actual cells stored now
    delivered: int  # actual cells the source delivers


class MatchOut(BaseModel):
    key: str
    dimension: str
    code: str
    name: str


class PlanOut(BaseModel):
    datasets: list[str]
    problems: list[str]
    new: list[str]
    changed: list[str]
    gone: list[str]
    matches: list[MatchOut]
    actual_cells: int | None
    years: YearsIO | None  # the window used; None: every year
    actual_years: list[YearCountOut]


class SyncRunOut(BaseModel):
    members: int
    actual_rows: int
    matched: list[str]
    seconds: float  # how long the run took


@router.post("/sync/plan", responses={403: {}})
def sync_plan(request: Request, user: CurrentUser, body: SyncIn) -> PlanOut:
    """What a sync would do; writes nothing."""
    try:
        _syncer(user)
        p = sync.plan(engine_of(request), _source(request), body.entity, body.window())
    except HANDLED as e:
        raise _http(e) from None
    return PlanOut(
        datasets=p.datasets,
        problems=p.problems,
        new=p.new,
        changed=p.changed,
        gone=p.gone,
        matches=[
            MatchOut(key=m.key, dimension=m.dimension, code=m.code, name=m.name) for m in p.matches
        ],
        actual_cells=p.actual_cells,
        years=YearsIO(first=p.window.first, last=p.window.last) if p.window else None,
        actual_years=[
            YearCountOut(fiscal_year=y.fiscal_year, stored=y.stored, delivered=y.delivered)
            for y in p.years
        ],
    )


@router.post("/sync/run", responses={403: {}, 422: {}})
def sync_run(request: Request, user: CurrentUser, body: SyncRunIn) -> SyncRunOut:
    try:
        _syncer(user)
        r = sync.run(
            engine_of(request), _source(request), user, body.entity, body.confirm, body.window()
        )
    except HANDLED as e:
        raise _http(e) from None
    return SyncRunOut(
        members=r.members, actual_rows=r.actual_rows, matched=r.matched, seconds=r.seconds
    )


# --- Members --------------------------------------------------------------------------------------


class MemberOut(BaseModel):
    code: str
    name: str
    source_name: str | None
    origin: Literal["source", "manual"]
    is_group: bool
    depth: int
    parent: str | None
    source_parent: str | None
    renamed: bool
    moved: bool
    entity_code: str | None
    owner: str | None
    account_type: str | None
    valid_from: date | None
    valid_to: date | None
    cost_center_code: str | None  # work centers
    account_code: str | None  # activity types: the account credited
    variable_account_code: str | None
    external: bool | None = None  # staff types: external staff (ADR 0020)


class MemberIn(BaseModel):
    code: Annotated[str, Field(min_length=1)]
    name: Annotated[str, Field(min_length=1)]
    parent: str | None = None
    is_group: bool = False
    entity_code: str | None = None
    owner: str | None = None
    account_type: str | None = None
    cost_center_code: str | None = None
    account_code: str | None = None
    variable_account_code: str | None = None
    external: bool | None = None  # staff types


class MemberChange(BaseModel):
    """Only the given keys change; ``parent: null`` makes the member a root."""

    name: str | None = None
    parent: str | None = None
    entity_code: str | None = None
    owner: str | None = None
    account_type: str | None = None
    cost_center_code: str | None = None
    account_code: str | None = None
    variable_account_code: str | None = None
    external: bool | None = None


@router.get("/members/{dataset}", responses={403: {}})
def members(request: Request, user: CurrentUser, dataset: MemberDataset) -> list[MemberOut]:
    try:
        rows = masterdata.tree(engine_of(request), user, dataset)
    except HANDLED as e:
        raise _http(e) from None
    return [MemberOut.model_validate(r, from_attributes=True) for r in rows]


@router.post("/members/{dataset}", responses={403: {}, 422: {}})
def add_member(request: Request, user: CurrentUser, dataset: MemberDataset, body: MemberIn) -> None:
    try:
        masterdata.add_member(
            engine_of(request),
            user,
            dataset,
            body.code,
            body.name,
            parent=body.parent,
            is_group=body.is_group,
            entity_code=body.entity_code,
            owner=body.owner,
            account_type=body.account_type,
            references={f: getattr(body, f) for f in REFERENCE_FIELDS},
            external=body.external,
        )
    except HANDLED as e:
        raise _http(e) from None


@router.patch("/members/{dataset}/{code}", responses={403: {}, 422: {}})
def change_member(
    request: Request, user: CurrentUser, dataset: MemberDataset, code: str, body: MemberChange
) -> None:
    engine, given = engine_of(request), body.model_fields_set
    try:
        if "name" in given and body.name is not None:
            masterdata.rename(engine, user, dataset, code, body.name)
        if "parent" in given:
            masterdata.move(engine, user, dataset, code, body.parent)
        if given & {"entity_code", "owner", "account_type", "external", *REFERENCE_FIELDS}:
            masterdata.set_attributes(
                engine,
                user,
                dataset,
                code,
                entity_code=body.entity_code,
                owner=body.owner,
                account_type=body.account_type,
                references={f: getattr(body, f) for f in REFERENCE_FIELDS},
                external=body.external,
            )
    except HANDLED as e:
        raise _http(e) from None


@router.delete("/members/{dataset}/{code}/override", responses={403: {}, 422: {}})
def reset_member(request: Request, user: CurrentUser, dataset: MemberDataset, code: str) -> None:
    """Back to the source's name and parent."""
    try:
        masterdata.reset(engine_of(request), user, dataset, code)
    except HANDLED as e:
        raise _http(e) from None


@router.delete("/members/{dataset}/{code}", responses={403: {}, 422: {}})
def delete_member(request: Request, user: CurrentUser, dataset: MemberDataset, code: str) -> None:
    try:
        masterdata.delete_member(engine_of(request), user, dataset, code)
    except HANDLED as e:
        raise _http(e) from None
