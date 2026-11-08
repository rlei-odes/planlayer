"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, Response, status
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from planlayer import __version__
from planlayer.api.access import router as access_router
from planlayer.api.auth import router as auth_router
from planlayer.api.planning import router as planning_router
from planlayer.config import Settings
from planlayer.store.db import create_app_engine
from planlayer.store.migrate import current_revision


class Health(BaseModel):
    status: Literal["ok", "unavailable"]
    version: str
    schema_revision: str | None


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()  # type: ignore[call-arg]  # values come from the env
    engine = create_app_engine(settings)

    # FastAPI requires the lifespan to be async; it only disposes the pool on shutdown.
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        engine.dispose()

    app = FastAPI(title="Planlayer", version=__version__, lifespan=lifespan)
    app.state.engine = engine
    app.state.settings = settings
    app.include_router(auth_router)
    app.include_router(access_router)
    app.include_router(planning_router)

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
