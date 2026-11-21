"""FastAPI application factory."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, Request, Response, status
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError, StatementError

from planlayer import __version__, crypto
from planlayer.adapters.source import PostgresSource
from planlayer.api.access import router as access_router
from planlayer.api.auth import router as auth_router
from planlayer.api.bodylimit import BodyLimit
from planlayer.api.capacity import router as capacity_router
from planlayer.api.capex import router as capex_router
from planlayer.api.forwarding import router as forwarding_router
from planlayer.api.home import router as home_router
from planlayer.api.item_lists import router as item_lists_router
from planlayer.api.login import router as login_router
from planlayer.api.masterdata import router as masterdata_router
from planlayer.api.notes import router as notes_router
from planlayer.api.personnel import router as personnel_router
from planlayer.api.planning import router as planning_router
from planlayer.api.publish import router as publish_router
from planlayer.api.settings import router as settings_router
from planlayer.api.setup import router as setup_router
from planlayer.api.workforce import router as workforce_router
from planlayer.api.workforce import transfers as transfers_router
from planlayer.config import Settings
from planlayer.login import local
from planlayer.login import settings as login_settings
from planlayer.store.db import create_app_engine
from planlayer.store.migrate import current_revision, head_revision
from planlayer.store.output import create_output_engine

log = logging.getLogger("planlayer")

# Postgres refusing a value too large for its column: a number beyond its numeric(p, s), or a
# text beyond a length. The parsers check numbers against their columns; this catches the rest.
TOO_LARGE_SQLSTATES = ("22003", "22001")


class Health(BaseModel):
    status: Literal["ok", "unavailable"]
    version: str
    schema_revision: str | None


def _first_run(engine: Engine, settings: Settings) -> None:
    """While no admin can log in, the setup code goes to the log at start (ADR 0030). An app
    store not yet migrated, or not reachable yet, gets it when the login page first asks."""
    try:
        with engine.connect() as conn:
            if current_revision(conn, settings.app_schema) != head_revision():
                return
        local.ensure_code(engine, login_settings.load(engine)[0], renew=True)
    except SQLAlchemyError as e:
        # The server starts without its database; /api/health reports it.
        log.warning("first run check skipped, the app store can't be read: %s", type(e).__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()  # type: ignore[call-arg]  # values come from the env
    engine = create_app_engine(settings)
    source = PostgresSource(settings.source_url)
    output = create_output_engine(settings)

    # FastAPI requires the lifespan to be async; it only disposes the pool on shutdown.
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if not settings.demo:
            _first_run(engine, settings)
        yield
        engine.dispose()
        source.dispose()
        output.dispose()

    app = FastAPI(title="Planlayer", version=__version__, lifespan=lifespan)
    app.state.engine = engine
    app.state.settings = settings
    app.state.source = source
    app.state.output_engine = output
    app.add_middleware(BodyLimit)

    @app.exception_handler(DBAPIError)
    def too_large(_: Request, e: DBAPIError) -> JSONResponse:
        if getattr(e.orig, "sqlstate", None) in TOO_LARGE_SQLSTATES:
            return JSONResponse({"detail": "a value is too large for its field"}, status_code=422)
        raise e

    # Pay and the client secret without their key (ADR 0029): those pages fail, the rest works.
    # Writing wraps the error in a StatementError, reading doesn't.
    def no_key(e: Exception) -> JSONResponse:
        log.error("%s", e)
        return JSONResponse({"detail": str(e)}, status_code=503)

    @app.exception_handler(crypto.PayKeyError)
    def pay_key(_: Request, e: crypto.PayKeyError) -> JSONResponse:
        return no_key(e)

    @app.exception_handler(StatementError)
    def pay_key_on_write(_: Request, e: StatementError) -> JSONResponse:
        if isinstance(e.orig, crypto.PayKeyError):
            return no_key(e.orig)
        raise e

    app.include_router(auth_router)
    app.include_router(login_router)
    app.include_router(access_router)
    app.include_router(masterdata_router)
    app.include_router(planning_router)
    app.include_router(capacity_router)
    app.include_router(workforce_router)
    app.include_router(personnel_router)
    app.include_router(transfers_router)
    app.include_router(home_router)
    app.include_router(forwarding_router)
    app.include_router(capex_router)
    app.include_router(settings_router)
    app.include_router(setup_router)
    app.include_router(publish_router)
    app.include_router(notes_router)
    app.include_router(item_lists_router)

    @app.get("/api/health")
    def health(response: Response) -> Health:
        """Liveness plus app store reachability and migration state."""
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
                revision = current_revision(conn, settings.app_schema)
        except SQLAlchemyError:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return Health(status="unavailable", version=__version__, schema_revision=None)
        return Health(status="ok", version=__version__, schema_revision=revision)

    # Mounted last, so /api routes take precedence.
    if settings.frontend_dist is not None:
        app.mount("/", StaticFiles(directory=settings.frontend_dist, html=True), name="frontend")

    return app
