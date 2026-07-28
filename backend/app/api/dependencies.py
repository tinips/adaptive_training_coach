"""FastAPI request dependencies."""

from __future__ import annotations

from typing import cast

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.services.strava.orchestrator import StravaCoordinator


def get_session_factory(
    request: Request,
) -> async_sessionmaker[AsyncSession]:
    """Return the application-owned async session factory."""

    return cast(
        async_sessionmaker[AsyncSession],
        request.app.state.session_factory,
    )


def get_runtime_settings(request: Request) -> Settings:
    """Return settings attached during application construction."""

    return cast(Settings, request.app.state.settings)


def get_strava_coordinator(request: Request) -> StravaCoordinator:
    """Return the application-owned Strava integration coordinator."""

    return cast(StravaCoordinator, request.app.state.strava_coordinator)
