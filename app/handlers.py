"""Telegram bot command and message handlers."""

from telegram import Update
from telegram.ext import ContextTypes


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reply to /start with a welcome message."""
    message = update.effective_message
    if message is None:
        return

    user = update.effective_user
    first_name = user.first_name if user else "athlete"

    await message.reply_text(
        f"Hi {first_name}! The adaptive training coach is connected. "
        f"Use /start to see this message again."
    )


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Echo any non-command text message back to the user."""
    message = update.effective_message
    if message is None or message.text is None:
        return

    await message.reply_text(message.text)
