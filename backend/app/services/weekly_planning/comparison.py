"""Pure, deterministic comparison of a completed week to its published plan."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.domain.enums import Discipline
from app.schemas.fitness import FitnessWorkoutEvidence
from app.schemas.weekly_plans import PlanSession, WeeklyPlan
from app.services.fitness.calculator import _RELIABLE_HR_QUALITIES


class _ComparisonSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TargetOutcome(_ComparisonSchema):
    field: str
    planned: float
    actual: float | None
    delta_ratio: float | None


class SessionOutcome(_ComparisonSchema):
    discipline: Discipline
    planned_date: date
    completed_workout_id: UUID | None
    completed_on: date | None
    targets: tuple[TargetOutcome, ...]


class DisciplineComparison(_ComparisonSchema):
    discipline: Discipline
    sessions_planned: int
    sessions_completed: int
    minutes_planned: int
    minutes_actual: int
    adherence: float | None


class WeekComparison(_ComparisonSchema):
    week_start: date
    plan_id: UUID
    disciplines: tuple[DisciplineComparison, ...]
    sessions: tuple[SessionOutcome, ...]
    overall_adherence: float | None
    unplanned_workouts: int


def compare_week(
    *, plan_id: UUID, plan: WeeklyPlan, workouts: tuple[FitnessWorkoutEvidence, ...]
) -> WeekComparison:
    """Greedily pair same-discipline workouts to the nearest planned date."""

    planned = tuple(
        (day.date, session) for day in plan.days for session in day.sessions
    )
    by_discipline: dict[Discipline, list[FitnessWorkoutEvidence]] = defaultdict(list)
    for evidence in workouts:
        if (
            plan.week_start
            <= evidence.started_at.date()
            <= date.fromordinal(plan.week_start.toordinal() + 6)
        ):
            by_discipline[evidence.discipline].append(evidence)
    used_ids: set[UUID] = set()
    session_outcomes: list[SessionOutcome] = []
    actual_minutes: dict[Discipline, int] = defaultdict(int)
    completed: dict[Discipline, int] = defaultdict(int)
    for planned_date, session in sorted(
        planned, key=lambda item: (item[0], item[1].discipline.value)
    ):
        candidates = [
            candidate
            for candidate in by_discipline[session.discipline]
            if candidate.workout_id not in used_ids
        ]
        workout: FitnessWorkoutEvidence | None = min(
            candidates,
            key=lambda item: (
                abs((item.started_at.date() - planned_date).days),
                str(item.workout_id),
            ),
            default=None,
        )
        if workout is not None:
            used_ids.add(workout.workout_id)
            completed[session.discipline] += 1
            actual_minutes[session.discipline] += _minutes(workout)
        session_outcomes.append(
            SessionOutcome(
                discipline=session.discipline,
                planned_date=planned_date,
                completed_workout_id=workout.workout_id if workout else None,
                completed_on=workout.started_at.date() if workout else None,
                targets=_compare_targets(session, workout),
            )
        )
    disciplines = tuple(
        sorted(
            {session.discipline for _, session in planned}, key=lambda item: item.value
        )
    )
    discipline_outcomes = tuple(
        _discipline_outcome(
            discipline=discipline,
            planned=planned,
            completed=completed[discipline],
            actual_minutes=actual_minutes[discipline],
        )
        for discipline in disciplines
    )
    minutes_planned = sum(item.minutes_planned for item in discipline_outcomes)
    minutes_actual = sum(item.minutes_actual for item in discipline_outcomes)
    unmatched = sum(
        1
        for workouts_for_discipline in by_discipline.values()
        for workout in workouts_for_discipline
        if workout.workout_id not in used_ids
    )
    return WeekComparison(
        week_start=plan.week_start,
        plan_id=plan_id,
        disciplines=discipline_outcomes,
        sessions=tuple(session_outcomes),
        overall_adherence=(
            None if minutes_planned == 0 else min(1.0, minutes_actual / minutes_planned)
        ),
        unplanned_workouts=unmatched,
    )


def _compare_targets(
    session: PlanSession, workout: FitnessWorkoutEvidence | None
) -> tuple[TargetOutcome, ...]:
    targets = session.targets
    result: list[TargetOutcome] = []
    for field, planned in (
        ("duration_minutes", targets.duration_minutes),
        ("distance_meters", targets.distance_meters),
        ("average_hr_bpm", targets.average_hr_bpm),
        (
            "hr_range_bpm",
            (sum(targets.hr_range_bpm) / len(targets.hr_range_bpm))
            if targets.hr_range_bpm is not None
            else None,
        ),
        ("average_power_watts", targets.average_power_watts),
        ("pace_seconds_per_km", targets.pace_seconds_per_km),
        ("swim_pace_seconds_per_100m", targets.swim_pace_seconds_per_100m),
    ):
        if planned is None:
            continue
        actual = _target_actual(field, session.discipline, workout)
        result.append(
            TargetOutcome(
                field=field,
                planned=float(planned),
                actual=actual,
                delta_ratio=(actual / float(planned) if actual is not None else None),
            )
        )
    return tuple(result)


def _target_actual(
    field: str, discipline: Discipline, workout: FitnessWorkoutEvidence | None
) -> float | None:
    if workout is None:
        return None
    if field == "duration_minutes":
        return _duration_seconds(workout) / 60
    if field == "distance_meters":
        return workout.distance_meters
    if field in {"average_hr_bpm", "hr_range_bpm"}:
        samples = [
            item.beats_per_minute
            for item in workout.heart_rate_observations
            if item.temporal_quality in _RELIABLE_HR_QUALITIES
        ]
        return sum(samples) / len(samples) if samples else None
    if field == "average_power_watts":
        return None
    if field == "pace_seconds_per_km" and discipline is Discipline.RUNNING:
        return _pace(workout, unit_meters=1000)
    if field == "swim_pace_seconds_per_100m" and discipline is Discipline.SWIMMING:
        return _pace(workout, unit_meters=100)
    return None


def _duration_seconds(workout: FitnessWorkoutEvidence) -> int:
    return workout.moving_duration_seconds or workout.duration_seconds


def _minutes(workout: FitnessWorkoutEvidence) -> int:
    return round(_duration_seconds(workout) / 60)


def _pace(workout: FitnessWorkoutEvidence, *, unit_meters: int) -> float | None:
    if (
        workout.distance_meters is None
        or workout.distance_meters <= 0
        or workout.moving_duration_seconds is None
        or workout.moving_duration_seconds <= 0
    ):
        return None
    return workout.moving_duration_seconds / (workout.distance_meters / unit_meters)


def _discipline_outcome(
    *,
    discipline: Discipline,
    planned: tuple[tuple[date, PlanSession], ...],
    completed: int,
    actual_minutes: int,
) -> DisciplineComparison:
    sessions = tuple(
        session for _, session in planned if session.discipline is discipline
    )
    minutes_planned = sum(session.targets.duration_minutes or 0 for session in sessions)
    return DisciplineComparison(
        discipline=discipline,
        sessions_planned=len(sessions),
        sessions_completed=completed,
        minutes_planned=minutes_planned,
        minutes_actual=actual_minutes,
        adherence=(
            None if minutes_planned == 0 else min(1.0, actual_minutes / minutes_planned)
        ),
    )
