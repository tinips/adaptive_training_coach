"""FastAPI application construction."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine

from app.api.routes.health import router as health_router
from app.api.routes.strava import router as strava_router
from app.bot.notifier import TelegramInitialSyncNotifier
from app.config import Settings, get_settings
from app.db.session import create_engine, create_session_factory
from app.logging import configure_logging
from app.services.apple_health import AppleHealthImportService
from app.services.strava.orchestrator import StravaCoordinator

logger = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    *,
    engine: AsyncEngine | None = None,
) -> FastAPI:
    """Create an independently testable API application."""

    runtime_settings = settings or get_settings()
    runtime_engine = engine or create_engine(runtime_settings)
    session_factory = create_session_factory(runtime_engine)
    telegram_token = runtime_settings.telegram_bot_token
    initial_sync_notifier = (
        TelegramInitialSyncNotifier(
            session_factory=session_factory,
            bot_token=telegram_token,
        )
        if telegram_token is not None and telegram_token.get_secret_value()
        else None
    )
    strava_coordinator = StravaCoordinator(
        session_factory=session_factory,
        settings=runtime_settings,
        initial_sync_notifier=initial_sync_notifier,
    )
    apple_health_import = AppleHealthImportService(
        session_factory=session_factory,
        settings=runtime_settings,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        configure_logging(runtime_settings.log_level)
        try:
            await strava_coordinator.recover_stale_work()
            await apple_health_import.recover_stale_work()
        except Exception as exc:
            logger.error(
                "Startup work recovery failed type=%s",
                type(exc).__name__,
            )
        try:
            yield
        finally:
            try:
                await strava_coordinator.aclose()
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
    application.state.strava_coordinator = strava_coordinator
    application.state.apple_health_import = apple_health_import
    application.include_router(health_router)
    application.include_router(strava_router)
    return application


app = create_app()
