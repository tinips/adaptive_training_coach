"""Ownership-scoped account queries and transactional local deletion."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.repositories.users import UserRepository
from app.schemas.common import TelegramIdentity
from app.services.profiles import ProfileService


class AccountServiceError(RuntimeError):
    """Safe account-layer error."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class AccountQueryService:
    """Produce delivery-neutral account and profile views."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._session_factory = session_factory
        self._profiles = ProfileService(session_factory)

    async def resolve_user_id(
        self,
        identity: TelegramIdentity,
    ) -> uuid.UUID | None:
        async with self._session_factory() as session:
            user = await UserRepository(session).get_by_telegram_id(
                identity.telegram_user_id
            )
            return user.id if user is not None else None

    async def lifecycle(
        self,
        identity: TelegramIdentity,
    ) -> dict[str, Any] | None:
        async with self._session_factory() as session:
            user = await UserRepository(session).get_by_telegram_id(
                identity.telegram_user_id
            )
            if user is None:
                return None
            return {"user_id": user.id, "status": user.status}

    async def profile(
        self,
        identity: TelegramIdentity,
    ) -> dict[str, Any] | None:
        user_id = await self.resolve_user_id(identity)
        if user_id is None:
            return None
        profile = await self._profiles.get(user_id=user_id)
        if profile is None:
            return None
        return profile.model_dump(mode="python")


class AccountService:
    """Delete all local personal data in one database transaction."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._session_factory = session_factory

    async def delete(self, *, user_id: uuid.UUID) -> bool:
        async with self._session_factory.begin() as session:
            return await UserRepository(session).delete(user_id=user_id)
