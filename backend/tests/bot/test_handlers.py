"""Thin Telegram delivery delegation tests."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from telegram import Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from app.bot import handlers, messages
from app.bot.rendering import TelegramResponse
from app.schemas.common import TelegramIdentity


def _context(service: object, *, error: Exception | None = None) -> Any:
    return SimpleNamespace(
        application=SimpleNamespace(
            bot_data={handlers.BOT_SERVICE_KEY: service},
        ),
        error=error,
    )


def _update(*, callback_data: str | None = None) -> Any:
    message = SimpleNamespace(reply_text=AsyncMock())
    callback = None
    if callback_data is not None:
        callback = SimpleNamespace(
            data=callback_data,
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
    return SimpleNamespace(
        effective_user=SimpleNamespace(
            id=8172,
            username="runner",
            first_name="Ada",
            language_code="es",
        ),
        effective_message=message,
        callback_query=callback,
    )


@pytest.mark.asyncio
async def test_start_handler_delegates_identity_to_service() -> None:
    service = SimpleNamespace(
        start=AsyncMock(return_value=TelegramResponse("delegated")),
    )
    update = _update()

    await handlers.start_handler(
        cast(Update, update),
        cast(ContextTypes.DEFAULT_TYPE, _context(service)),
    )

    identity = service.start.await_args.args[0]
    assert identity == TelegramIdentity(
        telegram_user_id=8172,
        telegram_username="runner",
        first_name="Ada",
        language_code="es",
    )
    update.effective_message.reply_text.assert_awaited_once_with(
        "delegated",
        reply_markup=None,
    )


@pytest.mark.asyncio
async def test_callback_handler_acknowledges_and_delegates_action() -> None:
    service = SimpleNamespace(
        handle_callback=AsyncMock(
            return_value=TelegramResponse("next", edit_existing=True)
        ),
    )
    update = _update(callback_data="ob:v1:set:running")

    await handlers.callback_handler(
        cast(Update, update),
        cast(ContextTypes.DEFAULT_TYPE, _context(service)),
    )

    update.callback_query.answer.assert_awaited_once()
    assert service.handle_callback.await_args.args[1] == "ob:v1:set:running"
    update.callback_query.edit_message_text.assert_awaited_once_with(
        "next",
        reply_markup=None,
    )


@pytest.mark.asyncio
async def test_callback_replay_does_not_send_duplicate_message() -> None:
    service = SimpleNamespace(
        handle_callback=AsyncMock(
            return_value=TelegramResponse("same", edit_existing=True)
        ),
    )
    update = _update(callback_data="ob:v1:set:SPORT_SELECTION:RUNNING")
    update.callback_query.edit_message_text.side_effect = BadRequest(
        "Message is not modified"
    )

    await handlers.callback_handler(
        cast(Update, update),
        cast(ContextTypes.DEFAULT_TYPE, _context(service)),
    )

    update.callback_query.answer.assert_awaited_once()
    update.effective_message.reply_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_global_error_handler_sends_neutral_message() -> None:
    message = SimpleNamespace(reply_text=AsyncMock())
    update = Update(update_id=1, message=cast(Any, message))

    await handlers.global_error_handler(
        update,
        cast(
            ContextTypes.DEFAULT_TYPE,
            _context(object(), error=RuntimeError("sensitive detail")),
        ),
    )

    message.reply_text.assert_awaited_once_with(
        messages.GENERIC_ERROR,
        reply_markup=None,
    )
    assert "sensitive detail" not in messages.GENERIC_ERROR
