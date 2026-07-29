"""Telegram handler registration."""

from __future__ import annotations

from typing import Any

from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from app.bot.handlers import (
    add_workout_handler,
    baseline_handler,
    callback_handler,
    cancel_handler,
    delete_handler,
    document_handler,
    global_error_handler,
    help_handler,
    profile_handler,
    start_handler,
    strava_handler,
    text_handler,
)


def register_handlers(
    application: Application[Any, Any, Any, Any, Any, Any],
) -> None:
    """Register command, callback, text, and safe error handlers."""

    application.add_handler(CommandHandler("start", start_handler))
    application.add_handler(CommandHandler("help", help_handler))
    application.add_handler(CommandHandler("profile", profile_handler))
    application.add_handler(CommandHandler("baseline", baseline_handler))
    application.add_handler(CommandHandler("add_workout", add_workout_handler))
    application.add_handler(CommandHandler("strava", strava_handler))
    application.add_handler(CommandHandler("cancel", cancel_handler))
    application.add_handler(CommandHandler("delete_me", delete_handler))
    application.add_handler(CallbackQueryHandler(callback_handler))
    application.add_handler(MessageHandler(filters.Document.ALL, document_handler))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler)
    )
    application.add_error_handler(global_error_handler)
