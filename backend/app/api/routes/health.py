"""Liveness and database readiness endpoints."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.dependencies import get_session_factory

router = APIRouter(tags=["operations"])
logger = logging.getLogger(__name__)

SessionFactory = Annotated[
    async_sessionmaker[AsyncSession],
    Depends(get_session_factory),
]


@router.get("/health")
async def health() -> dict[str, str]:
    """Confirm that the HTTP process is responsive."""

    return {"status": "ok"}


@router.get(
    "/ready",
    responses={503: {"description": "Database unavailable"}},
)
async def ready(session_factory: SessionFactory) -> JSONResponse:
    """Perform a lightweight database query without exposing failures."""

    try:
        async with session_factory() as session:
            await session.execute(text("SELECT 1"))
    except Exception:
        logger.warning("Database readiness check failed", exc_info=False)
        return JSONResponse(status_code=503, content={"status": "unavailable"})
    return JSONResponse(status_code=200, content={"status": "ready"})
