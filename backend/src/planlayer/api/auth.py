"""Login sessions (T-22, ADR 0009).

A session token lives in an HttpOnly cookie; the app store keeps only its hash. Until OIDC and
LDAPS arrive, the development login (``PLANLAYER_DEV_LOGIN``, refused in production) logs in as
any active user without a password.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import Engine

from planlayer import access
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


class Me(BaseModel):
    login: str
    display_name: str
    email: str | None
    is_admin: bool
    is_controller: bool
    is_viewer: bool


class LoginOptions(BaseModel):
    dev_login: bool


class DevUser(BaseModel):
    login: str
    display_name: str


class DevLoginIn(BaseModel):
    login: str


def me_of(user: access.User) -> Me:
    return Me(
        login=user.login,
        display_name=user.display_name,
        email=user.email,
        is_admin=user.is_admin,
        is_controller=user.is_controller,
        is_viewer=user.is_viewer,
    )


@router.get("/me", responses={401: {}})
def me(user: CurrentUser) -> Me:
    return me_of(user)


@router.get("/auth/options")
def options(request: Request) -> LoginOptions:
    return LoginOptions(dev_login=settings_of(request).dev_login)


def _require_dev_login(request: Request) -> None:
    if not settings_of(request).dev_login:
        raise HTTPException(404, "the development login is off")


@router.get("/auth/dev-users", responses={404: {}})
def dev_users(request: Request) -> list[DevUser]:
    _require_dev_login(request)
    return [
        DevUser(login=u.login, display_name=u.display_name)
        for u in access.users(engine_of(request), active_only=True)
    ]


@router.post("/auth/dev-login", responses={404: {}, 422: {}})
def dev_login(request: Request, response: Response, body: DevLoginIn) -> Me:
    _require_dev_login(request)
    settings = settings_of(request)
    engine = engine_of(request)
    try:
        token = access.create_session(engine, body.login, settings.session_hours)
    except access.UserError as e:
        raise HTTPException(422, str(e)) from None
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.session_hours * 3600,
        httponly=True,
        samesite="lax",
        secure=settings.environment == "production",
    )
    user = access.session_user(engine, token)
    assert user is not None
    return me_of(user)


@router.post("/auth/logout")
def logout(request: Request, response: Response) -> None:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        access.end_session(engine_of(request), token)
    response.delete_cookie(SESSION_COOKIE)
