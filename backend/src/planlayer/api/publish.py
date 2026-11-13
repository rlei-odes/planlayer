"""Publishing endpoints (ADR 0011): downloads, preview, run and history. Controllers only."""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import Engine

from planlayer import publish, rounds
from planlayer.access import AccessError
from planlayer.api.auth import CurrentUser, engine_of, settings_of

router = APIRouter(prefix="/api")
Scope = Literal["approved", "all"]


def _output(request: Request) -> tuple[Engine, str]:
    engine: Engine = request.app.state.output_engine
    return engine, settings_of(request).output_schema


def _http(e: Exception) -> HTTPException:
    match e:
        case AccessError():
            return HTTPException(403, str(e))
        case rounds.NotFoundError():
            return HTTPException(404, str(e))
        case publish.PublishError():
            return HTTPException(409, str(e))
    raise e


HANDLED = (AccessError, rounds.NotFoundError, publish.PublishError)


class CountsOut(BaseModel):
    code: str
    added: int
    changed: int
    removed: int
    unchanged: int


class ChangeOut(BaseModel):
    kind: Literal["added", "changed", "removed"]
    cost_center_code: str
    account_code: str
    period: date
    old: Decimal | None
    new: Decimal | None


class PublishPreviewOut(BaseModel):
    version_code: str
    scope: Scope
    rows: int
    added: int
    changed: int
    removed: int
    unchanged: int
    cost_centers: list[CountsOut]
    changes: list[ChangeOut]
    fingerprint: str
    hours_rows: int  # planned hours written to fact_plan_capacity (ADR 0016)
    workforce_rows: int  # FTE and headcount written to fact_plan_workforce (ADR 0020)


class PublishRunIn(BaseModel):
    scope: Scope
    fingerprint: str


class PublishRunOut(BaseModel):
    id: int
    scope: Scope
    actor: str | None
    started_at: datetime
    finished_at: datetime | None
    rows: int | None
    added: int | None
    changed: int | None
    removed: int | None


def _preview_out(p: publish.Preview) -> PublishPreviewOut:
    return PublishPreviewOut.model_validate(p, from_attributes=True)


@router.get("/rounds/{code}/publish/preview", responses={403: {}, 404: {}})
def preview(
    request: Request, user: CurrentUser, code: str, scope: Scope = "approved"
) -> PublishPreviewOut:
    """What a run would do; writes nothing."""
    output, schema = _output(request)
    try:
        return _preview_out(publish.preview(engine_of(request), output, schema, user, code, scope))
    except HANDLED as e:
        raise _http(e) from None


@router.post("/rounds/{code}/publish", responses={403: {}, 404: {}, 409: {}})
def run(request: Request, user: CurrentUser, code: str, body: PublishRunIn) -> PublishPreviewOut:
    """Replace the round's rows in the output, if nothing changed since the preview."""
    output, schema = _output(request)
    try:
        done = publish.run(
            engine_of(request), output, schema, user, code, body.scope, body.fingerprint
        )
    except HANDLED as e:
        raise _http(e) from None
    return _preview_out(done)


@router.get("/rounds/{code}/publish/runs", responses={403: {}, 404: {}})
def runs(request: Request, user: CurrentUser, code: str) -> list[PublishRunOut]:
    try:
        rows = publish.history(engine_of(request), user, code)
    except HANDLED as e:
        raise _http(e) from None
    return [PublishRunOut.model_validate(r, from_attributes=True) for r in rows]


@router.get("/rounds/{code}/export.{fmt}", responses={403: {}, 404: {}})
def export(
    request: Request,
    user: CurrentUser,
    code: str,
    fmt: Literal["csv", "parquet"],
    scope: Scope = "approved",
) -> Response:
    """The round's plan values in the scope as a file; writes nothing."""
    try:
        if fmt == "csv":
            body, media = publish.csv_bytes(engine_of(request), user, code, scope), "text/csv"
        else:
            body = publish.parquet_bytes(engine_of(request), user, code, scope)
            media = "application/vnd.apache.parquet"
    except HANDLED as e:
        raise _http(e) from None
    name = f"{code}-{scope}.{fmt}"
    return Response(
        body, media_type=media, headers={"Content-Disposition": f'attachment; filename="{name}"'}
    )
