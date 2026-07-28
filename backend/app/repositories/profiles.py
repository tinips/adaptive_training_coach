"""Transactional, ownership-scoped normalized athlete profile persistence."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AthleteProfile,
    AvailabilityRule,
    BaselinePreference,
    BodyArea,
    CoachPreference,
    EquipmentAccess,
    EquipmentAccessType,
    EquipmentType,
    GoalType,
    HealthConstraint,
    HealthConstraintType,
    TrainingGoal,
    User,
)
from app.domain.enums import (
    BaselinePreferenceStatus,
    BaselineSource,
    CoachTone,
    DayOfWeek,
    DetailLevel,
    GoalPriority,
    PrimarySport,
)
from app.repositories.errors import OwnedRecordNotFoundError


@dataclass(frozen=True, slots=True)
class AvailabilityRuleInput:
    """Normalized input for one selected training day."""

    day_of_week: DayOfWeek
    available_minutes: int | None
    is_variable: bool


@dataclass(frozen=True, slots=True)
class EquipmentAccessInput:
    """Normalized input for one selected equipment item."""

    equipment_type: EquipmentType
    access_type: EquipmentAccessType = EquipmentAccessType.REGULAR
    access_days: tuple[DayOfWeek, ...] | None = None
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class HealthConstraintInput:
    """Non-diagnostic limitation input with explicit timing."""

    body_area: BodyArea | None
    constraint_type: HealthConstraintType
    normalized_description: str | None = None


@dataclass(frozen=True, slots=True)
class ProfileBundle:
    """All normalized profile records for one authenticated user."""

    athlete_profile: AthleteProfile | None
    training_goal: TrainingGoal | None
    availability_rules: tuple[AvailabilityRule, ...]
    equipment_access: tuple[EquipmentAccess, ...]
    health_constraints: tuple[HealthConstraint, ...]
    coach_preference: CoachPreference | None
    baseline_preference: BaselinePreference | None


class ProfileRepository:
    """Materialize and read a normalized profile in the caller's transaction."""

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
        statement = select(AthleteProfile).where(
            AthleteProfile.user_id == user_id,
        )
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
        """Load a full profile with every query constrained by owner."""

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
            select(CoachPreference).where(
                CoachPreference.user_id == user_id,
            ),
        )
        baseline = await self._session.scalar(
            select(BaselinePreference).where(
                BaselinePreference.user_id == user_id,
            ),
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

    async def upsert_athlete_profile(
        self,
        *,
        user_id: uuid.UUID,
        age: int,
        height_cm: float | None,
        weight_kg: float | None,
        primary_sport: PrimarySport,
    ) -> AthleteProfile:
        await self._require_user(user_id)
        profile = await self.get_athlete_profile(user_id=user_id)
        if profile is None:
            profile = AthleteProfile(user_id=user_id)
            self._session.add(profile)
        profile.age = age
        profile.height_cm = height_cm
        profile.weight_kg = weight_kg
        profile.primary_sport = primary_sport
        await self._session.flush()
        return profile

    async def upsert_training_goal(
        self,
        *,
        user_id: uuid.UUID,
        goal_type: GoalType,
        event_name: str | None,
        event_date: date | None,
        goal_priority: GoalPriority,
    ) -> TrainingGoal:
        await self._require_user(user_id)
        goal = await self.get_training_goal(user_id=user_id)
        if goal is None:
            goal = TrainingGoal(user_id=user_id)
            self._session.add(goal)
        goal.goal_type = goal_type
        goal.event_name = event_name
        goal.event_date = event_date
        goal.goal_priority = goal_priority
        await self._session.flush()
        return goal

    async def replace_availability_rules(
        self,
        *,
        user_id: uuid.UUID,
        rules: Sequence[AvailabilityRuleInput],
    ) -> tuple[AvailabilityRule, ...]:
        await self._require_user(user_id)
        await self._session.execute(
            delete(AvailabilityRule).where(
                AvailabilityRule.user_id == user_id,
            ),
        )
        records = tuple(
            AvailabilityRule(
                user_id=user_id,
                day_of_week=rule.day_of_week,
                available_minutes=rule.available_minutes,
                is_variable=rule.is_variable,
            )
            for rule in rules
        )
        self._session.add_all(records)
        await self._session.flush()
        return records

    async def replace_equipment_access(
        self,
        *,
        user_id: uuid.UUID,
        equipment: Sequence[EquipmentAccessInput],
    ) -> tuple[EquipmentAccess, ...]:
        await self._require_user(user_id)
        await self._session.execute(
            delete(EquipmentAccess).where(
                EquipmentAccess.user_id == user_id,
            ),
        )
        records = tuple(
            EquipmentAccess(
                user_id=user_id,
                equipment_type=item.equipment_type,
                access_type=item.access_type,
                access_days=(
                    [day.value for day in item.access_days]
                    if item.access_days is not None
                    else None
                ),
                notes=item.notes,
            )
            for item in equipment
        )
        self._session.add_all(records)
        await self._session.flush()
        return records

    async def replace_health_constraints(
        self,
        *,
        user_id: uuid.UUID,
        constraints: Sequence[HealthConstraintInput],
    ) -> tuple[HealthConstraint, ...]:
        await self._require_user(user_id)
        await self._session.execute(
            delete(HealthConstraint).where(
                HealthConstraint.user_id == user_id,
            ),
        )
        records = tuple(
            HealthConstraint(
                user_id=user_id,
                body_area=item.body_area,
                constraint_type=item.constraint_type,
                normalized_description=item.normalized_description,
                is_current=item.constraint_type
                in (HealthConstraintType.CURRENT, HealthConstraintType.BOTH),
                is_historical=item.constraint_type
                in (
                    HealthConstraintType.HISTORICAL,
                    HealthConstraintType.BOTH,
                ),
            )
            for item in constraints
        )
        self._session.add_all(records)
        await self._session.flush()
        return records

    async def upsert_coach_preference(
        self,
        *,
        user_id: uuid.UUID,
        tone: CoachTone,
        detail_level: DetailLevel,
    ) -> CoachPreference:
        await self._require_user(user_id)
        preference = await self._session.scalar(
            select(CoachPreference).where(
                CoachPreference.user_id == user_id,
            ),
        )
        if preference is None:
            preference = CoachPreference(user_id=user_id)
            self._session.add(preference)
        preference.tone = tone
        preference.detail_level = detail_level
        await self._session.flush()
        return preference

    async def upsert_baseline_preference(
        self,
        *,
        user_id: uuid.UUID,
        selected_source: BaselineSource,
        status: BaselinePreferenceStatus,
    ) -> BaselinePreference:
        await self._require_user(user_id)
        preference = await self._session.scalar(
            select(BaselinePreference).where(
                BaselinePreference.user_id == user_id,
            ),
        )
        if preference is None:
            preference = BaselinePreference(user_id=user_id)
            self._session.add(preference)
        preference.selected_source = selected_source
        preference.status = status
        await self._session.flush()
        return preference

    async def finalize_profile(
        self,
        *,
        user_id: uuid.UUID,
        age: int,
        height_cm: float | None,
        weight_kg: float | None,
        primary_sport: PrimarySport,
        goal_type: GoalType,
        event_name: str | None,
        event_date: date | None,
        goal_priority: GoalPriority,
        availability: Sequence[AvailabilityRuleInput],
        equipment: Sequence[EquipmentAccessInput],
        constraints: Sequence[HealthConstraintInput],
        coach_tone: CoachTone,
        detail_level: DetailLevel,
        baseline_source: BaselineSource,
        baseline_status: BaselinePreferenceStatus,
    ) -> ProfileBundle:
        """Idempotently replace normalized profile records without committing."""

        await self.upsert_athlete_profile(
            user_id=user_id,
            age=age,
            height_cm=height_cm,
            weight_kg=weight_kg,
            primary_sport=primary_sport,
        )
        await self.upsert_training_goal(
            user_id=user_id,
            goal_type=goal_type,
            event_name=event_name,
            event_date=event_date,
            goal_priority=goal_priority,
        )
        await self.replace_availability_rules(
            user_id=user_id,
            rules=availability,
        )
        await self.replace_equipment_access(
            user_id=user_id,
            equipment=equipment,
        )
        await self.replace_health_constraints(
            user_id=user_id,
            constraints=constraints,
        )
        await self.upsert_coach_preference(
            user_id=user_id,
            tone=coach_tone,
            detail_level=detail_level,
        )
        await self.upsert_baseline_preference(
            user_id=user_id,
            selected_source=baseline_source,
            status=baseline_status,
        )
        return await self.get_bundle(user_id=user_id)

    async def _require_user(self, user_id: uuid.UUID) -> None:
        exists = await self._session.scalar(
            select(User.id).where(User.id == user_id),
        )
        if exists is None:
            raise OwnedRecordNotFoundError("user not found")
