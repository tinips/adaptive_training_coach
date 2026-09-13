"""Telegram baseline Web App endpoint tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import create_async_engine

from app.api.main import create_app
from app.config import Settings
from app.schemas.common import TelegramIdentity


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
    assert "running.recent_race_effort" in response.text
    assert "Maximal / race effort" in response.text
    assert "triathlon.open_water_confidence" in response.text
    await engine.dispose()


@pytest.mark.asyncio
async def test_baseline_web_app_keeps_validation_failures_in_the_form() -> None:
    settings = Settings(
        environment="test",
        database_url="sqlite+aiosqlite:///:memory:",
        telegram_bot_token=SecretStr("test-token"),
    )
    engine = create_async_engine(settings.database_url)
    application = create_app(settings, engine=engine)
    transport = httpx.ASGITransport(app=application)
    identity = TelegramIdentity(
        telegram_user_id=123,
        telegram_username=None,
        first_name=None,
        language_code="en",
    )
    result = SimpleNamespace(
        kind="baseline_validation_error",
        error_code="triathlon.prior_experience",
    )

    with (
        patch(
            "app.api.routes.baseline_web_app.telegram_web_app_identity",
            return_value=identity,
        ),
        patch("app.api.routes.baseline_web_app.OnboardingService") as service_class,
    ):
        service_class.return_value.submit_baseline_form = AsyncMock(return_value=result)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/webapp/baseline/submit",
                json={"init_data": "signed-data", "values": {}},
            )

    assert response.status_code == 422
    assert response.json() == {"detail": "triathlon.prior_experience"}
    await engine.dispose()
