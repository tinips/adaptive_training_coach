"""Pure matching and target-join coverage for finished weekly plans."""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import uuid4

from app.domain.enums import ActivitySource, Discipline
from app.schemas.fitness import FitnessWorkoutEvidence
from app.schemas.weekly_plans import PlanDay, PlanSession, WeeklyPlan
from app.services.weekly_planning.comparison import compare_week


def _plan() -> WeeklyPlan:
    week_start = date(2026, 9, 7)
    return WeeklyPlan(
        week_start=week_start,
        days=tuple(
            PlanDay(
                date=date.fromordinal(week_start.toordinal() + offset),
                sessions=(
                    PlanSession(
                        discipline=Discipline.CYCLING,
                        purpose="Build aerobic consistency.",
                        intensity={
                            "metric": "RPE",
                            "target_range": [2, 3],
                            "rpe_range": [2, 3],
                            "guidance": "Easy, conversational effort.",
                        },
                        objective="Build aerobic consistency.",
                        targets={"duration_minutes": 45, "average_power_watts": 160},
                        execution="Ride easily throughout.",
                    ),
                )
                if offset in {1, 3}
                else (),
                rest_note=None if offset in {1, 3} else "Rest and recover.",
            )
            for offset in range(7)
        ),
    )


def _workout(day: int) -> FitnessWorkoutEvidence:
    return FitnessWorkoutEvidence(
        workout_id=uuid4(),
        discipline=Discipline.CYCLING,
        source=ActivitySource.MANUAL,
        started_at=datetime(2026, 9, day, 9, tzinfo=UTC),
        duration_seconds=2700,
        moving_duration_seconds=2700,
        fitness_input_updated_at=datetime(2026, 9, day, 10, tzinfo=UTC),
    )


def test_nearest_date_matching_keeps_extra_workouts_as_unplanned() -> None:
    comparison = compare_week(
        plan_id=uuid4(),
        plan=_plan(),
        workouts=(_workout(10), _workout(11), _workout(12)),
    )

    assert comparison.sessions[0].completed_on == date(2026, 9, 10)
    assert comparison.sessions[1].completed_on == date(2026, 9, 11)
    assert comparison.unplanned_workouts == 1
    assert comparison.disciplines[0].adherence == 1.0
    power = comparison.sessions[0].targets[1]
    assert power.field == "average_power_watts"
    assert power.actual is None
    assert power.delta_ratio is None
