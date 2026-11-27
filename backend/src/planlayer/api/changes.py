"""The round's change log for controllers: change batches newest first, and one batch's lines."""

from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from planlayer import changes
from planlayer.api.auth import CurrentUser, engine_of
from planlayer.api.planning import HANDLED, _errors

router = APIRouter(prefix="/api")

CodeQuery = Annotated[str | None, Query(max_length=100)]


class PersonOut(BaseModel):
    login: str
    name: str


class CostCenterOut(BaseModel):
    code: str
    name: str


class BatchOut(BaseModel):
    batch_id: str
    at: datetime
    actor: PersonOut
    sheets: list[changes.Sheet]
    sources: list[changes.Source]
    cost_centers: list[CostCenterOut]  # the first three, by code
    cost_center_count: int
    cells: int
    cleared: int  # cells blanked
    calculated: bool  # written by the app from other inputs, or by the system user


class ChangesOut(BaseModel):
    batches: list[BatchOut]
    counts: dict[changes.Kind, int]
    cursor: str | None  # pass back for the next page; null: the last page
    users: list[PersonOut]
    cost_centers: list[CostCenterOut]


class LineOut(BaseModel):
    sheet: changes.Sheet
    cost_center: CostCenterOut
    code: str
    name: str
    unit: changes.Unit
    source: changes.Source
    months: list[date]  # the months changed
    # Amounts and hours: the changed months' sum; FTE and headcount: the last changed month.
    before: str | None
    after: str | None


class BatchDetailOut(BaseModel):
    batch_id: str
    lines: list[LineOut]
    total_lines: int


def _person(p: changes.Person) -> PersonOut:
    return PersonOut(login=p.login, name=p.name)


def _cc(c: changes.CostCenter) -> CostCenterOut:
    return CostCenterOut(code=c.code, name=c.name)


@router.get("/versions/{code}/changes", responses={403: {}, 404: {}})
def change_log(
    request: Request,
    user: CurrentUser,
    code: str,
    kind: changes.Kind = "all",
    sheet: changes.Sheet | None = None,
    actor: CodeQuery = None,
    cost_center: CodeQuery = None,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
) -> ChangesOut:
    """The round's change batches, newest first, 50 a page."""
    try:
        page = changes.history(
            engine_of(request),
            user,
            code,
            kind=kind,
            sheet=sheet,
            actor=actor,
            cost_center=cost_center,
            cursor=cursor,
        )
    except HANDLED as e:
        raise _errors(e) from None
    return ChangesOut(
        batches=[
            BatchOut(
                batch_id=b.batch_id,
                at=b.at,
                actor=_person(b.actor),
                sheets=b.sheets,
                sources=b.sources,
                cost_centers=[_cc(c) for c in b.cost_centers],
                cost_center_count=b.cost_center_count,
                cells=b.cells,
                cleared=b.cleared,
                calculated=b.calculated,
            )
            for b in page.batches
        ],
        counts=page.counts,
        cursor=page.cursor,
        users=[_person(p) for p in page.users],
        cost_centers=[_cc(c) for c in page.cost_centers],
    )


@router.get("/versions/{code}/changes/{batch_id}", responses={403: {}, 404: {}})
def change_batch(
    request: Request,
    user: CurrentUser,
    code: str,
    batch_id: str,
    cost_center: CodeQuery = None,
) -> BatchDetailOut:
    """What one change batch wrote, per cost center and line."""
    try:
        d = changes.batch_detail(engine_of(request), user, code, batch_id, cost_center)
    except HANDLED as e:
        raise _errors(e) from None
    return BatchDetailOut(
        batch_id=d.batch_id,
        lines=[
            LineOut(
                sheet=line.sheet,
                cost_center=_cc(line.cost_center),
                code=line.code,
                name=line.name,
                unit=line.unit,
                source=line.source,
                months=line.months,
                before=None if line.before is None else f"{line.before:f}",
                after=None if line.after is None else f"{line.after:f}",
            )
            for line in d.lines
        ],
        total_lines=d.total_lines,
    )
