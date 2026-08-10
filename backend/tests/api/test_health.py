"""Operational endpoint tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.dependencies import get_session_factory
from app.api.main import create_app
from app.config import Settings
from app.db.base import Base


@pytest.fixture
def test_settings() -> Settings:
    return Settings(
        environment="test",
        database_url="sqlite+aiosqlite:///:memory:",
    )


@pytest.mark.asyncio
async def test_health_reports_process_liveness(test_settings: Settings) -> None:
    engine = create_async_engine(test_settings.database_url)
    application = create_app(test_settings, engine=engine)
    transport = httpx.ASGITransport(app=application)

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    await engine.dispose()


@pytest.mark.asyncio
async def test_ready_reports_database_success(test_settings: Settings) -> None:
    engine = create_async_engine(test_settings.database_url)
    application = create_app(test_settings, engine=engine)
    transport = httpx.ASGITransport(app=application)

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        response = await client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
    await engine.dispose()


@pytest.mark.asyncio
async def test_lifespan_health_and_readiness_start() -> None:
    settings = Settings(
        environment="test",
        database_url="sqlite+aiosqlite:///:memory:",
        telegram_bot_token=None,
        telegram_bot_username="adaptive_training_coach_bot",
        llm_mode="mock",
        llm_api_key=None,
    )
    engine = create_async_engine(settings.database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    application = create_app(settings, engine=engine)
    transport = httpx.ASGITransport(app=application)

    async with application.router.lifespan_context(application):
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            health_response = await client.get("/health")
            ready_response = await client.get("/ready")

    assert health_response.status_code == 200
    assert health_response.json() == {"status": "ok"}
    assert ready_response.status_code == 200
    assert ready_response.json() == {"status": "ready"}


@pytest.mark.asyncio
async def test_ready_hides_database_failure(test_settings: Settings) -> None:
    engine = create_async_engine(test_settings.database_url)
    application = create_app(test_settings, engine=engine)

    @asynccontextmanager
    async def failing_session() -> AsyncIterator[Any]:
        raise RuntimeError("sensitive upstream detail")
        yield

    def failing_factory() -> async_sessionmaker[AsyncSession]:
        return failing_session  # type: ignore[return-value]

    application.dependency_overrides[get_session_factory] = failing_factory
    transport = httpx.ASGITransport(app=application)

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        response = await client.get("/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
    assert "sensitive" not in response.text
    await engine.dispose()
