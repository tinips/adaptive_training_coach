"""Conversion between canonical workout detail schemas and ORM models."""

from __future__ import annotations

from app.db.models import (
    CyclingWorkoutDetails,
    HikingWorkoutDetails,
    OtherWorkoutDetails,
    PoolSwimmingDetails,
    RunningWorkoutDetails,
    StrengthWorkoutDetails,
    SwimmingWorkoutDetails,
    Workout,
)
from app.domain.enums import (
    CyclingType,
    Discipline,
    HikingType,
    RunningType,
    StrengthType,
)
from app.schemas.workouts import (
    CyclingWorkoutDetailsData,
    HikingWorkoutDetailsData,
    OtherWorkoutDetailsData,
    RunningWorkoutDetailsData,
    StrengthExercise,
    StrengthWorkoutDetailsData,
    SwimmingWorkoutDetailsData,
    WorkoutDetailsData,
    main_detail,
)
from app.services.activities.contracts import (
    ActivityImportData,
    ActivityImportValidationError,
)
from app.services.activities.normalization import human_activity_name, json_safe


def details_for_import(incoming: ActivityImportData) -> WorkoutDetailsData:
    """Build the one canonical discipline detail for an import."""

    common = {
        "distance_meters": incoming.distance_meters,
        "moving_duration_seconds": incoming.moving_duration_seconds,
        "average_heart_rate": incoming.average_heart_rate,
        "max_heart_rate": incoming.max_heart_rate,
    }
    if incoming.discipline is Discipline.RUNNING:
        return RunningWorkoutDetailsData(
            running_type=incoming.running_type or RunningType.OUTDOOR,
            average_pace_seconds_per_km=incoming.average_pace_seconds_per_km,
            elevation_gain_meters=incoming.elevation_gain_meters,
            elevation_loss_meters=incoming.elevation_loss_meters,
            average_cadence_spm=incoming.average_cadence,
            max_cadence_spm=incoming.max_cadence,
            **common,
        )
    if incoming.discipline is Discipline.CYCLING:
        return CyclingWorkoutDetailsData(
            cycling_type=incoming.cycling_type or CyclingType.OTHER,
            average_speed_kph=incoming.average_speed_kph,
            max_speed_kph=incoming.max_speed_kph,
            elevation_gain_meters=incoming.elevation_gain_meters,
            elevation_loss_meters=incoming.elevation_loss_meters,
            average_cadence_rpm=incoming.average_cadence,
            max_cadence_rpm=incoming.max_cadence,
            **common,
        )
    if incoming.discipline is Discipline.HIKING:
        return HikingWorkoutDetailsData(
            hiking_type=incoming.hiking_type or HikingType.OTHER,
            average_pace_seconds_per_km=incoming.average_pace_seconds_per_km,
            elevation_gain_meters=incoming.elevation_gain_meters,
            elevation_loss_meters=incoming.elevation_loss_meters,
            **common,
        )
    if incoming.discipline is Discipline.SWIMMING:
        if incoming.swimming_environment is None:
            raise ActivityImportValidationError("Swimming environment is required")
        return SwimmingWorkoutDetailsData(
            swimming_environment=incoming.swimming_environment,
            average_pace_seconds_per_100m=(incoming.average_pace_seconds_per_100m),
            pool_details=incoming.pool_details,
            **common,
        )
    if incoming.discipline is Discipline.STRENGTH:
        return StrengthWorkoutDetailsData(
            strength_type=incoming.strength_type or StrengthType.OTHER,
            session_focus=incoming.session_focus,
            exercises_jsonb=list(incoming.exercises),
        )
    return OtherWorkoutDetailsData(
        activity_name=incoming.activity_name or human_activity_name(incoming.raw_sport),
        activity_description=incoming.activity_description,
        raw_sport=incoming.raw_sport,
        raw_sub_sport=incoming.raw_sub_sport,
        distance_meters=incoming.distance_meters,
        average_heart_rate=incoming.average_heart_rate,
        max_heart_rate=incoming.max_heart_rate,
        metrics_jsonb=(
            json_safe(incoming.unsupported_metrics)
            if incoming.unsupported_metrics
            else None
        ),
    )


def replace_detail(workout: Workout, details: WorkoutDetailsData) -> None:
    """Replace the aggregate's main detail with the matching ORM model."""

    workout.running_details = None
    workout.cycling_details = None
    workout.hiking_details = None
    workout.swimming_details = None
    workout.strength_details = None
    workout.other_details = None
    values = details.model_dump(mode="python")
    if isinstance(details, RunningWorkoutDetailsData):
        workout.running_details = RunningWorkoutDetails(**values)
    elif isinstance(details, CyclingWorkoutDetailsData):
        workout.cycling_details = CyclingWorkoutDetails(**values)
    elif isinstance(details, HikingWorkoutDetailsData):
        workout.hiking_details = HikingWorkoutDetails(**values)
    elif isinstance(details, SwimmingWorkoutDetailsData):
        pool = values.pop("pool_details")
        swimming = SwimmingWorkoutDetails(**values)
        swimming.pool_details = (
            PoolSwimmingDetails(**pool) if pool is not None else None
        )
        workout.swimming_details = swimming
    elif isinstance(details, StrengthWorkoutDetailsData):
        values["exercises_jsonb"] = _exercise_values(details)
        workout.strength_details = StrengthWorkoutDetails(**values)
    else:
        workout.other_details = OtherWorkoutDetails(**values)


def apply_exact_detail(workout: Workout, details: WorkoutDetailsData) -> bool:
    """Apply an exact-source refresh and report whether persisted values changed."""

    current = main_detail(workout)
    expected = _orm_type(details)
    if not isinstance(current, expected):
        replace_detail(workout, details)
        return True

    values = details.model_dump(mode="python")
    pool_values: dict[str, object] | None = None
    if isinstance(details, SwimmingWorkoutDetailsData):
        pool_values = values.pop("pool_details")
    if isinstance(details, StrengthWorkoutDetailsData):
        values["exercises_jsonb"] = _exercise_values(details)

    changed = _assign_nonidentical(current, values)
    if isinstance(current, SwimmingWorkoutDetails):
        if pool_values is None:
            if current.pool_details is not None:
                current.pool_details = None
                changed = True
        elif current.pool_details is None:
            current.pool_details = PoolSwimmingDetails(**pool_values)
            changed = True
        else:
            changed = _assign_nonidentical(current.pool_details, pool_values) or changed
    return changed


def _orm_type(details: WorkoutDetailsData) -> type[object]:
    if isinstance(details, RunningWorkoutDetailsData):
        return RunningWorkoutDetails
    if isinstance(details, CyclingWorkoutDetailsData):
        return CyclingWorkoutDetails
    if isinstance(details, HikingWorkoutDetailsData):
        return HikingWorkoutDetails
    if isinstance(details, SwimmingWorkoutDetailsData):
        return SwimmingWorkoutDetails
    if isinstance(details, StrengthWorkoutDetailsData):
        return StrengthWorkoutDetails
    return OtherWorkoutDetails


def _exercise_values(details: StrengthWorkoutDetailsData) -> list[object]:
    return [
        exercise.model_dump(mode="python")
        if isinstance(exercise, StrengthExercise)
        else exercise
        for exercise in details.exercises_jsonb
    ]


def _assign_nonidentical(target: object, values: dict[str, object]) -> bool:
    changed = False
    for name, value in values.items():
        if getattr(target, name) != value:
            setattr(target, name, value)
            changed = True
    return changed


__all__ = ["apply_exact_detail", "details_for_import", "replace_detail"]
