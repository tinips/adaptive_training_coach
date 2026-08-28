"""Application service for first immutable, goal-scoped fitness baselines."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.base import utc_now
from app.db.models import AthleteBaselineAssessment, Workout
from app.domain.enums import (
    Discipline,
    FitnessBaselineSource,
    GoalContextRole,
    GoalTemplateKind,
)
from app.repositories.fitness import FitnessRepository
from app.repositories.profiles import ProfileRepository
from app.repositories.training_catalog import TrainingCatalogRepository
from app.schemas.fitness import (
    BaselineCalculation,
    FitnessWorkoutEvidence,
    HeartRateEvidence,
)
from app.services.fitness.calculator import calculate_baseline_window

_EVIDENCE_ATTRIBUTES = (
    "analysis_started_at",
    "analysis_ended_at",
    "calculated_at",
    "session_count",
    "active_day_count",
    "total_duration_seconds",
    "known_distance_meters",
    "distance_session_count",
    "longest_duration_seconds",
    "longest_distance_meters",
    "total_calories_kcal",
    "reliable_hr_sample_count",
    "reliable_average_hr_bpm",
    "reliable_max_hr_bpm",
    "confidence",
    "discipline_metrics_jsonb",
    "evidence_summary_jsonb",
    "quality_flags_jsonb",
    "source_workout_through_at",
    "input_updated_through_at",
    "input_digest",
    "calculation_version",
)


class BaselineAssessmentService:
    """Create the first evidence-backed baseline for each active goal discipline.

    The caller owns the transaction. Locking the athlete before looking up the
    goal and baseline serializes imports and makes the first baseline immutable
    even when several import requests arrive concurrently.
    """

    def __init__(self, *, settings: Settings) -> None:
        self._settings = settings

    async def create_missing_baselines_for_goal_disciplines_in_session(
        self,
        session: AsyncSession,
        *,
        athlete_id: uuid.UUID,
        calculated_at: datetime | None = None,
    ) -> tuple[Discipline, ...]:
        """Create one immutable baseline per eligible goal discipline.

        Baselines deliberately use the latest owned workout in each discipline
        as their window endpoint. This preserves imported historical evidence
        even when the import does not contain activity from the last 14 days.
        Existing baseline rows are never updated.
        """

        now = _as_utc(calculated_at or utc_now())
        profiles = ProfileRepository(session)
        await profiles.lock_owner(user_id=athlete_id)
        # Imported workout/detail/HR rows can be pending when this service runs
        # in the same transaction, so materialize them before querying.
        await session.flush()

        disciplines = await self._goal_disciplines(
            profiles=profiles,
            catalog=TrainingCatalogRepository(session),
            athlete_id=athlete_id,
        )
        return await self.create_missing_baselines_for_disciplines_in_session(
            session,
            athlete_id=athlete_id,
            disciplines=disciplines,
            calculated_at=now,
            owner_locked=True,
        )

    async def create_missing_baselines_for_disciplines_in_session(
        self,
        session: AsyncSession,
        *,
        athlete_id: uuid.UUID,
        disciplines: tuple[Discipline, ...],
        calculated_at: datetime | None = None,
        owner_locked: bool = False,
        window_started_at: datetime | None = None,
        window_ended_at: datetime | None = None,
    ) -> tuple[Discipline, ...]:
        """Create baselines for an explicit, already-resolved discipline scope.

        The planner passes the window it just evaluated, so the frozen baseline
        reflects the evidence that authorised it rather than a narrower slice.
        Callers that omit the window keep the latest-workout anchoring, which
        preserves imported historical evidence for the file-import path.
        """

        now = _as_utc(calculated_at or utc_now())
        if not disciplines:
            return ()
        profiles = ProfileRepository(session)
        if not owner_locked:
            await profiles.lock_owner(user_id=athlete_id)
        await session.flush()
        if not disciplines:
            return ()

        repository = FitnessRepository(session)
        created: list[Discipline] = []
        for discipline in disciplines:
            if (
                await repository.baseline_for_discipline(
                    athlete_id=athlete_id,
                    discipline=discipline,
                )
                is not None
            ):
                continue

            if window_started_at is not None and window_ended_at is not None:
                discipline_started_at = _as_utc(window_started_at)
                discipline_ended_at = _as_utc(window_ended_at)
            else:
                latest_started_at = await repository.latest_workout_started_at(
                    athlete_id=athlete_id,
                    discipline=discipline,
                )
                if latest_started_at is None:
                    continue
                discipline_ended_at = _as_utc(latest_started_at)
                discipline_started_at = discipline_ended_at - timedelta(
                    days=self._settings.fitness_window_days
                )
            workouts = await repository.workouts_for_window(
                athlete_id=athlete_id,
                disciplines=(discipline,),
                started_at=discipline_started_at,
                ended_at=discipline_ended_at,
            )
            calculation = calculate_baseline_window(
                discipline=discipline,
                workouts=tuple(
                    _fitness_evidence_for_workout(item) for item in workouts
                ),
                window_started_at=discipline_started_at,
                window_ended_at=discipline_ended_at,
                calculated_at=now,
            )
            if calculation is None:
                continue

            session.add(
                _new_baseline(
                    athlete_id=athlete_id,
                    calculation=calculation,
                    created_at=now,
                )
            )
            created.append(discipline)

        await session.flush()
        return tuple(created)

    async def _goal_disciplines(
        self,
        *,
        profiles: ProfileRepository,
        catalog: TrainingCatalogRepository,
        athlete_id: uuid.UUID,
    ) -> tuple[Discipline, ...]:
        """Resolve the primary TARGET and supporting SUPPORTING contexts only."""

        goal = await profiles.get_training_goal(user_id=athlete_id)
        if goal is None:
            return ()

        expected_role_by_goal_id: dict[uuid.UUID, GoalContextRole] = {}
        if goal.goal_template_id is not None:
            primary = await catalog.active_goal_by_id(
                goal_template_id=goal.goal_template_id
            )
            if primary is not None and primary.kind is GoalTemplateKind.PRIMARY:
                expected_role_by_goal_id[primary.id] = GoalContextRole.TARGET
        if goal.supporting_goal_template_id is not None:
            supporting = await catalog.active_goal_by_id(
                goal_template_id=goal.supporting_goal_template_id
            )
            if (
                supporting is not None
                and supporting.kind is GoalTemplateKind.SUPPORTING
            ):
                expected_role_by_goal_id[supporting.id] = GoalContextRole.SUPPORTING
        if not expected_role_by_goal_id:
            return ()

        rows = await catalog.contexts_for_goals(
            goal_template_ids=expected_role_by_goal_id.keys()
        )
        return tuple(
            sorted(
                {
                    context.discipline
                    for relation, context in rows
                    if expected_role_by_goal_id.get(relation.goal_template_id)
                    is relation.role
                },
                key=lambda discipline: discipline.value,
            )
        )


def _fitness_evidence_for_workout(workout: Workout) -> FitnessWorkoutEvidence:
    """Map ORM workout/detail rows to only the facts used by the calculator."""

    detail = _detail_for_workout(workout)
    distance_meters = getattr(detail, "distance_meters", None)
    moving_duration_seconds = getattr(detail, "moving_duration_seconds", None)
    calories_kcal = getattr(detail, "calories_kcal", None)
    elevation_gain_meters = getattr(detail, "elevation_gain_meters", None)
    average_cadence = _average_cadence(workout)
    subtype = _subtype(workout)
    swimming_environment = getattr(detail, "swimming_environment", None)
    structured_exercise_count = (
        len(workout.strength_details.exercises_jsonb)
        if workout.strength_details is not None
        else None
    )
    coarse_summary_hr = any(
        getattr(detail, attribute, None) is not None
        for attribute in ("average_heart_rate", "max_heart_rate")
    )
    return FitnessWorkoutEvidence(
        workout_id=workout.id,
        discipline=workout.discipline,
        source=workout.source,
        started_at=workout.started_at,
        duration_seconds=workout.duration_seconds,
        fitness_input_updated_at=workout.fitness_input_updated_at,
        distance_meters=distance_meters,
        moving_duration_seconds=moving_duration_seconds,
        calories_kcal=calories_kcal,
        subtype=subtype,
        swimming_environment=swimming_environment,
        elevation_gain_meters=elevation_gain_meters,
        average_cadence=average_cadence,
        structured_exercise_count=structured_exercise_count,
        heart_rate_observations=tuple(
            HeartRateEvidence(
                started_at=observation.started_at,
                ended_at=observation.ended_at,
                beats_per_minute=observation.beats_per_minute,
                temporal_quality=observation.temporal_quality,
            )
            for observation in workout.heart_rate_observations
        ),
        coarse_heart_rate_present=coarse_summary_hr,
    )


def _detail_for_workout(workout: Workout) -> object | None:
    if workout.discipline is Discipline.RUNNING:
        return workout.running_details
    if workout.discipline is Discipline.CYCLING:
        return workout.cycling_details
    if workout.discipline is Discipline.HIKING:
        return workout.hiking_details
    if workout.discipline is Discipline.SWIMMING:
        return workout.swimming_details
    if workout.discipline is Discipline.STRENGTH:
        return workout.strength_details
    return workout.other_details


def _subtype(workout: Workout) -> str | None:
    detail = _detail_for_workout(workout)
    if workout.discipline is Discipline.RUNNING:
        value = getattr(detail, "running_type", None)
    elif workout.discipline is Discipline.CYCLING:
        value = getattr(detail, "cycling_type", None)
    elif workout.discipline is Discipline.HIKING:
        value = getattr(detail, "hiking_type", None)
    elif workout.discipline is Discipline.STRENGTH:
        value = getattr(detail, "strength_type", None)
    else:
        return None
    return value.value if value is not None else None


def _average_cadence(workout: Workout) -> float | None:
    if workout.discipline is Discipline.RUNNING and workout.running_details is not None:
        return workout.running_details.average_cadence_spm
    if workout.discipline is Discipline.CYCLING and workout.cycling_details is not None:
        return workout.cycling_details.average_cadence_rpm
    return None


def _evidence_values(calculation: BaselineCalculation) -> dict[str, object]:
    return {
        attribute: getattr(calculation, attribute) for attribute in _EVIDENCE_ATTRIBUTES
    }


def _new_baseline(
    *,
    athlete_id: uuid.UUID,
    calculation: BaselineCalculation,
    created_at: datetime,
) -> AthleteBaselineAssessment:
    return AthleteBaselineAssessment(
        athlete_id=athlete_id,
        discipline=calculation.discipline,
        source=FitnessBaselineSource.IMPORTED_WORKOUT_WINDOW,
        created_at=created_at,
        **_evidence_values(calculation),
    )


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
