"""Adaptive endurance training coach -- Telegram bot entry point."""

import logging
import sys

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters

from app.handlers import start_handler, text_handler


def configure_logging() -> None:
    """Configure application logging, suppressing verbose HTTP request logs."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)


def create_application() -> Application:
    """Build and return a configured Telegram Application."""
    load_dotenv()

    import os

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is not set. "
            "Copy .env.example to .env and add your token."
        )

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler)
    )

    return app


def main() -> None:
    """Start the Telegram bot with long polling."""
    configure_logging()

    app = create_application()

    print("Bot started. Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
