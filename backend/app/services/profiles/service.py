"""Atomic profile finalization and ownership-scoped profile reads."""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import (
    AvailabilityRule,
    BodyArea,
    EquipmentAccessType,
    EquipmentType,
)
from app.domain.enums import (
    BaselinePreferenceStatus,
    BaselineSource,
    DayOfWeek,
    OnboardingStatus,
    OnboardingStep,
    UserStatus,
)
from app.repositories.baselines import BaselineRepository
from app.repositories.onboarding import OnboardingRepository
from app.repositories.profiles import (
    AvailabilityRuleInput,
    EquipmentAccessInput,
    HealthConstraintInput,
    ProfileBundle,
    ProfileRepository,
)
from app.repositories.users import UserRepository
from app.schemas.profile import (
    AccessSelection,
    FinalOnboardingAnswers,
    PersistedEquipmentAccessData,
    PersistedHealthConstraintData,
    PersistedProfileData,
)


class IncompleteProfileError(ValueError):
    """Raised before writes when confirmed staging is incomplete."""

    code = "incomplete_profile"


class BaselineSelectionUnavailableError(ValueError):
    """Raised when a stale menu action cannot change the baseline source."""

    code = "baseline_selection_unavailable"


class ProfileService:
    """Materialize and query profiles while preserving transaction boundaries."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._session_factory = session_factory

    async def finalize(self, *, user_id: uuid.UUID) -> PersistedProfileData:
        """Atomically and idempotently materialize confirmed onboarding answers."""

        async with self._session_factory.begin() as session:
            onboarding_repository = OnboardingRepository(session)
            onboarding = await onboarding_repository.lock_for_user(
                user_id=user_id,
            )
            profile_repository = ProfileRepository(session)
            if onboarding.status is OnboardingStatus.COMPLETED:
                bundle = await profile_repository.get_bundle(user_id=user_id)
                return self._serialize_bundle(
                    bundle,
                    onboarding_answers=onboarding.answers,
                )
            if (
                onboarding.current_step is not OnboardingStep.SUMMARY
                or onboarding.pending_free_text_step is not None
                or onboarding.pending_parsed_value is not None
            ):
                raise IncompleteProfileError

            try:
                answers = FinalOnboardingAnswers.model_validate(onboarding.answers)
            except ValidationError as exc:
                raise IncompleteProfileError from exc
            if (
                answers.baseline_source is BaselineSource.APPLE_HEALTH_EXPORT
                and await BaselineRepository(session).get_latest(user_id=user_id)
                is None
            ):
                raise IncompleteProfileError

            bundle = await profile_repository.finalize_profile(
                user_id=user_id,
                age=answers.age,
                height_cm=answers.height,
                weight_kg=answers.weight,
                primary_sport=answers.primary_sport,
                goal_type=answers.goal_type,
                event_name=answers.event_name,
                event_date=answers.event_date,
                goal_priority=answers.goal_priority,
                availability=self._availability(answers),
                equipment=self._equipment(answers),
                constraints=self._constraints(answers),
                coach_tone=answers.coach_tone,
                detail_level=answers.coach_detail,
                baseline_source=answers.baseline_source,
                baseline_status=self._baseline_status(answers.baseline_source),
            )
            await onboarding_repository.complete(user_id=user_id)
            await UserRepository(session).update_status(
                user_id=user_id,
                status=self._user_status(answers.baseline_source),
            )
            return self._serialize_bundle(
                bundle,
                onboarding_answers=onboarding.answers,
            )

    async def get(self, *, user_id: uuid.UUID) -> PersistedProfileData | None:
        """Read a normalized profile only through its owning user."""

        async with self._session_factory() as session:
            bundle = await ProfileRepository(session).get_bundle(user_id=user_id)
            if bundle.athlete_profile is None:
                return None
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
    ) -> PersistedProfileData:
        """Persist a post-profile manual/calibration choice without inventing data."""

        if source not in {BaselineSource.MANUAL, BaselineSource.CALIBRATION}:
            raise ValueError("Only pending manual or calibration sources are valid.")
        async with self._session_factory.begin() as session:
            repository = ProfileRepository(session)
            user = await repository.lock_owner(user_id=user_id)
            profile = await repository.get_athlete_profile(user_id=user_id)
            if profile is None:
                raise IncompleteProfileError
            if user.status not in {
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
    def _availability(
        cls,
        answers: FinalOnboardingAnswers,
    ) -> tuple[AvailabilityRuleInput, ...]:
        rules: list[AvailabilityRuleInput] = []
        for day in answers.training_days:
            duration = (
                answers.weekend_duration
                if day in {DayOfWeek.SATURDAY, DayOfWeek.SUNDAY}
                else answers.weekday_duration
            )
            rules.append(
                AvailabilityRuleInput(
                    day_of_week=day,
                    available_minutes=(
                        duration
                        if isinstance(duration, int)
                        else cls._over_duration_threshold(duration)
                    ),
                    is_variable=not isinstance(duration, int),
                )
            )
        return tuple(rules)

    @classmethod
    def _equipment(
        cls,
        answers: FinalOnboardingAnswers,
    ) -> tuple[EquipmentAccessInput, ...]:
        records: list[EquipmentAccessInput] = []
        bike_types = {
            EquipmentType.ROAD_BIKE,
            EquipmentType.MOUNTAIN_BIKE,
            EquipmentType.INDOOR_BIKE_TRAINER,
        }
        equipment_types = list(dict.fromkeys(answers.equipment))
        if (
            answers.pool_access is not None
            and EquipmentType.SWIMMING_POOL not in equipment_types
        ):
            equipment_types.append(EquipmentType.SWIMMING_POOL)
        if answers.bike_access is not None and not any(
            equipment_type in bike_types for equipment_type in equipment_types
        ):
            equipment_types.append(EquipmentType.ROAD_BIKE)

        for equipment_type in equipment_types:
            selection: AccessSelection | None = None
            if equipment_type is EquipmentType.SWIMMING_POOL:
                selection = answers.pool_access
            elif equipment_type in bike_types:
                selection = answers.bike_access
            records.append(
                cls._equipment_input(
                    equipment_type=equipment_type,
                    selection=selection,
                    notes=(
                        answers.equipment_other_description
                        if equipment_type is EquipmentType.OTHER
                        else None
                    ),
                )
            )
        return tuple(records)

    @staticmethod
    def _equipment_input(
        *,
        equipment_type: EquipmentType,
        selection: AccessSelection | None,
        notes: str | None = None,
    ) -> EquipmentAccessInput:
        if selection is None:
            return EquipmentAccessInput(
                equipment_type=equipment_type,
                notes=notes,
            )
        return EquipmentAccessInput(
            equipment_type=equipment_type,
            access_type=selection.type,
            access_days=(
                tuple(selection.days)
                if selection.type is EquipmentAccessType.REGULAR
                else None
            ),
            notes=notes,
        )

    @staticmethod
    def _constraints(
        answers: FinalOnboardingAnswers,
    ) -> tuple[HealthConstraintInput, ...]:
        if answers.health_areas == ["NONE"]:
            return ()
        timing = answers.health_timing
        if timing is None:
            raise IncompleteProfileError
        return tuple(
            HealthConstraintInput(
                body_area=BodyArea(area),
                constraint_type=timing,
                normalized_description=ProfileService._constraint_description(
                    area=BodyArea(area),
                    answers=answers,
                ),
            )
            for area in answers.health_areas
        )

    @staticmethod
    def _constraint_description(
        *,
        area: BodyArea,
        answers: FinalOnboardingAnswers,
    ) -> str | None:
        descriptions: list[str] = []
        if (
            area is BodyArea.OTHER
            and answers.health_areas_other_description is not None
        ):
            descriptions.append(answers.health_areas_other_description)
        if answers.health_description is not None:
            descriptions.append(answers.health_description)
        return "; ".join(dict.fromkeys(descriptions)) or None

    @staticmethod
    def _over_duration_threshold(duration: str) -> int | None:
        thresholds = {
            "OVER_90": 90,
            "OVER_180": 180,
        }
        return thresholds.get(duration)

    @staticmethod
    def _baseline_status(
        source: BaselineSource,
    ) -> BaselinePreferenceStatus:
        if source is BaselineSource.STRAVA:
            return BaselinePreferenceStatus.PENDING
        if source is BaselineSource.APPLE_HEALTH_EXPORT:
            return BaselinePreferenceStatus.READY
        if source in {BaselineSource.MANUAL, BaselineSource.CALIBRATION}:
            return BaselinePreferenceStatus.NOT_IMPLEMENTED
        return BaselinePreferenceStatus.SELECTED

    @staticmethod
    def _user_status(source: BaselineSource) -> UserStatus:
        if source is BaselineSource.STRAVA:
            return UserStatus.BASELINE_PENDING
        if source is BaselineSource.APPLE_HEALTH_EXPORT:
            return UserStatus.BASELINE_READY
        return UserStatus.PROFILE_COMPLETED

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
            goal_type=goal.goal_type,
            event_name=goal.event_name,
            event_date=goal.event_date,
            goal_priority=goal.goal_priority,
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
