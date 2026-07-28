"""Thin Telegram handlers that delegate all use cases to one facade."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import cast

from telegram import Update
from telegram.error import BadRequest, TelegramError
from telegram.ext import ContextTypes

from app.bot import messages
from app.bot.rendering import TelegramResponse
from app.bot.service_protocol import CoachBotService
from app.schemas.common import TelegramIdentity
from app.services.apple_health import TelegramDocumentUpload

logger = logging.getLogger(__name__)
BOT_SERVICE_KEY = "coach_bot_service"


async def start_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    await _delegate(update, context, "start")


async def help_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    del context
    await _deliver(update, TelegramResponse(messages.HELP))


async def profile_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    await _delegate(update, context, "profile")


async def baseline_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    await _delegate(update, context, "baseline")


async def strava_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    await _delegate(update, context, "strava")


async def cancel_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    await _delegate(update, context, "cancel")


async def delete_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    await _delegate(update, context, "delete_me")


async def callback_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    if query is None or query.data is None:
        return
    await query.answer()
    identity = _identity(update)
    if identity is None:
        return
    response = await _service(context).handle_callback(identity, query.data)
    await _deliver(update, response)


async def text_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = update.effective_message
    identity = _identity(update)
    if message is None or message.text is None or identity is None:
        return
    response = await _service(context).handle_text(identity, message.text)
    await _deliver(update, response)


async def document_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Pass a Telegram document to the import service without naming temp files."""

    message = update.effective_message
    identity = _identity(update)
    document = message.document if message is not None else None
    if message is None or identity is None or document is None:
        return
    status_message = await message.reply_text(
        messages.APPLE_HEALTH_PROGRESS["validating_archive"]
    )

    async def download(destination: Path) -> None:
        telegram_file = await document.get_file()
        await telegram_file.download_to_drive(custom_path=destination)

    async def progress(stage: str) -> None:
        text = messages.APPLE_HEALTH_PROGRESS.get(stage)
        if text is None:
            return
        try:
            await status_message.edit_text(text)
        except BadRequest as exc:
            if "message is not modified" not in str(exc).lower():
                logger.info("Telegram import progress edit unavailable")

    response = await _service(context).handle_document(
        identity,
        TelegramDocumentUpload(
            file_id=document.file_id,
            file_unique_id=document.file_unique_id,
            display_filename=document.file_name or "Apple Health export.zip",
            file_size=document.file_size,
            update_id=update.update_id,
        ),
        download,
        progress,
    )
    try:
        await status_message.edit_text(
            response.text,
            reply_markup=response.keyboard,
        )
    except BadRequest:
        await message.reply_text(response.text, reply_markup=response.keyboard)


async def global_error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Log a safe error code and hide all internal details from Telegram."""

    error_type = type(context.error).__name__ if context.error else "Unknown"
    logger.error("Unhandled Telegram update error type=%s", error_type)
    if isinstance(update, Update):
        try:
            await _deliver(update, TelegramResponse(messages.GENERIC_ERROR))
        except TelegramError:
            logger.warning("Could not deliver neutral Telegram error response")


async def _delegate(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    operation: str,
) -> None:
    identity = _identity(update)
    if identity is None:
        return
    service = _service(context)
    method = cast(
        Callable[[TelegramIdentity], Awaitable[TelegramResponse]],
        getattr(service, operation),
    )
    response = await method(identity)
    await _deliver(update, response)


def _identity(update: Update) -> TelegramIdentity | None:
    user = update.effective_user
    if user is None:
        return None
    return TelegramIdentity(
        telegram_user_id=user.id,
        telegram_username=user.username,
        first_name=user.first_name,
        language_code=user.language_code or "en",
    )


def _service(context: ContextTypes.DEFAULT_TYPE) -> CoachBotService:
    return cast(
        CoachBotService,
        context.application.bot_data[BOT_SERVICE_KEY],
    )


async def _deliver(update: Update, response: TelegramResponse) -> None:
    query = update.callback_query
    message = update.effective_message
    if message is None:
        return

    if response.edit_existing and query is not None:
        try:
            await query.edit_message_text(
                response.text,
                reply_markup=response.keyboard,
            )
            return
        except BadRequest as exc:
            if "message is not modified" in str(exc).lower():
                return
            logger.info("Telegram message edit unavailable; sending a new message")

    await message.reply_text(response.text, reply_markup=response.keyboard)
