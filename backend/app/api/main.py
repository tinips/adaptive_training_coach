"""FastAPI application construction."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine

from app.api.routes.baseline_web_app import router as baseline_web_app_router
from app.api.routes.health import router as health_router
from app.api.routes.mobile_sync import router as mobile_sync_router
from app.config import Settings, get_settings
from app.db.session import create_engine, create_session_factory
from app.logging import configure_logging


def create_app(
    settings: Settings | None = None,
    *,
    engine: AsyncEngine | None = None,
) -> FastAPI:
    """Create an independently testable API application."""

    runtime_settings = settings or get_settings()
    runtime_engine = engine or create_engine(runtime_settings)
    session_factory = create_session_factory(runtime_engine)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        configure_logging(runtime_settings.log_level)
        try:
            yield
        finally:
            await runtime_engine.dispose()

    application = FastAPI(
        title="Adaptive Endurance Coach API",
        version="0.1.0",
        lifespan=lifespan,
    )
    application.state.settings = runtime_settings
    application.state.engine = runtime_engine
    application.state.session_factory = session_factory
    application.include_router(health_router)
    application.include_router(baseline_web_app_router)
    application.include_router(mobile_sync_router)
    return application


app = create_app()
