"""Pure baseline-calculation tests."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from app.domain.enums import ActivitySource, Discipline
from app.schemas.fitness import FitnessWorkoutEvidence
from app.services.fitness.calculator import calculate_baseline_window

NOW = datetime(2026, 8, 28, 12, tzinfo=UTC)


def _workout(
    *,
    discipline: Discipline,
    started_at: datetime,
    distance_meters: float | None,
    duration_seconds: int = 3600,
) -> FitnessWorkoutEvidence:
    return FitnessWorkoutEvidence(
        workout_id=uuid.uuid4(),
        discipline=discipline,
        source=ActivitySource.MANUAL,
        started_at=started_at,
        duration_seconds=duration_seconds,
        fitness_input_updated_at=NOW,
        distance_meters=distance_meters,
    )


def test_zero_distance_is_treated_as_unmeasured_not_as_zero_speed() -> None:
    """An indoor ride reports 0 m because distance does not apply."""

    calculation = calculate_baseline_window(
        discipline=Discipline.CYCLING,
        workouts=(
            _workout(
                discipline=Discipline.CYCLING,
                started_at=NOW - timedelta(days=8),
                distance_meters=0.0,
            ),
            _workout(
                discipline=Discipline.CYCLING,
                started_at=NOW - timedelta(days=2),
                distance_meters=0.0,
            ),
        ),
        window_started_at=NOW - timedelta(days=30),
        window_ended_at=NOW,
        calculated_at=NOW,
    )

    assert calculation is not None
    assert calculation.session_count == 2
    assert calculation.distance_session_count == 0
    assert calculation.known_distance_meters is None
    assert calculation.discipline_metrics_jsonb["elapsed_speed_kph"] is None
    assert "MISSING_DISTANCE" in calculation.quality_flags_jsonb


def test_real_distance_is_still_counted() -> None:
    calculation = calculate_baseline_window(
        discipline=Discipline.RUNNING,
        workouts=(
            _workout(
                discipline=Discipline.RUNNING,
                started_at=NOW - timedelta(days=3),
                distance_meters=10_000.0,
                duration_seconds=3600,
            ),
        ),
        window_started_at=NOW - timedelta(days=30),
        window_ended_at=NOW,
        calculated_at=NOW,
    )

    assert calculation is not None
    assert calculation.distance_session_count == 1
    assert calculation.known_distance_meters == 10_000.0
    assert "MISSING_DISTANCE" not in calculation.quality_flags_jsonb


def test_confidence_is_capped_when_no_heart_rate_is_available() -> None:
    """Volume alone must not read as full confidence: effort is unknown."""

    workouts = tuple(
        _workout(
            discipline=Discipline.RUNNING,
            started_at=NOW - timedelta(days=day),
            distance_meters=10_000.0,
        )
        for day in (2, 4, 6, 8, 10, 12)
    )
    calculation = calculate_baseline_window(
        discipline=Discipline.RUNNING,
        workouts=workouts,
        window_started_at=NOW - timedelta(days=30),
        window_ended_at=NOW,
        calculated_at=NOW,
    )

    assert calculation is not None
    assert calculation.reliable_hr_sample_count == 0
    assert calculation.confidence <= 0.6


def test_calculation_version_is_two() -> None:
    from app.services.fitness.calculator import CALCULATION_VERSION

    assert CALCULATION_VERSION == 2
