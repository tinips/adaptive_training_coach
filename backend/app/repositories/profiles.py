"""Ownership-scoped reads and retained profile persistence operations."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AthleteProfile,
    AvailabilityRule,
    BaselinePreference,
    CoachPreference,
    EquipmentAccess,
    HealthConstraint,
    TrainingGoal,
    User,
)
from app.domain.enums import (
    BaselinePreferenceStatus,
    BaselineSource,
    TrainingGoalStatus,
)
from app.repositories.errors import OwnedRecordNotFoundError


@dataclass(frozen=True, slots=True)
class ProfileBundle:
    """All normalized profile records retained for one authenticated user."""

    athlete_profile: AthleteProfile | None
    training_goal: TrainingGoal | None
    availability_rules: tuple[AvailabilityRule, ...]
    equipment_access: tuple[EquipmentAccess, ...]
    health_constraints: tuple[HealthConstraint, ...]
    coach_preference: CoachPreference | None
    baseline_preference: BaselinePreference | None


class ProfileRepository:
    """Read historical profiles and persist the one supported goal path."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def lock_owner(self, *, user_id: uuid.UUID) -> User:
        """Lock the profile owner for a transactional lifecycle transition."""

        user = await self._session.scalar(
            select(User).where(User.id == user_id).with_for_update(),
        )
        if user is None:
            raise OwnedRecordNotFoundError("user not found")
        return user

    async def get_athlete_profile(
        self,
        *,
        user_id: uuid.UUID,
        profile_id: uuid.UUID | None = None,
    ) -> AthleteProfile | None:
        statement = select(AthleteProfile).where(AthleteProfile.user_id == user_id)
        if profile_id is not None:
            statement = statement.where(AthleteProfile.id == profile_id)
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def get_training_goal(
        self,
        *,
        user_id: uuid.UUID,
        goal_id: uuid.UUID | None = None,
    ) -> TrainingGoal | None:
        statement = select(TrainingGoal).where(TrainingGoal.user_id == user_id)
        if goal_id is not None:
            statement = statement.where(TrainingGoal.id == goal_id)
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def get_bundle(self, *, user_id: uuid.UUID) -> ProfileBundle:
        """Load existing normalized data with every query constrained by owner."""

        athlete_profile = await self.get_athlete_profile(user_id=user_id)
        training_goal = await self.get_training_goal(user_id=user_id)
        availability = await self._session.scalars(
            select(AvailabilityRule)
            .where(AvailabilityRule.user_id == user_id)
            .order_by(AvailabilityRule.day_of_week),
        )
        equipment = await self._session.scalars(
            select(EquipmentAccess)
            .where(EquipmentAccess.user_id == user_id)
            .order_by(EquipmentAccess.equipment_type),
        )
        constraints = await self._session.scalars(
            select(HealthConstraint)
            .where(HealthConstraint.user_id == user_id)
            .order_by(HealthConstraint.created_at, HealthConstraint.id),
        )
        coach = await self._session.scalar(
            select(CoachPreference).where(CoachPreference.user_id == user_id),
        )
        baseline = await self._session.scalar(
            select(BaselinePreference).where(BaselinePreference.user_id == user_id),
        )
        return ProfileBundle(
            athlete_profile=athlete_profile,
            training_goal=training_goal,
            availability_rules=tuple(availability.all()),
            equipment_access=tuple(equipment.all()),
            health_constraints=tuple(constraints.all()),
            coach_preference=coach,
            baseline_preference=baseline,
        )

    async def upsert_conversational_training_goal(
        self,
        *,
        user_id: uuid.UUID,
        main_goal: str,
        event_date: date | None,
        target_outcome: str,
        secondary_priority: str | None,
        original_description: str,
    ) -> TrainingGoal:
        """Persist only an application-validated, explicitly confirmed goal."""

        await self._require_user(user_id)
        goal = await self.get_training_goal(user_id=user_id)
        if goal is None:
            goal = TrainingGoal(user_id=user_id)
            self._session.add(goal)
        goal.main_goal = main_goal
        goal.event_date = event_date
        goal.target_outcome = target_outcome
        goal.secondary_priority = secondary_priority
        goal.original_description = original_description
        goal.status = TrainingGoalStatus.CONFIRMED
        await self._session.flush()
        return goal

    async def upsert_baseline_preference(
        self,
        *,
        user_id: uuid.UUID,
        selected_source: BaselineSource,
        status: BaselinePreferenceStatus,
    ) -> BaselinePreference:
        """Retain post-profile baseline selection for existing complete profiles."""

        await self._require_user(user_id)
        preference = await self._session.scalar(
            select(BaselinePreference).where(BaselinePreference.user_id == user_id),
        )
        if preference is None:
            preference = BaselinePreference(user_id=user_id)
            self._session.add(preference)
        preference.selected_source = selected_source
        preference.status = status
        await self._session.flush()
        return preference

    async def _require_user(self, user_id: uuid.UUID) -> None:
        exists = await self._session.scalar(select(User.id).where(User.id == user_id))
        if exists is None:
            raise OwnedRecordNotFoundError("user not found")
