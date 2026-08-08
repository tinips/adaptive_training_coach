"""Ownership-scoped persistence for deterministic profile settings edits."""

from __future__ import annotations

import uuid
from typing import cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ProfileSettingsSession
from app.domain.enums import ProfileSettingsStep


class ProfileSettingsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_or_create(self, *, user_id: uuid.UUID) -> ProfileSettingsSession:
        row = await self._session.scalar(
            select(ProfileSettingsSession)
            .where(ProfileSettingsSession.user_id == user_id)
            .with_for_update()
        )
        if row is None:
            row = ProfileSettingsSession(user_id=user_id)
            self._session.add(row)
            await self._session.flush()
        return row

    async def get(self, *, user_id: uuid.UUID) -> ProfileSettingsSession | None:
        return cast(
            ProfileSettingsSession | None,
            await self._session.scalar(
                select(ProfileSettingsSession).where(
                    ProfileSettingsSession.user_id == user_id
                )
            ),
        )

    async def save(
        self,
        *,
        user_id: uuid.UUID,
        step: ProfileSettingsStep,
        pending: dict[str, object],
    ) -> ProfileSettingsSession:
        row = await self.get_or_create(user_id=user_id)
        row.current_step = step
        row.pending_answers = pending
        await self._session.flush()
        return row
