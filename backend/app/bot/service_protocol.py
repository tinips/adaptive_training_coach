"""Application-service contract consumed by thin Telegram handlers."""

from __future__ import annotations

from typing import Protocol

from app.bot.rendering import TelegramResponse
from app.schemas.common import TelegramIdentity


class CoachBotService(Protocol):
    """Facade that owns all stateful bot use cases."""

    async def start(self, identity: TelegramIdentity) -> TelegramResponse: ...

    async def handle_callback(
        self,
        identity: TelegramIdentity,
        callback_data: str,
    ) -> TelegramResponse: ...

    async def handle_text(
        self,
        identity: TelegramIdentity,
        text: str,
    ) -> TelegramResponse: ...

    async def profile(self, identity: TelegramIdentity) -> TelegramResponse: ...

    async def baseline(self, identity: TelegramIdentity) -> TelegramResponse: ...

    async def strava(self, identity: TelegramIdentity) -> TelegramResponse: ...

    async def cancel(self, identity: TelegramIdentity) -> TelegramResponse: ...

    async def delete_me(self, identity: TelegramIdentity) -> TelegramResponse: ...
