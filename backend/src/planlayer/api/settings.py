"""Installation settings (E-12) for the admin: so far the sign convention of published amounts
(ADR 0019)."""

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from planlayer import signs
from planlayer.access import AccessError
from planlayer.api.auth import CurrentUser, engine_of

router = APIRouter(prefix="/api")


class CodeNameOut(BaseModel):
    code: str
    name: str


class SignsOut(BaseModel):
    convention: Literal["ledger", "result"]
    cost_accounts: int
    revenue_accounts: int
    credit_accounts: int
    untyped: list[CodeNameOut]  # accounts without a type: published as costs
    credits: list[CodeNameOut]  # accounts credited by capacity planning or a forwarding


class SignsIn(BaseModel):
    convention: Literal["ledger", "result"]


def _out(s: signs.SignSettings) -> SignsOut:
    return SignsOut(
        convention=s.convention,
        cost_accounts=s.roles["cost"],
        revenue_accounts=s.roles["revenue"],
        credit_accounts=s.roles["credit"],
        untyped=[CodeNameOut(code=c, name=n) for c, n in s.untyped],
        credits=[CodeNameOut(code=c, name=n) for c, n in s.credits],
    )


@router.get("/settings/signs", responses={403: {}})
def get_signs(request: Request, user: CurrentUser) -> SignsOut:
    try:
        return _out(signs.settings(engine_of(request), user))
    except AccessError as e:
        raise HTTPException(403, str(e)) from None


@router.put("/settings/signs", responses={403: {}, 422: {}})
def put_signs(request: Request, user: CurrentUser, body: SignsIn) -> SignsOut:
    engine = engine_of(request)
    try:
        signs.set_convention(engine, user, body.convention)
        return _out(signs.settings(engine, user))
    except AccessError as e:
        raise HTTPException(403, str(e)) from None
    except signs.SignsError as e:
        raise HTTPException(422, str(e)) from None
