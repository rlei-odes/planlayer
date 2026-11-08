"""Planning endpoints: versions, the cost center switcher, the grid and its edits.

Amounts travel as strings (T-12); ``null`` is a blank cell, distinct from ``"0.00"``.
Until login exists (E-09), every change is recorded with the actor ``dev``.
"""

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import Engine

from planlayer import planning

router = APIRouter(prefix="/api")
ACTOR = "dev"


def _engine(request: Request) -> Engine:
    engine: Engine = request.app.state.engine
    return engine


def _s(value: Decimal | None) -> str | None:
    return None if value is None else f"{value:.2f}"


class Version(BaseModel):
    code: str
    name: str
    kind: str
    fiscal_year: int


class PlanningUnit(BaseModel):
    code: str
    name: str
    entity_code: str | None
    owner: str | None


class CellOut(BaseModel):
    value: str | None
    row_version: int | None


class RowOut(BaseModel):
    kind: Literal["group", "account"]
    code: str
    name: str
    group_code: str
    prior_year: list[str | None]
    plan: list[CellOut]


class GridOut(BaseModel):
    version_code: str
    version_name: str
    fiscal_year: int
    cost_center_code: str
    cost_center_name: str
    periods: list[date]
    prior_periods: list[date]
    rows: list[RowOut]


class EditIn(BaseModel):
    account_code: str
    month: Annotated[int, Field(ge=0, le=11)] | None = None
    value: str | None
    expected: Annotated[list[int | None], Field(min_length=12, max_length=12)]


class EditsIn(BaseModel):
    edits: Annotated[list[EditIn], Field(min_length=1, max_length=20_000)]


class ConflictOut(BaseModel):
    account_code: str
    month: int


def _grid_out(grid: planning.Grid) -> GridOut:
    return GridOut(
        version_code=grid.version_code,
        version_name=grid.version_name,
        fiscal_year=grid.fiscal_year,
        cost_center_code=grid.cost_center_code,
        cost_center_name=grid.cost_center_name,
        periods=grid.periods,
        prior_periods=grid.prior_periods,
        rows=[
            RowOut(
                kind=r.kind,
                code=r.code,
                name=r.name,
                group_code=r.group_code,
                prior_year=[_s(v) for v in r.prior_year],
                plan=[CellOut(value=_s(c.value), row_version=c.row_version) for c in r.plan],
            )
            for r in grid.rows
        ],
    )


@router.get("/versions")
def versions(request: Request) -> list[Version]:
    return [Version.model_validate(v) for v in planning.plan_versions(_engine(request))]


@router.get("/versions/{version_code}/cost-centers")
def cost_centers(request: Request, version_code: str) -> list[PlanningUnit]:
    try:
        units = planning.planning_units(_engine(request), version_code)
    except planning.NotFoundError as e:
        raise HTTPException(404, str(e)) from None
    return [PlanningUnit.model_validate(u) for u in units]


@router.get("/versions/{version_code}/cost-centers/{cc_code}/grid")
def grid(request: Request, version_code: str, cc_code: str) -> GridOut:
    try:
        return _grid_out(planning.read_grid(_engine(request), version_code, cc_code))
    except planning.NotFoundError as e:
        raise HTTPException(404, str(e)) from None


@router.post(
    "/versions/{version_code}/cost-centers/{cc_code}/edits",
    responses={409: {"model": list[ConflictOut]}, 422: {}},
)
def edit(request: Request, version_code: str, cc_code: str, body: EditsIn) -> GridOut:
    """Apply edits atomically; returns the updated grid, or 409 with the conflicting cells."""
    engine = _engine(request)
    edits = [planning.Edit(e.account_code, e.month, e.value, e.expected) for e in body.edits]
    try:
        planning.apply_edits(engine, ACTOR, version_code, cc_code, edits)
    except planning.NotFoundError as e:
        raise HTTPException(404, str(e)) from None
    except planning.EditError as e:
        raise HTTPException(422, str(e)) from None
    except planning.ConflictError as e:
        return JSONResponse(  # type: ignore[return-value]
            status_code=409,
            content=[{"account_code": c.account_code, "month": c.month} for c in e.conflicts],
        )
    return _grid_out(planning.read_grid(engine, version_code, cc_code))
