"""Application-service contract consumed by thin Telegram handlers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Protocol

from app.bot.rendering import TelegramResponse
from app.schemas.common import TelegramIdentity
from app.schemas.training_import import TelegramDocumentUpload


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

    async def handle_document(
        self,
        identity: TelegramIdentity,
        document: TelegramDocumentUpload,
        download: Callable[[Path], Awaitable[None]],
        progress: Callable[[str], Awaitable[None]],
    ) -> TelegramResponse: ...

    async def profile(self, identity: TelegramIdentity) -> TelegramResponse: ...

    async def baseline(self, identity: TelegramIdentity) -> TelegramResponse: ...

    async def add_workout(
        self,
        identity: TelegramIdentity,
    ) -> TelegramResponse: ...

    async def strava(self, identity: TelegramIdentity) -> TelegramResponse: ...

    async def cancel(self, identity: TelegramIdentity) -> TelegramResponse: ...

    async def delete_me(self, identity: TelegramIdentity) -> TelegramResponse: ...
