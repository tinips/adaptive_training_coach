"""Ownership-scoped reads for existing normalized profiles."""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import AvailabilityRule
from app.domain.enums import (
    BaselinePreferenceStatus,
    BaselineSource,
    DayOfWeek,
    UserStatus,
)
from app.repositories.onboarding import OnboardingRepository
from app.repositories.profiles import ProfileBundle, ProfileRepository
from app.schemas.profile import (
    PersistedEquipmentAccessData,
    PersistedHealthConstraintData,
    PersistedMandatoryProfileData,
    PersistedProfileData,
)


class IncompleteProfileError(ValueError):
    """Raised when historical normalized profile data is incomplete."""

    code = "incomplete_profile"


class BaselineSelectionUnavailableError(ValueError):
    """Raised when a stale menu action cannot change the baseline source."""

    code = "baseline_selection_unavailable"


class ProfileService:
    """Read existing profiles without materializing removed onboarding answers."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._session_factory = session_factory

    async def get(
        self,
        *,
        user_id: uuid.UUID,
    ) -> PersistedProfileData | PersistedMandatoryProfileData | None:
        """Read a normalized profile only through its owning user."""

        async with self._session_factory() as session:
            bundle = await ProfileRepository(session).get_bundle(user_id=user_id)
            if bundle.athlete_profile is None:
                return None
            athlete = bundle.athlete_profile
            if (
                athlete.birth_year is not None
                and athlete.gender is not None
                and athlete.weight_kg is not None
                and athlete.height_cm is not None
            ):
                return PersistedMandatoryProfileData(
                    birth_year=athlete.birth_year,
                    gender=athlete.gender,
                    weight_kg=athlete.weight_kg,
                    height_cm=athlete.height_cm,
                )
            onboarding = await OnboardingRepository(session).get_for_user(
                user_id=user_id,
            )
            return self._serialize_bundle(
                bundle,
                onboarding_answers=(
                    onboarding.answers if onboarding is not None else None
                ),
            )

    async def select_pending_baseline_source(
        self,
        *,
        user_id: uuid.UUID,
        source: BaselineSource,
    ) -> PersistedProfileData | PersistedMandatoryProfileData:
        """Retain baseline selection for users with an existing complete profile."""

        if source not in {BaselineSource.MANUAL, BaselineSource.CALIBRATION}:
            raise ValueError("Only pending manual or calibration sources are valid.")
        async with self._session_factory.begin() as session:
            repository = ProfileRepository(session)
            user = await repository.lock_owner(user_id=user_id)
            profile = await repository.get_athlete_profile(user_id=user_id)
            if profile is None:
                raise IncompleteProfileError
            if user.status not in {
                UserStatus.ONBOARDING_COMPLETED,
                UserStatus.PROFILE_COMPLETED,
                UserStatus.BASELINE_PENDING,
                UserStatus.BASELINE_FAILED,
            }:
                raise BaselineSelectionUnavailableError
            await repository.upsert_baseline_preference(
                user_id=user_id,
                selected_source=source,
                status=BaselinePreferenceStatus.NOT_IMPLEMENTED,
            )
            user.status = UserStatus.BASELINE_PENDING
            await session.flush()
            bundle = await repository.get_bundle(user_id=user_id)
            athlete = bundle.athlete_profile
            if (
                athlete is not None
                and athlete.birth_year is not None
                and athlete.gender is not None
                and athlete.weight_kg is not None
                and athlete.height_cm is not None
            ):
                return PersistedMandatoryProfileData(
                    birth_year=athlete.birth_year,
                    gender=athlete.gender,
                    weight_kg=athlete.weight_kg,
                    height_cm=athlete.height_cm,
                )
            onboarding = await OnboardingRepository(session).get_for_user(
                user_id=user_id,
            )
            return self._serialize_bundle(
                bundle,
                onboarding_answers=(
                    onboarding.answers if onboarding is not None else None
                ),
            )

    @classmethod
    def _serialize_bundle(
        cls,
        bundle: ProfileBundle,
        *,
        onboarding_answers: Mapping[str, object] | None = None,
    ) -> PersistedProfileData:
        profile = bundle.athlete_profile
        goal = bundle.training_goal
        coach = bundle.coach_preference
        baseline = bundle.baseline_preference
        if profile is None or goal is None or coach is None or baseline is None:
            raise IncompleteProfileError

        descriptions = [
            (
                constraint.normalized_description
                or (
                    constraint.body_area.value
                    if constraint.body_area is not None
                    else "OTHER"
                )
            )
            for constraint in bundle.health_constraints
        ]
        day_order = {day: index for index, day in enumerate(DayOfWeek)}
        availability_rules = sorted(
            bundle.availability_rules,
            key=lambda rule: day_order[rule.day_of_week],
        )
        equipment_access = [
            PersistedEquipmentAccessData(
                equipment_type=item.equipment_type,
                access_type=item.access_type,
                access_days=[DayOfWeek(str(day)) for day in (item.access_days or [])],
                notes=item.notes,
            )
            for item in bundle.equipment_access
        ]
        health_details = [
            PersistedHealthConstraintData(
                body_area=constraint.body_area,
                constraint_type=constraint.constraint_type,
                description=constraint.normalized_description,
            )
            for constraint in bundle.health_constraints
        ]
        return PersistedProfileData(
            primary_sport=profile.primary_sport,
            main_goal=goal.main_goal,
            event_date=goal.event_date,
            target_outcome=goal.target_outcome,
            secondary_priority=goal.secondary_priority,
            age=profile.age,
            height_cm=profile.height_cm,
            weight_kg=profile.weight_kg,
            training_days=[rule.day_of_week for rule in availability_rules],
            weekday_duration=cls._persisted_duration(
                availability_rules,
                onboarding_answers=onboarding_answers,
                weekend=False,
            ),
            weekend_duration=cls._persisted_duration(
                availability_rules,
                onboarding_answers=onboarding_answers,
                weekend=True,
            ),
            equipment=[item.equipment_type for item in equipment_access],
            equipment_access=equipment_access,
            health_constraints=descriptions,
            health_constraint_details=health_details,
            coach_tone=coach.tone,
            detail_level=coach.detail_level,
            baseline_source=baseline.selected_source,
        )

    @staticmethod
    def _persisted_duration(
        availability_rules: Sequence[AvailabilityRule],
        *,
        onboarding_answers: Mapping[str, object] | None,
        weekend: bool,
    ) -> int | str | None:
        key = "weekend_duration" if weekend else "weekday_duration"
        allowed_strings = (
            {"OVER_180", "VARIABLE"} if weekend else {"OVER_90", "VARIABLE"}
        )
        if onboarding_answers is not None:
            source_value = onboarding_answers.get(key)
            if isinstance(source_value, int) and not isinstance(source_value, bool):
                return source_value
            if isinstance(source_value, str) and source_value in allowed_strings:
                return source_value

        weekend_days = {DayOfWeek.SATURDAY, DayOfWeek.SUNDAY}
        matching_rules = [
            rule
            for rule in availability_rules
            if (rule.day_of_week in weekend_days) is weekend
        ]
        if not matching_rules:
            return None
        rule = matching_rules[0]
        if not rule.is_variable:
            return rule.available_minutes
        threshold = 180 if weekend else 90
        over_value = "OVER_180" if weekend else "OVER_90"
        return over_value if rule.available_minutes == threshold else "VARIABLE"
