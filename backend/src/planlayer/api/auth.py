"""Login sessions (T-22, ADR 0009).

A session token lives in an HttpOnly cookie; the app store keeps only its hash. How a user gets
one is ``planlayer.api.login`` (ADR 0030).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import Engine

from planlayer import access, number_format
from planlayer.config import Settings

SESSION_COOKIE = "planlayer_session"

router = APIRouter(prefix="/api")


def engine_of(request: Request) -> Engine:
    engine: Engine = request.app.state.engine
    return engine


def settings_of(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def current_user(request: Request) -> access.User:
    token = request.cookies.get(SESSION_COOKIE)
    user = access.session_user(engine_of(request), token) if token else None
    if user is None:
        raise HTTPException(401, "not logged in, or the session has ended")
    return user


CurrentUser = Annotated[access.User, Depends(current_user)]


class NumberFormatIO(BaseModel):
    separators: number_format.Separators
    decimals: bool  # cents on full amounts; thousands and millions round on their own
    scale: number_format.Scale
    units: bool  # the k or M after a value in thousands or millions


class NumberOverrideIO(BaseModel):
    """A user's own choices; null takes the installation's."""

    separators: number_format.Separators | None = None
    decimals: bool | None = None
    scale: number_format.Scale | None = None
    units: bool | None = None


class Me(BaseModel):
    login: str
    display_name: str
    email: str | None
    is_admin: bool
    is_controller: bool
    is_viewer: bool
    is_hr: bool
    # Reads at least one cost center, so the planning and status pages have something to show.
    reads_cost_centers: bool
    # HR, or the owner the source names on a cost center: the personnel costs page (ADR 0022).
    reads_pay: bool
    # How amounts are shown and typed: the user's own choices over the installation's.
    number_format: NumberFormatIO
    number_default: NumberFormatIO
    number_own: NumberOverrideIO


def format_io(f: number_format.NumberFormat) -> NumberFormatIO:
    return NumberFormatIO(
        separators=f.separators, decimals=f.decimals, scale=f.scale, units=f.units
    )


def me_of(engine: Engine, user: access.User) -> Me:
    numbers = number_format.of_user(engine, user)
    return Me(
        reads_cost_centers=access.reads_cost_centers(engine, user),
        login=user.login,
        display_name=user.display_name,
        email=user.email,
        is_admin=user.is_admin,
        is_controller=user.is_controller,
        is_viewer=user.is_viewer,
        is_hr=user.is_hr,
        reads_pay=access.reads_pay(engine, user),
        number_format=format_io(numbers.effective),
        number_default=format_io(numbers.default),
        number_own=NumberOverrideIO(
            separators=numbers.own.separators,
            decimals=numbers.own.decimals,
            scale=numbers.own.scale,
            units=numbers.own.units,
        ),
    )


@router.get("/me", responses={401: {}})
def me(request: Request, user: CurrentUser) -> Me:
    return me_of(engine_of(request), user)


@router.put("/me/number-format", responses={401: {}, 422: {}})
def put_number_format(request: Request, user: CurrentUser, body: NumberOverrideIO) -> Me:
    """The user's own number format, for themselves."""
    engine = engine_of(request)
    number_format.set_own(
        engine, user, number_format.Override(body.separators, body.decimals, body.scale, body.units)
    )
    return me_of(engine, user)


def start_session(request: Request, response: Response, login: str) -> Me:
    """A session for an active user, in the cookie; the user as the API shows them."""
    settings = settings_of(request)
    engine = engine_of(request)
    token = access.create_session(engine, login, settings.session_hours)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.session_hours * 3600,
        httponly=True,
        samesite="lax",
        secure=not settings.development,
    )
    user = access.session_user(engine, token)
    assert user is not None
    return me_of(engine, user)


@router.post("/auth/logout")
def logout(request: Request, response: Response) -> None:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        access.end_session(engine_of(request), token)
    response.delete_cookie(SESSION_COOKIE)
