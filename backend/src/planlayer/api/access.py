"""Users (admin) and responsibilities (controller): owners, overrides, deputies, approvers."""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from planlayer import access
from planlayer.api.auth import CurrentUser, engine_of

router = APIRouter(prefix="/api")


def _http(e: Exception) -> HTTPException:
    match e:
        case access.AccessError():
            return HTTPException(403, str(e))
        case access.UserError():
            return HTTPException(422, str(e))
    raise e


HANDLED = (access.AccessError, access.UserError)


class UserOut(BaseModel):
    login: str
    display_name: str
    email: str | None
    is_admin: bool
    is_controller: bool
    is_viewer: bool
    disabled: bool


class UserIn(BaseModel):
    login: str
    display_name: str
    email: str | None = None
    is_admin: bool = False
    is_controller: bool = False
    is_viewer: bool = False


class UserChange(BaseModel):
    display_name: str | None = None
    email: str | None = None
    is_admin: bool | None = None
    is_controller: bool | None = None
    is_viewer: bool | None = None
    disabled: bool | None = None


class ResponsibilityOut(BaseModel):
    kind: str
    code: str
    name: str
    depth: int
    owner: str | None
    matched: str | None
    planner: str | None
    overridden: bool
    deputies: list[str]
    approvers: list[str]


class ResponsibilitiesOut(BaseModel):
    rows: list[ResponsibilityOut]
    unmatched_owners: list[str]
    users: list[str]  # active logins, for choosing


class OwnerIn(BaseModel):
    login: str | None  # None: no owner


class LoginIn(BaseModel):
    login: str


@router.get("/users", responses={403: {}})
def users(request: Request, user: CurrentUser) -> list[UserOut]:
    try:
        user.require_admin()
    except HANDLED as e:
        raise _http(e) from None
    return [
        UserOut.model_validate(u, from_attributes=True) for u in access.users(engine_of(request))
    ]


@router.post("/users", responses={403: {}, 422: {}})
def create_user(request: Request, user: CurrentUser, body: UserIn) -> None:
    try:
        access.create_user(
            engine_of(request),
            user,
            body.login,
            body.display_name,
            body.email,
            is_admin=body.is_admin,
            is_controller=body.is_controller,
            is_viewer=body.is_viewer,
        )
    except HANDLED as e:
        raise _http(e) from None


@router.patch("/users/{login}", responses={403: {}, 422: {}})
def change_user(request: Request, user: CurrentUser, login: str, body: UserChange) -> None:
    try:
        access.update_user(engine_of(request), user, login, **body.model_dump(exclude_none=True))
    except HANDLED as e:
        raise _http(e) from None


@router.get("/responsibilities", responses={403: {}})
def responsibilities(request: Request, user: CurrentUser) -> ResponsibilitiesOut:
    engine = engine_of(request)
    try:
        r = access.responsibilities(engine, user)
    except HANDLED as e:
        raise _http(e) from None
    return ResponsibilitiesOut(
        rows=[ResponsibilityOut.model_validate(row, from_attributes=True) for row in r.rows],
        unmatched_owners=r.unmatched_owners,
        users=[u.login for u in access.users(engine, active_only=True)],
    )


@router.put("/responsibilities/{cc_code}/owner", responses={403: {}, 422: {}})
def set_owner(request: Request, user: CurrentUser, cc_code: str, body: OwnerIn) -> None:
    """Override the owner matched from the source."""
    try:
        access.set_owner(engine_of(request), user, cc_code, body.login)
    except HANDLED as e:
        raise _http(e) from None


@router.delete("/responsibilities/{cc_code}/owner", responses={403: {}, 422: {}})
def clear_owner(request: Request, user: CurrentUser, cc_code: str) -> None:
    """Back to the owner matched from the source."""
    try:
        access.clear_owner(engine_of(request), user, cc_code)
    except HANDLED as e:
        raise _http(e) from None


@router.post("/responsibilities/{code}/deputies", responses={403: {}, 422: {}})
def add_deputy(request: Request, user: CurrentUser, code: str, body: LoginIn) -> None:
    try:
        access.add_deputy(engine_of(request), user, code, body.login)
    except HANDLED as e:
        raise _http(e) from None


@router.delete("/responsibilities/{code}/deputies/{login}", responses={403: {}, 422: {}})
def remove_deputy(request: Request, user: CurrentUser, code: str, login: str) -> None:
    try:
        access.remove_deputy(engine_of(request), user, code, login)
    except HANDLED as e:
        raise _http(e) from None


@router.post("/responsibilities/{code}/approvers", responses={403: {}, 422: {}})
def add_approver(request: Request, user: CurrentUser, code: str, body: LoginIn) -> None:
    try:
        access.add_approver(engine_of(request), user, code, body.login)
    except HANDLED as e:
        raise _http(e) from None


@router.delete("/responsibilities/{code}/approvers/{login}", responses={403: {}, 422: {}})
def remove_approver(request: Request, user: CurrentUser, code: str, login: str) -> None:
    try:
        access.remove_approver(engine_of(request), user, code, login)
    except HANDLED as e:
        raise _http(e) from None
