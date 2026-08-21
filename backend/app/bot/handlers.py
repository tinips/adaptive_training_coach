"""Thin Telegram handlers that delegate all use cases to one facade."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import cast

from langchain_core.messages import HumanMessage
from telegram import Update
from telegram.error import BadRequest, TelegramError
from telegram.ext import ContextTypes

from app.bot import messages
from app.bot.rendering import TelegramResponse
from app.bot.service_protocol import CoachBotService
from app.schemas.common import TelegramIdentity
from app.schemas.training_import import TelegramDocumentUpload

logger = logging.getLogger(__name__)
BOT_SERVICE_KEY = "coach_bot_service"
ALLOWED_USER_IDS_KEY = "telegram_allowed_user_ids"
DEV_USER_IDS_KEY = "dev_telegram_user_ids"


async def start_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    await _agent_delegate(update, context, "/start")


async def help_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    await _agent_delegate(update, context, "/help")


async def profile_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    await _agent_delegate(update, context, "/profile")


async def add_workout_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    await _agent_delegate(update, context, "/add_workout")


async def cancel_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    await _agent_delegate(update, context, "/cancel")


async def delete_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    await _agent_delegate(update, context, "/delete_me")


async def dev_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _development_authorized(update, context):
        return
    message = update.effective_message
    if message is not None and message.text is not None:
        await _agent_delegate(update, context, message.text)


async def callback_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    if query is None or query.data is None:
        return
    user = update.effective_user
    logger.info(
        "telegram_callback_received user_id=%s callback=%s",
        user.id if user is not None else None,
        query.data,
    )
    await query.answer()
    logger.info("telegram_callback_acknowledged callback=%s", query.data)
    if not _authorized(update, context):
        logger.warning("telegram_callback_unauthorized callback=%s", query.data)
        return
    if query.data == "ob:v1:goal:confirm":
        try:
            await query.edit_message_text(messages.GOAL_CATALOG_EXPANSION_PROGRESS)
        except BadRequest as exc:
            logger.info(
                "telegram_goal_progress_message_unavailable reason=%s",
                type(exc).__name__,
            )
    identity = _identity(update)
    if identity is None:
        return
    response = await _service(context).handle_agent_input(
        identity,
        HumanMessage(
            content=query.data,
            additional_kwargs={"telegram_event_type": "callback"},
        ),
    )
    logger.info(
        "telegram_callback_handled callback=%s edit_existing=%s",
        query.data,
        response.edit_existing,
    )
    await _deliver(update, response)


async def text_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = update.effective_message
    identity = _identity(update)
    if (
        message is None
        or message.text is None
        or identity is None
        or not _authorized(update, context)
    ):
        return
    response = await _service(context).handle_agent_input(
        identity,
        HumanMessage(
            content=message.text,
            additional_kwargs={"telegram_event_type": "text"},
        ),
    )
    await _deliver(update, response)


async def document_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Pass a Telegram document to the import service without naming temp files."""

    message = update.effective_message
    identity = _identity(update)
    document = message.document if message is not None else None
    if (
        message is None
        or identity is None
        or document is None
        or not _authorized(update, context)
    ):
        return
    status_message = await message.reply_text(
        messages.TRAINING_FILE_PROGRESS["validating_file"]
    )

    async def download(destination: Path) -> None:
        telegram_file = await document.get_file()
        await telegram_file.download_to_drive(custom_path=destination)

    async def progress(stage: str) -> None:
        text = messages.TRAINING_FILE_PROGRESS.get(stage)
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
            display_filename=document.file_name or "training-file",
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
        await message.reply_text(
            response.text,
            reply_markup=response.user_keyboard or response.keyboard,
        )


async def global_error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Log a safe error code and hide all internal details from Telegram."""

    error_type = type(context.error).__name__ if context.error else "Unknown"
    error_code = getattr(context.error, "code", None)
    logger.error(
        "Unhandled Telegram update error type=%s error_code=%s",
        error_type,
        error_code,
    )
    if isinstance(update, Update):
        try:
            await _deliver(update, TelegramResponse(messages.GENERIC_ERROR))
        except TelegramError:
            logger.warning("Could not deliver neutral Telegram error response")


async def _agent_delegate(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    content: str,
) -> None:
    if not _authorized(update, context):
        return
    identity = _identity(update)
    if identity is None:
        return
    response = await _service(context).handle_agent_input(
        identity,
        HumanMessage(
            content=content,
            additional_kwargs={"telegram_event_type": "text"},
        ),
    )
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


def _authorized(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Allow Telegram traffic only from the configured user allowlist."""

    user = update.effective_user
    if user is None:
        return False
    allowed = context.application.bot_data.get(ALLOWED_USER_IDS_KEY)
    # Test-only contexts that do not construct the production application have
    # no security configuration.  The real application always sets this key.
    return allowed is None or user.id in allowed


def _development_authorized(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Allow development shortcuts only for the explicit development allowlist."""

    user = update.effective_user
    if user is None or not _authorized(update, context):
        return False
    development_users = context.application.bot_data.get(DEV_USER_IDS_KEY)
    return development_users is None or user.id in development_users


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

    reply_markup = response.keyboard
    if reply_markup is None and response.button_rows:
        from app.bot.keyboards import dynamic_keyboard

        reply_markup = dynamic_keyboard(response.button_rows)

    if response.edit_existing and query is not None:
        try:
            await query.edit_message_text(
                response.text,
                reply_markup=reply_markup,
            )
            return
        except BadRequest as exc:
            if "message is not modified" in str(exc).lower():
                return
            logger.info("Telegram message edit unavailable; sending a new message")

    await message.reply_text(
        response.text,
        reply_markup=reply_markup or response.user_keyboard,
    )
