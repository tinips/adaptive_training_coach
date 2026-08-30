"""Production bot composition tests without Telegram network access."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from telegram.constants import ParseMode
from telegram.ext import CommandHandler, MessageHandler, filters

from app.bot.handlers import (
    BOT_SERVICE_KEY,
    DEV_USER_IDS_KEY,
    add_workout_handler,
    connect_iphone_handler,
    disconnect_iphone_handler,
    document_handler,
    web_app_data_handler,
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
    assert len(application.handlers[0]) == 13
    registered = [
        handler
        for handler in application.handlers[0]
        if isinstance(handler, CommandHandler)
        and handler.commands == frozenset({"add_workout"})
    ]
    assert len(registered) == 1
    assert registered[0].callback is add_workout_handler
    mobile_commands = {
        command: handler.callback
        for handler in application.handlers[0]
        if isinstance(handler, CommandHandler)
        for command in handler.commands
        if command in {"connect_iphone", "disconnect_iphone"}
    }
    assert mobile_commands == {
        "connect_iphone": connect_iphone_handler,
        "disconnect_iphone": disconnect_iphone_handler,
    }
    web_app_handlers = [
        handler
        for handler in application.handlers[0]
        if isinstance(handler, MessageHandler)
        and handler.callback is web_app_data_handler
    ]
    assert len(web_app_handlers) == 1
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


def test_create_application_registers_development_commands_only_in_development() -> (
    None
):
    settings = Settings(
        environment="development",
        database_url="sqlite+aiosqlite:///:memory:",
        telegram_bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi",
        telegram_bot_username="adaptive_training_coach_bot",
        dev_telegram_user_ids={206072865},
    )

    application = create_application(settings, service=AsyncMock(spec=CoachBotService))

    command_names = {
        command
        for handler in application.handlers[0]
        if isinstance(handler, CommandHandler)
        for command in handler.commands
    }
    assert {
        "dev_step",
        "dev_reset",
        "dev_import_history",
        "dev_reset_goal_equipment",
        "dev_goal",
    } <= command_names
    assert application.bot_data[DEV_USER_IDS_KEY] == frozenset({206072865})


@pytest.mark.asyncio
async def test_runtime_registers_document_handler() -> None:
    settings = Settings(
        environment="test",
        database_url="sqlite+aiosqlite:///:memory:",
        telegram_bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi",
        telegram_bot_username="adaptive_training_coach_bot",
        llm_mode="mock",
        llm_api_key=None,
    )
    engine = create_async_engine(settings.database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    runtime = build_runtime(settings, engine=engine)

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
