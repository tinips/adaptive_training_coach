"""Thin Telegram handlers that delegate all use cases to one facade."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import cast

from langchain_core.messages import HumanMessage
from telegram import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.error import BadRequest, TelegramError
from telegram.ext import ContextTypes

from app.bot import messages
from app.bot.rendering import TelegramResponse
from app.bot.service_protocol import CoachBotService
from app.integrations.llm.vision import ScreenshotExtractionError
from app.schemas.common import TelegramIdentity
from app.schemas.manual_import import ManualWorkoutImportRequest
from app.schemas.training_import import TelegramDocumentUpload
from app.services.workout_screenshot import (
    ActivityImportValidationError,
    ScreenshotDraft,
    WorkoutScreenshotDisabledError,
    WorkoutScreenshotNotFoundError,
    WorkoutScreenshotService,
)

logger = logging.getLogger(__name__)
BOT_SERVICE_KEY = "coach_bot_service"
WORKOUT_SCREENSHOT_SERVICE_KEY = "workout_screenshot_service"
ALLOWED_USER_IDS_KEY = "telegram_allowed_user_ids"
DEV_USER_IDS_KEY = "dev_telegram_user_ids"
_SCREENSHOT_CALLBACK_PREFIX = "screenshot:"


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
    if query.data.startswith(_SCREENSHOT_CALLBACK_PREFIX):
        await _handle_screenshot_callback(update, context, query.data)
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
    try:
        draft = _workout_screenshot_service(context).provide_heart_rate(
            telegram_user_id=identity.telegram_user_id,
            text=message.text,
        )
    except ValueError:
        await message.reply_text(
            "Reply with average / maximum HR, for example: 142 / 168."
        )
        return
    if draft is not None:
        await message.reply_text(
            _format_draft_summary(draft), reply_markup=_screenshot_keyboard(draft)
        )
        return
    response = await _service(context).handle_agent_input(
        identity,
        HumanMessage(
            content=message.text,
            additional_kwargs={"telegram_event_type": "text"},
        ),
    )
    await _deliver(update, response)


async def web_app_data_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = update.effective_message
    identity = _identity(update)
    if (
        message is None
        or message.web_app_data is None
        or identity is None
        or not _authorized(update, context)
    ):
        return
    response = await _service(context).submit_baseline_web_app(
        identity, message.web_app_data.data
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


async def workout_screenshot_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Read a workout screenshot and ask the athlete to confirm before saving."""

    message = update.effective_message
    user = update.effective_user
    photos = message.photo if message is not None else None
    if (
        message is None
        or user is None
        or not photos
        or not _authorized(update, context)
    ):
        return

    status_message = await message.reply_text(messages.SCREENSHOT_READING)
    try:
        # Telegram sends several resolutions of the same photo; the last is
        # the largest.
        telegram_file = await photos[-1].get_file()
        image_bytes = bytes(await telegram_file.download_as_bytearray())
        draft = await _workout_screenshot_service(context).extract_draft(
            telegram_user_id=user.id,
            image_bytes=image_bytes,
        )
    except WorkoutScreenshotDisabledError:
        await status_message.edit_text(messages.SCREENSHOT_DISABLED)
        return
    except WorkoutScreenshotNotFoundError:
        await status_message.edit_text(messages.SCREENSHOT_ATHLETE_NOT_FOUND)
        return
    except ScreenshotExtractionError:
        logger.info("telegram_screenshot_extraction_failed user_id=%s", user.id)
        await status_message.edit_text(messages.SCREENSHOT_EXTRACTION_FAILED)
        return

    await status_message.edit_text(
        _format_draft_summary(draft),
        reply_markup=_screenshot_keyboard(draft),
    )


async def _handle_screenshot_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    callback_data: str,
) -> None:
    query = update.callback_query
    user = update.effective_user
    if query is None or user is None:
        return
    action, _, token = callback_data.removeprefix(
        _SCREENSHOT_CALLBACK_PREFIX
    ).partition(":")
    service = _workout_screenshot_service(context)

    if action == "cancel":
        service.cancel(telegram_user_id=user.id, token=token)
        await _edit_or_reply(query, messages.SCREENSHOT_DISCARDED)
        return

    if action == "heart_rate":
        if not service.request_heart_rate(telegram_user_id=user.id, token=token):
            await _edit_or_reply(query, messages.SCREENSHOT_DRAFT_EXPIRED)
            return
        await _edit_or_reply(
            query,
            "Heart rate was not visible. Reply with average / maximum HR, "
            "for example: 142 / 168.",
        )
        return

    if action != "confirm":
        return

    try:
        _workout, outcome = await service.confirm(
            telegram_user_id=user.id,
            token=token,
        )
    except WorkoutScreenshotNotFoundError:
        await _edit_or_reply(query, messages.SCREENSHOT_DRAFT_EXPIRED)
        return
    except ActivityImportValidationError as error:
        logger.info(
            "telegram_screenshot_import_invalid user_id=%s reason=%s",
            user.id,
            str(error),
        )
        await _edit_or_reply(query, messages.SCREENSHOT_IMPORT_INVALID)
        return

    text = {
        "inserted": messages.SCREENSHOT_SAVED,
        "updated": messages.SCREENSHOT_UPDATED,
        "unchanged": messages.SCREENSHOT_UNCHANGED,
    }[outcome]
    await _edit_or_reply(query, text)


async def _edit_or_reply(query: CallbackQuery, text: str) -> None:
    try:
        await query.edit_message_text(text)
    except BadRequest as exc:
        if "message is not modified" in str(exc).lower():
            return
        logger.info("telegram_screenshot_message_edit_unavailable")
        reply_text = getattr(query.message, "reply_text", None)
        if callable(reply_text):
            await cast(Callable[[str], Awaitable[object]], reply_text)(text)


def _format_draft_summary(draft: ScreenshotDraft) -> str:
    request: ManualWorkoutImportRequest = draft.request
    minutes, seconds = divmod(request.duration_seconds, 60)
    lines = [
        messages.SCREENSHOT_DRAFT_HEADER,
        f"{request.discipline.title()} - {request.source_app_name}",
        f"{request.started_at:%Y-%m-%d %H:%M}",
        f"Duration: {minutes}:{seconds:02d}",
    ]
    if request.distance_meters is not None:
        lines.append(f"Distance: {request.distance_meters:.0f} m")
    if request.calories_active_kcal is not None:
        lines.append(f"Active calories: {request.calories_active_kcal:.0f} kcal")
    if request.average_heart_rate is not None:
        lines.append(f"Avg heart rate: {request.average_heart_rate:.0f} bpm")
    if request.max_heart_rate is not None:
        lines.append(f"Max heart rate: {request.max_heart_rate:.0f} bpm")
    if request.swimming is not None:
        swim = request.swimming
        if swim.total_lengths is not None:
            lines.append(f"Lengths: {swim.total_lengths}")
        if swim.primary_stroke is not None:
            lines.append(f"Stroke: {swim.primary_stroke.title()}")
        if swim.total_strokes is not None:
            lines.append(f"Total strokes: {swim.total_strokes}")
    lines.append("")
    lines.append(messages.SCREENSHOT_CONFIRM_PROMPT)
    return "\n".join(lines)


def _screenshot_keyboard(draft: ScreenshotDraft) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(
            messages.SCREENSHOT_CONFIRM_BUTTON,
            callback_data=f"screenshot:confirm:{draft.token}",
        )
    ]
    if draft.request.average_heart_rate is None or draft.request.max_heart_rate is None:
        buttons.append(
            InlineKeyboardButton(
                "Add heart rate", callback_data=f"screenshot:heart_rate:{draft.token}"
            )
        )
    buttons.append(
        InlineKeyboardButton(
            messages.SCREENSHOT_CANCEL_BUTTON,
            callback_data=f"screenshot:cancel:{draft.token}",
        )
    )
    return InlineKeyboardMarkup([buttons])


def _workout_screenshot_service(
    context: ContextTypes.DEFAULT_TYPE,
) -> WorkoutScreenshotService:
    return cast(
        WorkoutScreenshotService,
        context.application.bot_data[WORKOUT_SCREENSHOT_SERVICE_KEY],
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
