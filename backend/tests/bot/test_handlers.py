"""Thin Telegram delivery delegation tests."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, call

import pytest
from telegram import Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from app.bot import handlers, keyboards, messages
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
        handle_agent_input=AsyncMock(
            return_value=TelegramResponse(
                messages.WELCOME,
                keyboards.welcome_keyboard(),
            )
        ),
    )
    update = _update()

    await handlers.start_handler(
        cast(Update, update),
        cast(ContextTypes.DEFAULT_TYPE, _context(service)),
    )

    identity = service.handle_agent_input.await_args.args[0]
    assert identity == TelegramIdentity(
        telegram_user_id=8172,
        telegram_username="runner",
        first_name="Ada",
        language_code="es",
    )
    message = service.handle_agent_input.await_args.args[1]
    assert message.content == "/start"
    assert message.additional_kwargs["telegram_event_type"] == "text"
    update.effective_message.reply_text.assert_awaited_once_with(
        messages.WELCOME,
        reply_markup=keyboards.welcome_keyboard(),
    )


@pytest.mark.asyncio
async def test_lifecycle_refresh_preserves_inline_controls_without_extra_message() -> (
    None
):
    inline = keyboards.welcome_keyboard()
    reply = keyboards.onboarding_keyboard()
    service = SimpleNamespace(
        handle_agent_input=AsyncMock(
            return_value=TelegramResponse(
                messages.WELCOME,
                inline,
                user_keyboard=reply,
                refresh_user_keyboard=True,
            )
        ),
    )
    update = _update()

    await handlers.start_handler(
        cast(Update, update),
        cast(ContextTypes.DEFAULT_TYPE, _context(service)),
    )

    assert update.effective_message.reply_text.await_args_list == [
        call(messages.WELCOME, reply_markup=inline),
    ]


@pytest.mark.asyncio
async def test_add_workout_handler_delegates_identity_to_service() -> None:
    service = SimpleNamespace(
        handle_agent_input=AsyncMock(return_value=TelegramResponse("send a file")),
    )
    update = _update()

    await handlers.add_workout_handler(
        cast(Update, update),
        cast(ContextTypes.DEFAULT_TYPE, _context(service)),
    )

    identity, message = service.handle_agent_input.await_args.args
    assert identity == TelegramIdentity(
        telegram_user_id=8172,
        telegram_username="runner",
        first_name="Ada",
        language_code="es",
    )
    assert message.content == "/add_workout"
    update.effective_message.reply_text.assert_awaited_once_with(
        "send a file",
        reply_markup=None,
    )


@pytest.mark.asyncio
async def test_callback_handler_acknowledges_and_delegates_action() -> None:
    service = SimpleNamespace(
        handle_agent_input=AsyncMock(
            return_value=TelegramResponse("next", edit_existing=True)
        ),
    )
    update = _update(callback_data="ob:v1:consent")

    await handlers.callback_handler(
        cast(Update, update),
        cast(ContextTypes.DEFAULT_TYPE, _context(service)),
    )

    update.callback_query.answer.assert_awaited_once()
    message = service.handle_agent_input.await_args.args[1]
    assert message.content == "ob:v1:consent"
    assert message.additional_kwargs["telegram_event_type"] == "callback"
    update.callback_query.edit_message_text.assert_awaited_once_with(
        "next",
        reply_markup=None,
    )


@pytest.mark.asyncio
async def test_goal_confirmation_shows_processing_before_delegating() -> None:
    service = SimpleNamespace(
        handle_agent_input=AsyncMock(
            return_value=TelegramResponse("next", edit_existing=True)
        ),
    )
    update = _update(callback_data="ob:v1:goal:confirm")

    await handlers.callback_handler(
        cast(Update, update),
        cast(ContextTypes.DEFAULT_TYPE, _context(service)),
    )

    assert update.callback_query.edit_message_text.await_args_list == [
        call(messages.GOAL_CATALOG_EXPANSION_PROGRESS),
        call("next", reply_markup=None),
    ]


@pytest.mark.asyncio
async def test_callback_replay_does_not_send_duplicate_message() -> None:
    service = SimpleNamespace(
        handle_agent_input=AsyncMock(
            return_value=TelegramResponse("same", edit_existing=True)
        ),
    )
    update = _update(callback_data="ob:v1:goal:confirm")
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
async def test_callback_edit_failure_falls_back_to_one_new_message() -> None:
    service = SimpleNamespace(
        handle_agent_input=AsyncMock(
            return_value=TelegramResponse("replacement", edit_existing=True)
        ),
    )
    update = _update(callback_data="nav:v1:privacy")
    update.callback_query.edit_message_text.side_effect = BadRequest(
        "Message can't be edited"
    )

    await handlers.callback_handler(
        cast(Update, update),
        cast(ContextTypes.DEFAULT_TYPE, _context(service)),
    )

    update.callback_query.answer.assert_awaited_once()
    update.effective_message.reply_text.assert_awaited_once_with(
        "replacement",
        reply_markup=None,
    )


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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("supplied_filename", "expected_hint"),
    [
        ("../../Health Export.zip", "../../Health Export.zip"),
        (None, "training-file"),
    ],
)
async def test_document_handler_delegates_metadata_download_and_progress(
    tmp_path: Path,
    supplied_filename: str | None,
    expected_hint: str,
) -> None:
    status_message = SimpleNamespace(edit_text=AsyncMock())
    telegram_file = SimpleNamespace(download_to_drive=AsyncMock())
    document = SimpleNamespace(
        file_id="telegram-file",
        file_unique_id="telegram-unique",
        file_name=supplied_filename,
        file_size=1234,
        get_file=AsyncMock(return_value=telegram_file),
    )
    message = SimpleNamespace(
        document=document,
        reply_text=AsyncMock(return_value=status_message),
    )
    update = SimpleNamespace(
        update_id=987,
        effective_user=SimpleNamespace(
            id=8172,
            username="runner",
            first_name="Ada",
            language_code="en",
        ),
        effective_message=message,
        callback_query=None,
    )

    async def handle_document(
        identity: TelegramIdentity,
        metadata: object,
        download: object,
        progress: object,
    ) -> TelegramResponse:
        del identity, metadata
        destination = tmp_path / "generated.zip"
        await download(destination)  # type: ignore[operator]
        await progress("detecting_format")  # type: ignore[operator]
        await progress("not_a_real_stage")  # type: ignore[operator]
        return TelegramResponse("complete")

    service = SimpleNamespace(handle_document=AsyncMock(side_effect=handle_document))

    await handlers.document_handler(
        cast(Update, update),
        cast(ContextTypes.DEFAULT_TYPE, _context(service)),
    )

    metadata = service.handle_document.await_args.args[1]
    assert metadata.display_filename == expected_hint
    assert metadata.update_id == 987
    message.reply_text.assert_awaited_once_with(
        messages.TRAINING_FILE_PROGRESS["validating_file"]
    )
    telegram_file.download_to_drive.assert_awaited_once_with(
        custom_path=tmp_path / "generated.zip"
    )
    status_message.edit_text.assert_any_await(
        messages.TRAINING_FILE_PROGRESS["detecting_format"]
    )
    status_message.edit_text.assert_awaited_with(
        "complete",
        reply_markup=None,
    )
