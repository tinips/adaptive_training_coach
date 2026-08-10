"""Repository for stable Telegram users and lifecycle state."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from app.domain.enums import UserStatus
from app.repositories.errors import OwnedRecordNotFoundError


class UserRepository:
    """Persist users without taking transaction ownership from services."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        """Return the authenticated local user identity."""

        return await self._session.get(User, user_id)

    async def require_by_id(self, user_id: uuid.UUID) -> User:
        """Return a local user or raise a non-enumerating not-found error."""

        user = await self.get_by_id(user_id)
        if user is None:
            raise OwnedRecordNotFoundError("user not found")
        return user

    async def get_by_telegram_id(self, telegram_user_id: int) -> User | None:
        """Resolve the stable identity received from Telegram."""

        statement = select(User).where(
            User.telegram_user_id == telegram_user_id,
        )
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def get_or_create(
        self,
        *,
        telegram_user_id: int,
        telegram_username: str | None,
        first_name: str | None,
        language_code: str = "en",
        timezone: str | None = None,
    ) -> tuple[User, bool]:
        """Create one identity atomically and refresh mutable Telegram metadata."""

        existing = await self.get_by_telegram_id(telegram_user_id)
        if existing is not None:
            self._update_identity(
                existing,
                telegram_username=telegram_username,
                first_name=first_name,
                language_code=language_code,
                timezone=timezone,
            )
            await self._session.flush()
            return existing, False

        user = User(
            telegram_user_id=telegram_user_id,
            telegram_username=telegram_username,
            first_name=first_name,
            language_code=language_code,
            timezone=timezone,
        )
        try:
            async with self._session.begin_nested():
                self._session.add(user)
                await self._session.flush()
        except IntegrityError:
            existing = await self.get_by_telegram_id(telegram_user_id)
            if existing is None:
                raise
            self._update_identity(
                existing,
                telegram_username=telegram_username,
                first_name=first_name,
                language_code=language_code,
                timezone=timezone,
            )
            await self._session.flush()
            return existing, False
        return user, True

    async def update_status(
        self,
        *,
        user_id: uuid.UUID,
        status: UserStatus,
    ) -> User:
        """Update lifecycle state for the authenticated local user."""

        user = await self.require_by_id(user_id)
        user.status = status
        await self._session.flush()
        return user

    async def delete(self, *, user_id: uuid.UUID) -> bool:
        """Delete the authenticated user and database-cascaded personal data."""

        user = await self.get_by_id(user_id)
        if user is None:
            return False
        await self._session.delete(user)
        await self._session.flush()
        return True

    @staticmethod
    def _update_identity(
        user: User,
        *,
        telegram_username: str | None,
        first_name: str | None,
        language_code: str,
        timezone: str | None,
    ) -> None:
        user.telegram_username = telegram_username
        user.first_name = first_name
        user.language_code = language_code
        if timezone is not None:
            user.timezone = timezone
