"""Telegram baseline Web App endpoint tests."""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from app.api.main import create_app
from app.config import Settings


@pytest.mark.asyncio
async def test_baseline_web_app_serves_adaptive_discipline_fields() -> None:
    settings = Settings(
        environment="test",
        database_url="sqlite+aiosqlite:///:memory:",
    )
    engine = create_async_engine(settings.database_url)
    application = create_app(settings, engine=engine)
    transport = httpx.ASGITransport(app=application)

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        response = await client.get(
            "/webapp/baseline",
            params={"fields": "running.typical_weekly_sessions"},
        )

    assert response.status_code == 200
    assert "Your training baseline" in response.text
    assert "running.typical_weekly_sessions" in response.text
    assert "triathlon.open_water_confidence" in response.text
    await engine.dispose()
