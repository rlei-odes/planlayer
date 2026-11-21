"""Installation settings (E-12) for the admin: the sign convention of published amounts
(ADR 0019), the default number format, the databases Planlayer connects to, and whether a newer
release is out."""

import logging
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import Engine, make_url, text
from sqlalchemy.exc import DBAPIError

from planlayer import __version__, number_format, releases, signs
from planlayer.access import AccessError
from planlayer.api.auth import CurrentUser, NumberFormatIO, engine_of, format_io, settings_of

log = logging.getLogger("planlayer.settings")

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
    value = number_format.NumberFormat(body.separators, body.decimals, body.scale, body.units)
    try:
        number_format.set_default(engine, user, value)
    except AccessError as e:
        raise HTTPException(403, str(e)) from None
    return format_io(number_format.default(engine, user))


class ConnectionOut(BaseModel):
    role: Literal["app", "source", "output"]
    host: str | None
    port: int | None
    database: str | None
    user: str | None
    schema_name: str  # where Planlayer works in it
    shared: bool  # the same database as the app store's (an unset URL falls back to it)
    ok: bool
    server: str | None = None  # the server's version, when connected
    error: str | None = None  # why it failed, without the password


def _probe(engine: Engine) -> tuple[bool, str | None, str | None]:
    try:
        with engine.connect() as conn:
            version = conn.execute(text("SELECT current_setting('server_version')")).scalar()
        return True, f"PostgreSQL {str(version).split()[0]}", None  # 18.6, not its build
    except DBAPIError as e:
        log.warning("connection check failed: %s", e)
        return False, None, str(e.orig or e).splitlines()[0]


@router.get("/settings/connections", responses={403: {}})
def connections(request: Request, user: CurrentUser) -> list[ConnectionOut]:
    """The three databases Planlayer connects to (T-14): the app store, the source of master data
    and actuals, and the output it publishes to. Planlayer bundles no database of its own."""
    try:
        user.require_admin()
    except AccessError as e:
        raise HTTPException(403, str(e)) from None
    s = settings_of(request)
    engines: dict[str, Engine] = {
        "app": engine_of(request),
        "source": request.app.state.source.engine,
        "output": request.app.state.output_engine,
    }
    urls = {"app": s.app_url, "source": s.source_url, "output": s.output_url}
    schemas = {"app": s.app_schema, "source": s.source_schema, "output": s.output_schema}
    found = []
    for role in ("app", "source", "output"):
        url = make_url(urls[role])
        ok, server, error = _probe(engines[role])
        found.append(
            ConnectionOut(
                role=role,
                host=url.host,
                port=url.port,
                database=url.database,
                user=url.username,
                schema_name=schemas[role],
                shared=role != "app" and urls[role] == urls["app"],
                ok=ok,
                server=server,
                error=error,
            )
        )
    return found


class ReleaseOut(BaseModel):
    current: str
    latest: str | None = None  # a newer release, when there is one
    notes_url: str | None = None
    upgrade_url: str = releases.UPGRADE_URL


@router.get("/settings/release", responses={403: {}})
def release(request: Request, user: CurrentUser) -> ReleaseOut:
    """The running version, and a newer release for the admins' notice (none when the check is
    off or failed)."""
    try:
        user.require_admin()
    except AccessError as e:
        raise HTTPException(403, str(e)) from None
    found = releases.newer(releases.latest()) if settings_of(request).update_check else None
    return ReleaseOut(
        current=__version__,
        latest=found.version if found else None,
        notes_url=found.notes_url if found else None,
    )
