"""Ownership-scoped account queries and transactional local deletion."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.repositories.athlete_baselines import AthleteBaselineRepository
from app.repositories.users import UserRepository
from app.schemas.baseline import AthleteBaselineData
from app.schemas.common import TelegramIdentity
from app.services.athlete_zones import (
    AthleteDisplayZones,
    resolve_athlete_display_zones,
)
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

    async def zones(
        self,
        identity: TelegramIdentity,
    ) -> AthleteDisplayZones | None:
        user_id = await self.resolve_user_id(identity)
        if user_id is None:
            return None
        profile = await self._profiles.get(user_id=user_id)
        if profile is None:
            return None
        async with self._session_factory() as session:
            saved_baseline = await AthleteBaselineRepository(session).get(
                athlete_id=user_id
            )
        baseline: AthleteBaselineData | None = None
        if saved_baseline is not None:
            try:
                baseline = AthleteBaselineData.model_validate(
                    saved_baseline.baseline_jsonb
                )
            except ValidationError:
                baseline = None
        return resolve_athlete_display_zones(
            birth_year=profile.birth_year,
            baseline=baseline,
            current_year=datetime.now(UTC).year,
        )


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
