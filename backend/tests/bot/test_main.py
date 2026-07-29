"""Production bot composition tests without Telegram network access."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from telegram.constants import ParseMode
from telegram.ext import CommandHandler, MessageHandler, filters

from app.bot.handlers import (
    BOT_SERVICE_KEY,
    add_workout_handler,
    document_handler,
)
from app.bot.main import build_runtime, create_application
from app.bot.service_protocol import CoachBotService
from app.config import Settings
from app.db.base import Base


def test_create_application_registers_handlers_and_injects_facade() -> None:
    settings = Settings(
        environment="test",
        database_url="sqlite+aiosqlite:///:memory:",
        telegram_bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi",
        telegram_bot_username="adaptive_training_coach_bot",
    )
    service = AsyncMock(spec=CoachBotService)

    application = create_application(settings, service=service)

    assert application.bot_data[BOT_SERVICE_KEY] is service
    assert len(application.handlers[0]) == 11
    registered = [
        handler
        for handler in application.handlers[0]
        if isinstance(handler, CommandHandler)
        and handler.commands == frozenset({"add_workout"})
    ]
    assert len(registered) == 1
    assert registered[0].callback is add_workout_handler
    assert application.error_handlers
    assert application.bot.defaults.parse_mode == ParseMode.HTML


def test_create_application_requires_token_without_exposing_a_value() -> None:
    settings = Settings(
        environment="test",
        database_url="sqlite+aiosqlite:///:memory:",
        telegram_bot_token=None,
        telegram_bot_username="adaptive_training_coach_bot",
    )

    with pytest.raises(RuntimeError, match="TELEGRAM_BOT_TOKEN"):
        create_application(settings, service=AsyncMock(spec=CoachBotService))


@pytest.mark.asyncio
async def test_runtime_without_strava_registers_document_handler() -> None:
    settings = Settings(
        environment="test",
        database_url="sqlite+aiosqlite:///:memory:",
        telegram_bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi",
        telegram_bot_username="adaptive_training_coach_bot",
        llm_mode="mock",
        llm_api_key=None,
        strava_enabled=False,
        strava_client_id=None,
        strava_client_secret=None,
        strava_webhook_verify_token=None,
        strava_webhook_subscription_id=None,
    )
    engine = create_async_engine(settings.database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    runtime = build_runtime(settings, engine=engine)

    assert runtime.settings.strava_enabled is False
    assert runtime.settings.strava_client_id is None
    assert runtime.settings.strava_client_secret is None

    try:
        await runtime.recover()
        application = create_application(settings, runtime=runtime)

        document_handlers = [
            handler
            for handler in application.handlers[0]
            if isinstance(handler, MessageHandler)
            and handler.callback is document_handler
        ]

        assert application.bot_data[BOT_SERVICE_KEY] is runtime.service
        assert len(document_handlers) == 1
        assert document_handlers[0].filters is filters.Document.ALL
    finally:
        await runtime.aclose()
