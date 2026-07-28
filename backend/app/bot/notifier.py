"""Ownership-safe Telegram notifications initiated outside update handlers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from telegram import Bot

from app.bot import messages
from app.repositories.users import UserRepository

TelegramSender = Callable[[int, str], Awaitable[None]]


class TelegramInitialSyncNotifier:
    """Resolve the OAuth owner and notify only that user's Telegram chat."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        bot_token: SecretStr,
        sender: TelegramSender | None = None,
    ) -> None:
        token = bot_token.get_secret_value()
        if not token:
            raise ValueError("A Telegram bot token is required for notifications.")
        self._session_factory = session_factory
        self._bot_token = token
        self._sender = sender or self._send_with_bot

    async def notify_initial_sync_succeeded(self, *, user_id: UUID) -> None:
        """Map an internal user to its stable Telegram ID before delivery."""

        async with self._session_factory() as session:
            user = await UserRepository(session).require_by_id(user_id)
            telegram_user_id = user.telegram_user_id
        await self._sender(
            telegram_user_id,
            messages.STRAVA_INITIAL_IMPORT_COMPLETE,
        )

    async def _send_with_bot(self, chat_id: int, text: str) -> None:
        async with Bot(token=self._bot_token) as bot:
            await bot.send_message(chat_id=chat_id, text=text)
