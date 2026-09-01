"""Workout validation, normalization, and serializer-boundary tests."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import BaseModel, ValidationError

from app.domain.enums import (
    ActivitySource,
    CyclingType,
    Discipline,
    HikingType,
    RunningType,
    StrengthType,
    SwimmingEnvironment,
    SwimmingStroke,
)
from app.schemas.workouts import (
    CyclingWorkoutDetailsData,
    HikingWorkoutDetailsData,
    OtherWorkoutDetailsData,
    PoolSwimmingDetailsData,
    RunningWorkoutDetailsData,
    StrengthWorkoutDetailsData,
    SwimmingWorkoutDetailsData,
    WorkoutCreate,
    WorkoutRead,
    pace_seconds_per_unit,
    speed_kph,
    values_conflict,
)

NOW = datetime(2026, 7, 30, 8, tzinfo=UTC)


def test_workout_create_requires_positive_duration_and_matching_details() -> None:
    details = RunningWorkoutDetailsData(running_type=RunningType.OUTDOOR)

    with pytest.raises(ValidationError):
        WorkoutCreate(
            athlete_id=uuid4(),
            discipline=Discipline.RUNNING,
            started_at=NOW,
            duration_seconds=0,
            source=ActivitySource.MANUAL,
            details=details,
        )

    with pytest.raises(ValidationError, match="requires RUNNING details"):
        WorkoutCreate(
            athlete_id=uuid4(),
            discipline=Discipline.CYCLING,
            started_at=NOW,
            duration_seconds=3600,
            source=ActivitySource.MANUAL,
            details=details,
        )

    with pytest.raises(ValidationError, match="timezone-aware"):
        WorkoutCreate(
            athlete_id=uuid4(),
            discipline=Discipline.RUNNING,
            started_at=datetime(2026, 7, 30, 8),
            duration_seconds=3600,
            source=ActivitySource.MANUAL,
            details=details,
        )


@pytest.mark.parametrize(
    ("schema", "payload"),
    [
        (
            RunningWorkoutDetailsData,
            {"running_type": RunningType.OUTDOOR, "distance_meters": -1},
        ),
        (
            CyclingWorkoutDetailsData,
            {"cycling_type": CyclingType.ROAD, "max_speed_kph": -1},
        ),
        (
            SwimmingWorkoutDetailsData,
            {
                "swimming_environment": SwimmingEnvironment.OPEN_WATER,
                "average_heart_rate": -1,
            },
        ),
        (
            OtherWorkoutDetailsData,
            {"activity_name": "Padel", "distance_meters": -1},
        ),
        (
            PoolSwimmingDetailsData,
            {"pool_length_meters": 0},
        ),
    ],
)
def test_explicit_metrics_reject_negative_values(
    schema: type[BaseModel],
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        schema.model_validate(payload)


def test_other_activity_name_matches_database_length_boundary() -> None:
    assert OtherWorkoutDetailsData(activity_name="x" * 255).activity_name == ("x" * 255)
    with pytest.raises(ValidationError):
        OtherWorkoutDetailsData(activity_name="x" * 256)


def test_pace_speed_derivation_overrides_conflicting_imported_values() -> None:
    running = RunningWorkoutDetailsData(
        running_type=RunningType.OUTDOOR,
        distance_meters=10_000,
        moving_duration_seconds=3600,
        average_pace_seconds_per_km=999,
    )
    hiking = HikingWorkoutDetailsData(
        hiking_type=HikingType.TREKKING,
        distance_meters=5000,
        moving_duration_seconds=3600,
        average_pace_seconds_per_km=1,
    )
    cycling = CyclingWorkoutDetailsData(
        cycling_type=CyclingType.ROAD,
        distance_meters=20_000,
        moving_duration_seconds=3600,
        average_speed_kph=1,
    )
    swimming = SwimmingWorkoutDetailsData(
        swimming_environment=SwimmingEnvironment.OPEN_WATER,
        distance_meters=1000,
        moving_duration_seconds=1200,
        average_pace_seconds_per_100m=1,
    )

    assert running.average_pace_seconds_per_km == 360
    assert hiking.average_pace_seconds_per_km == 720
    assert cycling.average_speed_kph == 20
    assert swimming.average_pace_seconds_per_100m == 120
    assert (
        pace_seconds_per_unit(
            distance_meters=10_000,
            moving_duration_seconds=3600,
            unit_meters=1000,
        )
        == 360
    )
    assert (
        speed_kph(
            distance_meters=20_000,
            moving_duration_seconds=3600,
        )
        == 20
    )
    assert values_conflict(999, running.average_pace_seconds_per_km)
    assert values_conflict(20.2, cycling.average_speed_kph) is False
    assert (
        pace_seconds_per_unit(
            distance_meters=0,
            moving_duration_seconds=3600,
            unit_meters=1000,
        )
        is None
    )


def test_pool_and_open_water_swimming_invariants() -> None:
    pool = PoolSwimmingDetailsData(
        pool_length_meters=25,
        total_lengths=40,
        primary_stroke=SwimmingStroke.FREESTYLE,
    )

    with pytest.raises(ValidationError, match="requires pool_details"):
        SwimmingWorkoutDetailsData(
            swimming_environment=SwimmingEnvironment.POOL,
            distance_meters=1000,
        )

    with pytest.raises(ValidationError, match="cannot have pool_details"):
        SwimmingWorkoutDetailsData(
            swimming_environment=SwimmingEnvironment.OPEN_WATER,
            distance_meters=1000,
            pool_details=pool,
        )

    valid_pool = SwimmingWorkoutDetailsData(
        swimming_environment=SwimmingEnvironment.POOL,
        distance_meters=1000,
        moving_duration_seconds=1200,
        pool_details=pool,
    )
    valid_open_water = SwimmingWorkoutDetailsData(
        swimming_environment=SwimmingEnvironment.OPEN_WATER,
        distance_meters=1500,
    )

    assert valid_pool.pool_details == pool
    assert valid_pool.average_pace_seconds_per_100m == 120
    assert valid_open_water.pool_details is None


def test_strength_json_has_exact_keys_and_allows_empty_imports() -> None:
    structured = StrengthWorkoutDetailsData.model_validate(
        {
            "strength_type": StrengthType.CALISTHENICS,
            "session_focus": "Upper body",
            "exercises_jsonb": [
                {
                    "exercise": "Pull-up",
                    "sets": [
                        {"reps": 10, "kg": 0},
                        {"reps": 8, "kg": 10},
                    ],
                }
            ],
        }
    )
    imported_without_exercises = WorkoutCreate(
        athlete_id=uuid4(),
        discipline=Discipline.STRENGTH,
        started_at=NOW,
        duration_seconds=1800,
        source=ActivitySource.OTHER_IMPORT,
        external_id="strength-import-1",
        details=StrengthWorkoutDetailsData(
            strength_type=StrengthType.OTHER,
            exercises_jsonb=[],
        ),
    )

    assert structured.exercises_jsonb[0].model_dump() == {
        "exercise": "Pull-up",
        "sets": [
            {"reps": 10, "kg": 0.0},
            {"reps": 8, "kg": 10.0},
        ],
    }
    assert isinstance(
        imported_without_exercises.details,
        StrengthWorkoutDetailsData,
    )
    assert imported_without_exercises.details.exercises_jsonb == []

    for invalid_set in (
        {"reps": 10, "kg": 0, "duration_seconds": 30},
        {"reps": -1, "kg": 0},
        {"reps": 10, "kg": -1},
    ):
        with pytest.raises(ValidationError):
            StrengthWorkoutDetailsData.model_validate(
                {
                    "strength_type": StrengthType.GYM,
                    "exercises_jsonb": [
                        {
                            "exercise": "Squat",
                            "sets": [invalid_set],
                        }
                    ],
                }
            )


def test_generic_read_schema_and_cycling_schema_exclude_forbidden_metrics() -> None:
    assert set(WorkoutRead.model_fields) == {
        "id",
        "athlete_id",
        "discipline",
        "started_at",
        "duration_seconds",
        "source",
        "external_id",
        "title",
        "notes",
        "created_at",
        "updated_at",
        "details",
    }
    assert "is_indoor" not in CyclingWorkoutDetailsData.model_fields
    assert not any(
        "watt" in field or "power" in field
        for field in CyclingWorkoutDetailsData.model_fields
    )
