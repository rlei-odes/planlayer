"""Installation settings (E-12) for the admin: the sign convention of published amounts
(ADR 0019) and the default number format."""

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from planlayer import number_format, signs
from planlayer.access import AccessError
from planlayer.api.auth import CurrentUser, NumberFormatIO, engine_of, format_io

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


@router.put("/settings/number-format", responses={403: {}, 422: {}})
def put_number_format(request: Request, user: CurrentUser, body: NumberFormatIO) -> NumberFormatIO:
    """The installation's number format, for every user without choices of their own."""
    engine = engine_of(request)
    value = number_format.NumberFormat(body.separators, body.decimals, body.scale)
    try:
        number_format.set_default(engine, user, value)
    except AccessError as e:
        raise HTTPException(403, str(e)) from None
    return format_io(number_format.default(engine, user))
