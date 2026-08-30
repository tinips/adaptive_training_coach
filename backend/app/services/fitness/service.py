"""Workout evidence mapping used by recent-workout planning."""

from __future__ import annotations

from app.db.models import Workout
from app.domain.enums import Discipline
from app.schemas.fitness import FitnessWorkoutEvidence, HeartRateEvidence


def _fitness_evidence_for_workout(workout: Workout) -> FitnessWorkoutEvidence:
    detail = _detail_for_workout(workout)
    return FitnessWorkoutEvidence(
        workout_id=workout.id,
        discipline=workout.discipline,
        source=workout.source,
        started_at=workout.started_at,
        duration_seconds=workout.duration_seconds,
        fitness_input_updated_at=workout.fitness_input_updated_at,
        distance_meters=getattr(detail, "distance_meters", None),
        moving_duration_seconds=getattr(detail, "moving_duration_seconds", None),
        calories_kcal=getattr(detail, "calories_kcal", None),
        subtype=_subtype(workout),
        swimming_environment=getattr(detail, "swimming_environment", None),
        elevation_gain_meters=getattr(detail, "elevation_gain_meters", None),
        average_cadence=_average_cadence(workout),
        structured_exercise_count=(
            len(workout.strength_details.exercises_jsonb)
            if workout.strength_details is not None
            else None
        ),
        heart_rate_observations=tuple(
            HeartRateEvidence(
                started_at=item.started_at,
                ended_at=item.ended_at,
                beats_per_minute=item.beats_per_minute,
                temporal_quality=item.temporal_quality,
            )
            for item in workout.heart_rate_observations
        ),
        coarse_heart_rate_present=any(
            getattr(detail, item, None) is not None
            for item in ("average_heart_rate", "max_heart_rate")
        ),
    )


def _detail_for_workout(workout: Workout) -> object | None:
    return {
        Discipline.RUNNING: workout.running_details,
        Discipline.CYCLING: workout.cycling_details,
        Discipline.HIKING: workout.hiking_details,
        Discipline.SWIMMING: workout.swimming_details,
        Discipline.STRENGTH: workout.strength_details,
    }.get(workout.discipline, workout.other_details)


def _subtype(workout: Workout) -> str | None:
    detail = _detail_for_workout(workout)
    attribute = {
        Discipline.RUNNING: "running_type",
        Discipline.CYCLING: "cycling_type",
        Discipline.HIKING: "hiking_type",
        Discipline.STRENGTH: "strength_type",
    }.get(workout.discipline)
    value = getattr(detail, attribute, None) if attribute else None
    return value.value if value is not None else None


def _average_cadence(workout: Workout) -> float | None:
    if workout.discipline is Discipline.RUNNING and workout.running_details is not None:
        return workout.running_details.average_cadence_spm
    if workout.discipline is Discipline.CYCLING and workout.cycling_details is not None:
        return workout.cycling_details.average_cadence_rpm
    return None
