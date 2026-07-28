"""Production bot composition tests without Telegram network access."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from telegram.constants import ParseMode

from app.bot.handlers import BOT_SERVICE_KEY
from app.bot.main import create_application
from app.bot.service_protocol import CoachBotService
from app.config import Settings


def test_create_application_registers_handlers_and_injects_facade() -> None:
    settings = Settings(
        environment="test",
        database_url="sqlite+aiosqlite:///:memory:",
        telegram_bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi",
    )
    service = AsyncMock(spec=CoachBotService)

    application = create_application(settings, service=service)

    assert application.bot_data[BOT_SERVICE_KEY] is service
    assert len(application.handlers[0]) == 9
    assert application.error_handlers
    assert application.bot.defaults.parse_mode == ParseMode.HTML


def test_create_application_requires_token_without_exposing_a_value() -> None:
    settings = Settings(
        environment="test",
        database_url="sqlite+aiosqlite:///:memory:",
        telegram_bot_token=None,
    )

    with pytest.raises(RuntimeError, match="TELEGRAM_BOT_TOKEN"):
        create_application(settings, service=AsyncMock(spec=CoachBotService))
