"""Master data endpoints (ADR 0012): dataset mappings and previews (admin), sync plan and run
(admin, controller), the member tree with manual members and overrides (controller, admin)."""

from datetime import date
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from planlayer import masterdata, sync
from planlayer.access import AccessError, User
from planlayer.adapters.source import Query, SourceAdapter
from planlayer.api.auth import CurrentUser, engine_of

router = APIRouter(prefix="/api")
Dataset = Literal["entities", "cost_centers", "accounts", "actuals"]
MemberDataset = Literal["entities", "cost_centers", "accounts"]
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
    type: str
    required: bool


class MappingIO(BaseModel):
    query: str
    fields: dict[str, str]
    hierarchy: dict[str, Any] | None = None


class DatasetOut(BaseModel):
    dataset: Dataset
    fields: list[FieldOut]
    has_hierarchy: bool
    mapping: MappingIO | None


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
    return [
        DatasetOut(
            dataset=d,  # type: ignore[arg-type]
            fields=[
                FieldOut(name=f, type=str(t), required=f in sync.REQUIRED[d])
                for f, t in sync.FIELDS[d].items()
            ],
            has_hierarchy=sync.DIMENSIONS.get(d, (0, None))[1] is not None,
            mapping=MappingIO(
                query=maps[d].query, fields=maps[d].fields, hierarchy=maps[d].hierarchy
            )
            if d in maps
            else None,
        )
        for d in sync.DATASETS
    ]


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
        read = sync.read(source, m, PREVIEW_ROWS)
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


class SyncIn(BaseModel):
    entity: str | None = None


class SyncRunIn(SyncIn):
    confirm: list[str] = []


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


class SyncRunOut(BaseModel):
    members: int
    actual_rows: int
    matched: list[str]


@router.post("/sync/plan", responses={403: {}})
def sync_plan(request: Request, user: CurrentUser, body: SyncIn) -> PlanOut:
    """What a sync would do; writes nothing."""
    try:
        _syncer(user)
    except HANDLED as e:
        raise _http(e) from None
    p = sync.plan(engine_of(request), _source(request), body.entity)
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
    )


@router.post("/sync/run", responses={403: {}, 422: {}})
def sync_run(request: Request, user: CurrentUser, body: SyncRunIn) -> SyncRunOut:
    try:
        _syncer(user)
        r = sync.run(engine_of(request), _source(request), user, body.entity, body.confirm)
    except HANDLED as e:
        raise _http(e) from None
    return SyncRunOut(members=r.members, actual_rows=r.actual_rows, matched=r.matched)


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


class MemberIn(BaseModel):
    code: Annotated[str, Field(min_length=1)]
    name: Annotated[str, Field(min_length=1)]
    parent: str | None = None
    is_group: bool = False
    entity_code: str | None = None
    owner: str | None = None
    account_type: str | None = None


class MemberChange(BaseModel):
    """Only the given keys change; ``parent: null`` makes the member a root."""

    name: str | None = None
    parent: str | None = None
    entity_code: str | None = None
    owner: str | None = None
    account_type: str | None = None


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
        if given & {"entity_code", "owner", "account_type"}:
            masterdata.set_attributes(
                engine,
                user,
                dataset,
                code,
                entity_code=body.entity_code,
                owner=body.owner,
                account_type=body.account_type,
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
