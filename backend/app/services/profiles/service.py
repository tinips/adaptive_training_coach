"""Owned reads of the current mandatory athlete profile."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.repositories.equipment import EquipmentRepository
from app.repositories.profiles import ProfileRepository
from app.schemas.equipment import EquipmentAccessItem
from app.schemas.profile import PersistedMandatoryProfileData, PersistedTrainingGoalData


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
            equipment = await EquipmentRepository(session).selected_catalog(
                athlete_id=user_id
            )
            goal = await ProfileRepository(session).get_training_goal(user_id=user_id)
            return PersistedMandatoryProfileData(
                birth_year=profile.birth_year,
                gender=profile.gender,
                weight_kg=profile.weight_kg,
                height_cm=profile.height_cm,
                availability_text=profile.availability_text,
                equipment_access=tuple(
                    EquipmentAccessItem(
                        discipline=item.discipline,
                        display_name=item.display_name,
                        importance=item.importance,
                    )
                    for item in equipment
                ),
                health_limitations_text=profile.health_limitations_text,
                training_goal=(
                    PersistedTrainingGoalData(
                        main_goal=goal.main_goal,
                        target_outcome=goal.target_outcome,
                        event_date=goal.event_date,
                        secondary_priority=goal.secondary_priority,
                        status=goal.status,
                    )
                    if goal is not None
                    else None
                ),
            )
