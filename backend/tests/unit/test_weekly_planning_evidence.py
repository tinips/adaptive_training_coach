"""Pure evidence-transform tests for the weekly planner."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

from app.domain.enums import ActivitySource, Discipline, DisciplineEvidenceState
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
            # Historical Apple-origin rows remain readable after importer removal.
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
    assert readiness.disciplines[0].state is DisciplineEvidenceState.WELL_EVIDENCED


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
    assert readiness.disciplines[0].state is not DisciplineEvidenceState.WELL_EVIDENCED


def _running(started_at: datetime) -> FitnessWorkoutEvidence:
    return _workout(source=ActivitySource.TCX, started_at=started_at)


def _readiness_for(calculations: dict[Discipline, object]) -> object:
    return build_plan_readiness(
        week_start=date(2026, 8, 31),
        window_started_at=NOW - timedelta(days=30),
        window_ended_at=NOW,
        calculations=calculations,
    )


def _calculation_with(discipline: Discipline, day_offsets: tuple[int, ...]):
    workouts = tuple(
        FitnessWorkoutEvidence(
            workout_id=uuid.uuid4(),
            discipline=discipline,
            source=ActivitySource.MANUAL,
            started_at=NOW - timedelta(days=offset),
            duration_seconds=1800,
            fitness_input_updated_at=NOW,
            distance_meters=5_000,
        )
        for offset in day_offsets
    )
    return calculate_baseline_window(
        discipline=discipline,
        workouts=workouts,
        window_started_at=NOW - timedelta(days=30),
        window_ended_at=NOW,
        calculated_at=NOW,
    )


def test_one_thin_sport_no_longer_vetoes_the_whole_plan() -> None:
    """A triathlete with 2 rides, 2 runs and 1 swim must get a plan."""

    readiness = _readiness_for(
        {
            Discipline.CYCLING: _calculation_with(Discipline.CYCLING, (8, 2)),
            Discipline.RUNNING: _calculation_with(Discipline.RUNNING, (6, 4)),
            Discipline.SWIMMING: _calculation_with(Discipline.SWIMMING, (7,)),
        }
    )

    assert readiness.total_session_count == 5
    assert readiness.total_active_day_count == 5
    assert readiness.ready is True
    states = {row.discipline: row.state for row in readiness.disciplines}
    assert states[Discipline.CYCLING] is DisciplineEvidenceState.THIN
    assert states[Discipline.RUNNING] is DisciplineEvidenceState.THIN
    assert states[Discipline.SWIMMING] is DisciplineEvidenceState.THIN


def test_a_single_sport_athlete_reaches_the_floor_alone() -> None:
    """A runner who only runs must not be penalised for having one sport."""

    readiness = _readiness_for(
        {Discipline.RUNNING: _calculation_with(Discipline.RUNNING, (6, 4, 2))}
    )

    assert readiness.ready is True
    assert readiness.disciplines[0].state is DisciplineEvidenceState.WELL_EVIDENCED


def test_a_sport_with_no_sessions_is_none_and_does_not_block() -> None:
    readiness = _readiness_for(
        {
            Discipline.RUNNING: _calculation_with(Discipline.RUNNING, (6, 4, 2)),
            Discipline.SWIMMING: None,
        }
    )

    assert readiness.ready is True
    states = {row.discipline: row.state for row in readiness.disciplines}
    assert states[Discipline.SWIMMING] is DisciplineEvidenceState.NONE


def test_below_the_floor_is_not_ready() -> None:
    readiness = _readiness_for(
        {Discipline.RUNNING: _calculation_with(Discipline.RUNNING, (6, 6))}
    )

    assert readiness.total_session_count == 2
    assert readiness.total_active_day_count == 1
    assert readiness.ready is False
