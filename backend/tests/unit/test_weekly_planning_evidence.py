"""Pure evidence-transform tests for the weekly planner."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

from app.domain.enums import ActivitySource, Discipline
from app.schemas.fitness import FitnessWorkoutEvidence
from app.services.fitness.calculator import calculate_baseline_window
from app.services.weekly_planning.evidence import build_plan_readiness

NOW = datetime(2026, 8, 21, 12, tzinfo=UTC)


def _workout(
    *,
    source: ActivitySource,
    started_at: datetime,
) -> FitnessWorkoutEvidence:
    return FitnessWorkoutEvidence(
        workout_id=uuid.uuid4(),
        discipline=Discipline.RUNNING,
        source=source,
        started_at=started_at,
        duration_seconds=1800,
        fitness_input_updated_at=NOW,
        distance_meters=5_000,
    )


def test_readiness_uses_deduplicated_calculation_output() -> None:
    first = NOW - timedelta(days=20)
    calculation = calculate_baseline_window(
        discipline=Discipline.RUNNING,
        workouts=(
            _workout(source=ActivitySource.APPLE_HEALTH, started_at=first),
            _workout(source=ActivitySource.TCX, started_at=first),
            _workout(source=ActivitySource.TCX, started_at=NOW - timedelta(days=18)),
            _workout(source=ActivitySource.TCX, started_at=NOW - timedelta(days=16)),
        ),
        window_started_at=NOW - timedelta(days=30),
        window_ended_at=NOW,
        calculated_at=NOW,
    )

    readiness = build_plan_readiness(
        week_start=date(2026, 8, 24),
        window_started_at=NOW - timedelta(days=30),
        window_ended_at=NOW,
        calculations={Discipline.RUNNING: calculation},
    )

    assert calculation is not None
    assert calculation.session_count == 3
    assert readiness.disciplines[0].ready is True


def test_readiness_requires_two_active_days_even_with_three_sessions() -> None:
    calculation = calculate_baseline_window(
        discipline=Discipline.RUNNING,
        workouts=tuple(
            _workout(source=ActivitySource.TCX, started_at=NOW - timedelta(days=5))
            for _ in range(3)
        ),
        window_started_at=NOW - timedelta(days=30),
        window_ended_at=NOW,
        calculated_at=NOW,
    )

    readiness = build_plan_readiness(
        week_start=date(2026, 8, 24),
        window_started_at=NOW - timedelta(days=30),
        window_ended_at=NOW,
        calculations={Discipline.RUNNING: calculation},
    )

    assert calculation is not None
    assert calculation.session_count == 3
    assert readiness.disciplines[0].active_day_count == 1
    assert readiness.disciplines[0].ready is False
