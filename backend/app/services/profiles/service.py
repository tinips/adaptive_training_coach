"""Owned reads of the current mandatory athlete profile."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.repositories.profiles import ProfileRepository
from app.schemas.profile import PersistedMandatoryProfileData


class ProfileService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get(self, *, user_id: uuid.UUID) -> PersistedMandatoryProfileData | None:
        async with self._session_factory() as session:
            profile = await ProfileRepository(session).get_athlete_profile(
                user_id=user_id
            )
            if profile is None or any(
                value is None
                for value in (
                    profile.birth_year,
                    profile.gender,
                    profile.weight_kg,
                    profile.height_cm,
                )
            ):
                return None
            return PersistedMandatoryProfileData(
                birth_year=profile.birth_year,
                gender=profile.gender,
                weight_kg=profile.weight_kg,
                height_cm=profile.height_cm,
                availability_text=profile.availability_text,
                equipment_recommendation_text=profile.equipment_recommendation_text,
                equipment_text=profile.equipment_text,
                health_limitations_text=profile.health_limitations_text,
            )
