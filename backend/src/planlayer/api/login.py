"""Logging in (ADR 0030): the demo's user list, the first run's admin, passwords (local accounts
and LDAP), OpenID Connect; and the provider's settings for the admin, with a test of each."""

import json
import logging
import os
from typing import Annotated
from urllib.parse import parse_qs, urlencode, urlsplit

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

from planlayer import access
from planlayer.api.auth import CurrentUser, Me, engine_of, settings_of, start_session
from planlayer.api.limits import Code, Email, LongText, Name, Password, Pem
from planlayer.login import events, identity, ldap, local, oidc
from planlayer.login import settings as login_settings
from planlayer.login.settings import LoginSettings

log = logging.getLogger("planlayer.login")

router = APIRouter(prefix="/api")

CALLBACK = "/api/auth/oidc/callback"


# --- What the login page offers ------------------------------------------------------------------


class LoginOptions(BaseModel):
    demo: bool  # the user list, without passwords
    setup: bool  # no admin can log in: the first run's admin account
    oidc: str | None  # the provider's button, when OpenID Connect is set up
    ldap: bool  # the password form also takes directory accounts


class DemoUser(BaseModel):
    login: str
    display_name: str


class DemoLoginIn(BaseModel):
    login: Code


class PasswordLoginIn(BaseModel):
    login: Code
    password: Password


class SetupIn(BaseModel):
    code: Code
    login: Code
    display_name: Name
    email: Email | None = None
    password: Password


def _client(request: Request) -> events.Client:
    return events.Client(
        address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )


def _in_force(request: Request) -> LoginSettings:
    return login_settings.load(engine_of(request))[0]


@router.get("/auth/options")
def options(request: Request) -> LoginOptions:
    settings = settings_of(request)
    if settings.demo:
        return LoginOptions(demo=True, setup=False, oidc=None, ldap=False)
    engine = engine_of(request)
    s = _in_force(request)
    setup = local.needed(engine, s)
    if setup:
        local.ensure_code(engine, s)
    return LoginOptions(
        demo=False,
        setup=setup,
        oidc=(s.oidc_label or "OpenID Connect") if s.method == "oidc" else None,
        ldap=s.method == "ldap",
    )


def _require_demo(request: Request) -> None:
    if not settings_of(request).demo:
        raise HTTPException(404, "this is not a demo installation")


@router.get("/auth/demo-users", responses={404: {}})
def demo_users(request: Request) -> list[DemoUser]:
    _require_demo(request)
    return [
        DemoUser(login=u.login, display_name=u.display_name)
        for u in access.users(engine_of(request), active_only=True)
    ]


@router.post("/auth/demo-login", responses={404: {}, 422: {}})
def demo_login(request: Request, response: Response, body: DemoLoginIn) -> Me:
    _require_demo(request)
    try:
        me = start_session(request, response, body.login)
    except access.UserError as e:
        raise HTTPException(422, str(e)) from None
    events.record(
        engine_of(request), _client(request), login=body.login, method="demo", outcome="success"
    )
    return me


@router.post("/auth/setup", responses={404: {}, 422: {}, 429: {}})
def setup(request: Request, response: Response, body: SetupIn) -> Me:
    """The first run's admin account, with the setup code from the server log."""
    if settings_of(request).demo:
        raise HTTPException(404, "a demo has no first run")
    engine, client = engine_of(request), _client(request)
    if events.blocked(engine, client, body.login):
        raise HTTPException(429, "too many failed attempts; try again in 15 minutes")
    try:
        login = local.create_first_admin(
            engine,
            _in_force(request),
            code=body.code,
            login=body.login,
            display_name=body.display_name,
            email=body.email,
            password=body.password,
        )
    except local.SetupError as e:
        events.record(
            engine, client, login=body.login, method="setup", outcome="failed", detail=str(e)
        )
        raise HTTPException(422, str(e)) from None
    events.record(engine, client, login=login, method="setup", outcome="success")
    return start_session(request, response, login)


@router.post("/auth/login", responses={401: {}, 404: {}, 429: {}, 503: {}})
def password_login(request: Request, response: Response, body: PasswordLoginIn) -> Me:
    """A local account's password, or else the directory's (LDAP)."""
    if settings_of(request).demo:
        raise HTTPException(404, "a demo logs in without passwords")
    engine, client = engine_of(request), _client(request)

    def record(outcome: str, detail: str | None = None) -> None:
        events.record(
            engine, client, login=body.login, method="password", outcome=outcome, detail=detail
        )

    if events.blocked(engine, client, body.login):
        record("blocked")
        raise HTTPException(429, "too many failed logins; try again in 15 minutes")
    login = local.check_password(engine, body.login, body.password)
    s = _in_force(request)
    if login is None and s.method == "ldap":
        try:
            found = ldap.bind(s, body.login, body.password).identity
            login = identity.resolve(engine, s, found) if found is not None else None
        except ldap.LoginError as e:
            record("error", str(e))
            log.error("directory login failed: %s", e)
            raise HTTPException(503, "the directory can't be reached; see the server log") from None
        except identity.Refused as e:
            record("refused", str(e))
            raise HTTPException(401, f"Login refused: {e}") from None
    if login is None:
        record("failed")
        raise HTTPException(401, "wrong login or password")
    try:
        me = start_session(request, response, login)
    except access.UserError:
        record("refused", "no active user")
        raise HTTPException(401, "wrong login or password") from None
    record("success")
    return me


# --- OpenID Connect ------------------------------------------------------------------------------


def redirect_uri(request: Request) -> str:
    base = settings_of(request).public_url or str(request.base_url).rstrip("/")
    return base + CALLBACK


def _back(**query: str) -> RedirectResponse:
    return RedirectResponse("/?" + urlencode(query) if query else "/", status_code=303)


@router.get("/auth/oidc/login", responses={303: {}, 404: {}})
def oidc_start(request: Request) -> RedirectResponse:
    """Send the browser to the provider."""
    s = _in_force(request)
    if s.method != "oidc" or settings_of(request).demo:
        raise HTTPException(404, "OpenID Connect is not set up")
    try:
        url = oidc.start(engine_of(request), s, redirect_uri(request))
    except oidc.LoginError as e:
        log.error("OpenID Connect login not started: %s", e)
        return _back(login_error="The login provider can't be reached. See the server log.")
    return RedirectResponse(url, status_code=303)


class Step(BaseModel):
    label: str
    ok: bool | None  # None: skipped or only a note
    detail: str


def _steps_of_identity(
    request: Request, s: LoginSettings, found: identity.Identity, attributes: list[str], what: str
) -> list[Step]:
    steps = [
        Step(label=what, ok=True, detail=", ".join(attributes) or "none"),
        Step(
            label="User",
            ok=True,
            detail=f"login {found.login}, name {found.display_name}, "
            f"e-mail {found.email or '(none)'}"
            + ("" if found.email_verified else " (unverified, not matched)"),
        ),
        Step(
            label="Groups",
            ok=True if found.groups else None,
            detail=", ".join(sorted(found.groups)[:20]) or "none found",
        ),
    ]
    roles = identity.roles_of(s, found)
    steps.append(
        Step(
            label="Roles",
            ok=None,
            detail=", ".join(f"{k[3:]} {'yes' if v else 'no'}" for k, v in roles.items())
            or "no role groups: roles are set on the Users page",
        )
    )
    try:
        steps.append(
            Step(label="Logs in as", ok=True, detail=identity.preview(engine_of(request), s, found))
        )
    except identity.Refused as e:
        steps.append(Step(label="Logs in as", ok=False, detail=f"refused: {e}"))
    return steps


# An authorization code; Entra ID's run past a thousand characters.
AuthCode = Annotated[str, Query(max_length=4096)]


@router.get("/auth/oidc/callback", responses={303: {}})
def oidc_callback(
    request: Request,
    state: Annotated[str, Query(max_length=200)] = "",
    code: AuthCode = "",
    error: Annotated[str, Query(max_length=200)] = "",
) -> Response:
    """Where the provider sends the browser back to. A login gets a session and lands on the
    start page; a test keeps its steps and lands on the Settings page."""
    engine, client = engine_of(request), _client(request)
    p = oidc.pending(engine, state) if state else None
    if p is None:
        events.record(
            engine,
            client,
            login="",
            method="oidc",
            outcome="failed",
            detail="unknown or expired state",
        )
        return _back(login_error="The login took too long or was started elsewhere. Try again.")
    s = p.settings if p.settings is not None else _in_force(request)
    steps = [
        Step(label="Provider", ok=not error, detail=f"answered {error!r}" if error else "signed in")
    ]
    found: identity.Identity | None = None
    claims: list[str] = []
    if not error:
        try:
            found, claims = oidc.finish(s, p, code)
            steps.append(
                Step(label="ID token", ok=True, detail="issuer, audience, expiry and nonce checked")
            )
        except oidc.LoginError as e:
            steps.append(Step(label="ID token", ok=False, detail=str(e)))
    if p.test_by is not None:
        if found is not None:
            steps += _steps_of_identity(request, s, found, claims, "Claims")
        oidc.keep_result(
            engine,
            state,
            {"steps": [x.model_dump() for x in steps], "groups": _picks(found)},
        )
        return _test_done(state)
    oidc.forget(engine, state)
    if found is None:
        detail = steps[-1].detail
        events.record(engine, client, login="", method="oidc", outcome="failed", detail=detail)
        log.warning("OpenID Connect login failed: %s", detail)
        return _back(login_error="The login provider's answer was refused. See the server log.")
    try:
        login = identity.resolve(engine, s, found)
    except identity.Refused as e:
        events.record(
            engine, client, login=found.login, method="oidc", outcome="refused", detail=str(e)
        )
        return _back(login_error=f"Login refused: {e}.")
    response = _back()
    start_session(request, response, login)
    events.record(engine, client, login=login, method="oidc", outcome="success")
    return response


def _test_done(state: str) -> HTMLResponse:
    """The end of a test at the provider. In the popup the Settings page opened, it tells that
    page and closes; opened in the page itself (a blocked popup), it goes back to Settings."""
    back = json.dumps(f"/?{urlencode({'login_test': state})}#settings")
    message = json.dumps({"planlayerLoginTest": state})
    return HTMLResponse(
        "<!doctype html><meta charset=utf-8><title>Planlayer</title>"
        "<p>Test finished. This window closes by itself.</p><script>"
        f"if (window.opener) {{ window.opener.postMessage({message}, location.origin); "
        f"window.close() }} else location.replace({back})</script>"
    )


# --- The provider's settings, for the admin ------------------------------------------------------


class LoginSettingsIO(BaseModel):
    method: login_settings.Method
    oidc_label: Name = ""
    oidc_issuer: LongText = ""
    oidc_client_id: LongText = ""
    oidc_scopes: Name = "openid profile email"
    oidc_login_claim: Name = "preferred_username"
    oidc_email_claim: Name = "email"
    oidc_name_claim: Name = "name"
    oidc_groups_claim: Name = "groups"
    ldap_url: LongText = ""
    ldap_bind_dn: LongText = ""
    ldap_base_dn: LongText = ""
    ldap_login_attribute: Name = "uid"
    ldap_email_attribute: Name = "mail"
    ldap_name_attribute: Name = "cn"
    ca_pem: Pem = ""
    allowed_group: LongText = ""
    admin_group: LongText = ""
    controller_group: LongText = ""
    viewer_group: LongText = ""
    hr_group: LongText = ""


class LoginPageOut(BaseModel):
    settings: LoginSettingsIO
    from_env: bool  # set by PLANLAYER_LOGIN_* variables: read-only here
    redirect_uri: str  # to register at the OpenID Connect provider
    client_secret: bool  # PLANLAYER_OIDC_CLIENT_SECRET is set: a confidential client
    problems: list[str]
    # The saved settings filled in from PLANLAYER_LOGIN_* variables set without the method: a
    # draft to test and save.
    suggested: LoginSettingsIO | None = None


class LdapTestIn(BaseModel):
    settings: LoginSettingsIO
    login: Code
    password: Password


class OidcTestIn(BaseModel):
    settings: LoginSettingsIO


class OidcTestOut(BaseModel):
    url: str  # the provider's login page, to open
    state: str  # the test's key, to ask for its result


class TestOut(BaseModel):
    steps: list[Step]
    ok: bool
    env: str | None  # the settings as environment variables, after a test that passed
    # The groups (or app roles) the test found, for the admin to pick role groups from. An LDAP
    # group by its name, not its DN.
    groups: Annotated[list[str], Field(max_length=10_000)] = []


def _io(s: LoginSettings) -> LoginSettingsIO:
    return LoginSettingsIO.model_validate(s.as_dict())


def _admin(user: access.User) -> None:
    try:
        user.require_admin()
    except access.AccessError as e:
        raise HTTPException(403, str(e)) from None


def _of(body: LoginSettingsIO) -> LoginSettings:
    return LoginSettings.of(body.model_dump())


def _page(request: Request) -> LoginPageOut:
    s, from_env = login_settings.load(engine_of(request))
    draft = None if from_env else login_settings.suggested(s)
    return LoginPageOut(
        settings=_io(s),
        suggested=_io(draft) if draft else None,
        from_env=from_env,
        redirect_uri=redirect_uri(request),
        client_secret=bool(os.environ.get(oidc.CLIENT_SECRET_ENV)),
        problems=s.problems(development=settings_of(request).development),
    )


@router.get("/settings/login", responses={403: {}})
def get_login(request: Request, user: CurrentUser) -> LoginPageOut:
    _admin(user)
    return _page(request)


@router.put("/settings/login", responses={403: {}, 422: {}})
def put_login(request: Request, user: CurrentUser, body: LoginSettingsIO) -> LoginPageOut:
    _admin(user)
    try:
        login_settings.save(
            engine_of(request), user, _of(body), development=settings_of(request).development
        )
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    log.warning("login settings changed by %s: method %s", user.login, body.method)
    return _page(request)


def _picks(found: identity.Identity | None) -> list[str]:
    return sorted(g for g in found.groups if "=" not in g) if found else []


def _result(steps: list[Step], s: LoginSettings, groups: list[str] | None = None) -> TestOut:
    ok = all(x.ok is not False for x in steps)
    return TestOut(steps=steps, ok=ok, env=s.env() if ok else None, groups=groups or [])


@router.post("/settings/login/test-ldap", responses={403: {}})
def test_ldap(request: Request, user: CurrentUser, body: LdapTestIn) -> TestOut:
    """A directory login with these settings, unsaved, as this login; no session."""
    _admin(user)
    s = _of(body.settings)
    if problems := s.problems(development=settings_of(request).development):
        return _result([Step(label="Settings", ok=False, detail="; ".join(problems))], s)
    steps = [Step(label="Settings", ok=True, detail="complete")]
    try:
        bound = ldap.bind(s, body.login, body.password)
    except ldap.LoginError as e:
        return _result([*steps, Step(label="Directory", ok=False, detail=str(e))], s)
    if bound.identity is None:
        steps.append(
            Step(label="Bind", ok=False, detail=f"wrong login or password for {bound.bind_dn}")
        )
        return _result(steps, s)
    steps.append(Step(label="Bind", ok=True, detail=f"as {bound.bind_dn}"))
    steps += _steps_of_identity(request, s, bound.identity, bound.attributes, "Attributes")
    return _result(steps, s, _picks(bound.identity))


@router.post("/settings/login/test-oidc", responses={403: {}, 422: {}})
def test_oidc(request: Request, user: CurrentUser, body: OidcTestIn) -> OidcTestOut:
    """Start a login at the provider with these settings, unsaved; the callback keeps the steps
    for ``GET /settings/login/test-oidc/{state}`` and creates no session."""
    _admin(user)
    s = _of(body.settings)
    if problems := s.problems(development=settings_of(request).development):
        raise HTTPException(422, "; ".join(problems))
    try:
        url = oidc.start(engine_of(request), s, redirect_uri(request), test_by=user.user_id)
    except oidc.LoginError as e:
        raise HTTPException(422, str(e)) from None
    return OidcTestOut(url=url, state=parse_qs(urlsplit(url).query)["state"][0])


@router.get("/settings/login/test-oidc/{state}", responses={403: {}, 404: {}})
def oidc_test_result(request: Request, user: CurrentUser, state: Code) -> TestOut:
    _admin(user)
    engine = engine_of(request)
    p = oidc.pending(engine, state)
    if p is None or p.test_by != user.user_id or p.result is None or p.settings is None:
        raise HTTPException(404, "no test result; it is kept for ten minutes")
    steps = [Step(**x) for x in p.result["steps"]]
    return _result(steps, p.settings, p.result.get("groups"))
