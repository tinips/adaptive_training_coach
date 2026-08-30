"""Ownership-scoped profile and training-goal persistence."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utc_now
from app.db.models import AthleteProfile, TrainingGoal, User
from app.domain.enums import AthleteGender, TrainingGoalStatus
from app.repositories.errors import OwnedRecordNotFoundError


@dataclass(frozen=True, slots=True)
class AthleteProfileContext:
    availability_text: str | None
    weekly_availability_jsonb: dict[str, object] | None
    health_limitations_text: str | None


class ProfileRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def lock_owner(self, *, user_id: uuid.UUID) -> User:
        user = await self._session.scalar(
            select(User).where(User.id == user_id).with_for_update()
        )
        if user is None:
            raise OwnedRecordNotFoundError("user not found")
        return user

    async def get_athlete_profile(
        self, *, user_id: uuid.UUID, profile_id: uuid.UUID | None = None
    ) -> AthleteProfile | None:
        statement = select(AthleteProfile).where(AthleteProfile.user_id == user_id)
        if profile_id is not None:
            statement = statement.where(AthleteProfile.id == profile_id)
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def get_athlete_profile_context(
        self, *, user_id: uuid.UUID
    ) -> AthleteProfileContext | None:
        profile = await self.get_athlete_profile(user_id=user_id)
        if profile is None:
            return None
        return AthleteProfileContext(
            availability_text=profile.availability_text,
            weekly_availability_jsonb=profile.weekly_availability_jsonb,
            health_limitations_text=profile.health_limitations_text,
        )

    async def get_training_goal(
        self, *, user_id: uuid.UUID, goal_id: uuid.UUID | None = None
    ) -> TrainingGoal | None:
        statement = select(TrainingGoal).where(TrainingGoal.user_id == user_id)
        if goal_id is not None:
            statement = statement.where(TrainingGoal.id == goal_id)
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def delete_training_goal(self, *, user_id: uuid.UUID) -> None:
        """Delete only the requesting athlete's current goal."""

        await self._session.execute(
            delete(TrainingGoal).where(TrainingGoal.user_id == user_id)
        )
        await self._session.flush()

    async def upsert_training_goal(
        self,
        *,
        user_id: uuid.UUID,
        main_goal: str,
        event_date: date | None,
        target_outcome: str,
        secondary_priority: str | None,
        original_description: str,
        goal_template_id: uuid.UUID | None = None,
        supporting_goal_template_id: uuid.UUID | None = None,
        target_distance_km: float | None = None,
        target_elevation_m: float | None = None,
        target_pace_seconds_per_km: float | None = None,
        target_swim_pace_seconds_per_100m: float | None = None,
        target_average_speed_kph: float | None = None,
        target_finish_time_seconds: int | None = None,
        goal_metadata_jsonb: dict[str, object] | None = None,
    ) -> TrainingGoal:
        # Goal template changes determine baseline eligibility. Serialize them
        # with import-time baseline creation, which locks this same owner row
        # before reading the live goal.
        await self.lock_owner(user_id=user_id)
        goal = await self.get_training_goal(user_id=user_id)
        if goal is None:
            goal = TrainingGoal(user_id=user_id)
            self._session.add(goal)
        goal.main_goal = main_goal
        goal.event_date = event_date
        goal.target_outcome = target_outcome
        goal.secondary_priority = secondary_priority
        goal.goal_template_id = goal_template_id
        goal.supporting_goal_template_id = supporting_goal_template_id
        goal.target_distance_km = target_distance_km
        goal.target_elevation_m = target_elevation_m
        goal.target_pace_seconds_per_km = target_pace_seconds_per_km
        goal.target_swim_pace_seconds_per_100m = target_swim_pace_seconds_per_100m
        goal.target_average_speed_kph = target_average_speed_kph
        goal.target_finish_time_seconds = target_finish_time_seconds
        goal.goal_metadata_jsonb = goal_metadata_jsonb
        goal.original_description = original_description
        goal.status = TrainingGoalStatus.CONFIRMED
        await self._session.flush()
        return goal

    async def update_athlete_profile_fields(
        self, *, user_id: uuid.UUID, payload: Mapping[str, object]
    ) -> AthleteProfile:
        return await self._update_profile(
            user_id, payload, {"age", "birth_year", "gender", "weight_kg", "height_cm"}
        )

    async def update_athlete_profile_context_fields(
        self, *, user_id: uuid.UUID, payload: Mapping[str, object]
    ) -> AthleteProfile:
        return await self._update_profile(
            user_id,
            payload,
            {
                "availability_text",
                "weekly_availability_jsonb",
                "health_limitations_text",
            },
        )

    async def _update_profile(
        self, user_id: uuid.UUID, payload: Mapping[str, object], allowed: set[str]
    ) -> AthleteProfile:
        if not payload or not set(payload).issubset(allowed):
            raise ValueError("unsupported athlete profile update field")
        profile = await self._session.scalar(
            update(AthleteProfile)
            .where(AthleteProfile.user_id == user_id)
            .values(**dict(payload), updated_at=utc_now())
            .returning(AthleteProfile)
        )
        if profile is None:
            raise OwnedRecordNotFoundError("athlete profile not found")
        await self._session.flush()
        return profile

    async def update_training_goal_fields(
        self, *, user_id: uuid.UUID, payload: Mapping[str, object]
    ) -> TrainingGoal:
        if not payload or not set(payload).issubset(
            {
                "main_goal",
                "target_outcome",
                "event_date",
                "secondary_priority",
                "goal_template_id",
                "supporting_goal_template_id",
                "target_distance_km",
                "target_elevation_m",
                "target_pace_seconds_per_km",
                "target_swim_pace_seconds_per_100m",
                "target_average_speed_kph",
                "target_finish_time_seconds",
                "goal_metadata_jsonb",
            }
        ):
            raise ValueError("unsupported training goal update field")
        if {"goal_template_id", "supporting_goal_template_id"} & set(payload):
            await self.lock_owner(user_id=user_id)
        goal = await self._session.scalar(
            update(TrainingGoal)
            .where(TrainingGoal.user_id == user_id)
            .values(**dict(payload), updated_at=utc_now())
            .returning(TrainingGoal)
        )
        if goal is None:
            raise OwnedRecordNotFoundError("training goal not found")
        await self._session.flush()
        return goal

    async def upsert_mandatory_athlete_profile(
        self,
        *,
        user_id: uuid.UUID,
        birth_year: int,
        gender: AthleteGender,
        weight_kg: float,
        height_cm: float,
    ) -> AthleteProfile:
        await self._require_user(user_id)
        profile = await self.get_athlete_profile(user_id=user_id)
        if profile is None:
            profile = AthleteProfile(user_id=user_id, age=utc_now().year - birth_year)
            self._session.add(profile)
        profile.birth_year = birth_year
        profile.gender = gender
        profile.weight_kg = weight_kg
        profile.height_cm = height_cm
        profile.age = utc_now().year - birth_year
        await self._session.flush()
        return profile

    async def _require_user(self, user_id: uuid.UUID) -> None:
        if (
            await self._session.scalar(select(User.id).where(User.id == user_id))
            is None
        ):
            raise OwnedRecordNotFoundError("user not found")
