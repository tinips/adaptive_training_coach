"""Evaluator evidence projection: a workout's actual facts, with provenance.

docs/briefs/backlog/first-week-evaluator.md, "Evaluator evidence projection":
"Do not reuse FitnessWorkoutEvidence unchanged. Introduce or extend a typed,
read-only projection that can carry the actual values the design requires:
duration, moving duration, distance, canonical pace, cycling speed and
power, summary HR and timestamped HR observations with quality." Source
precedence (docs/design/first-week-evaluator.md, "Per-session comparison"):
canonical moving duration precedes elapsed; stored canonical pace precedes a
fresh derivation (there is no fresh derivation here -- the stored value is
used directly); stationary cycling average power is primary; a stored
summary average HR precedes sampled HR (comparison.py, not this module,
applies the ±10 bpm SOURCE_CONFLICT / 80%-coverage sampled-average rule).
Missing data is None, never zero.
"""

from __future__ import annotations

from typing import cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    CyclingWorkoutDetails,
    RunningWorkoutDetails,
    StrengthWorkoutDetails,
    SwimmingWorkoutDetails,
    Workout,
    WorkoutHeartRateObservation,
)
from app.domain.enums import Discipline
from app.schemas.weekly_evaluation import (
    EvaluatorHeartRateObservation,
    EvaluatorWorkoutEvidence,
)

_MISSING_DISCIPLINE_DETAIL_FLAG = "MISSING_DISCIPLINE_DETAIL"


async def _detail_for_workout(session: AsyncSession, workout: Workout) -> object | None:
    """Load the one discipline detail row directly, never via lazy-loaded
    relationship attributes (several are mapped `lazy="selectin"`, which only
    populates on the query that loads the `Workout` itself -- an object
    handed in from a prior write in this same session/task has not gone
    through that query, so touching an unrelated relationship attribute here
    risks an out-of-band lazy load AsyncSession cannot service inline).
    """

    if workout.discipline is Discipline.RUNNING:
        return cast(
            "object | None",
            await session.scalar(
                select(RunningWorkoutDetails).where(
                    RunningWorkoutDetails.workout_id == workout.id
                )
            ),
        )
    if workout.discipline is Discipline.CYCLING:
        return cast(
            "object | None",
            await session.scalar(
                select(CyclingWorkoutDetails).where(
                    CyclingWorkoutDetails.workout_id == workout.id
                )
            ),
        )
    if workout.discipline is Discipline.SWIMMING:
        return cast(
            "object | None",
            await session.scalar(
                select(SwimmingWorkoutDetails).where(
                    SwimmingWorkoutDetails.workout_id == workout.id
                )
            ),
        )
    if workout.discipline is Discipline.STRENGTH:
        return cast(
            "object | None",
            await session.scalar(
                select(StrengthWorkoutDetails).where(
                    StrengthWorkoutDetails.workout_id == workout.id
                )
            ),
        )
    return None


async def build_evaluator_workout_evidence(
    session: AsyncSession, workout: Workout
) -> EvaluatorWorkoutEvidence:
    """Project one workout's actuals for the evaluator, with provenance.

    Queries `WorkoutHeartRateObservation` directly rather than reading
    `workout.heart_rate_observations` (mapped `lazy="raise"`, meant for a
    caller that already eager-loaded it); this keeps the projection callable
    from any already-owned `Workout` without coupling to a specific loader
    option.
    """

    detail = await _detail_for_workout(session, workout)
    moving_duration = getattr(detail, "moving_duration_seconds", None)
    quality_flags: tuple[str, ...] = ()
    if workout.discipline is not Discipline.STRENGTH and detail is None:
        quality_flags = (_MISSING_DISCIPLINE_DETAIL_FLAG,)

    observations = await session.scalars(
        select(WorkoutHeartRateObservation)
        .where(WorkoutHeartRateObservation.workout_id == workout.id)
        .order_by(WorkoutHeartRateObservation.started_at)
    )

    return EvaluatorWorkoutEvidence(
        workout_id=workout.id,
        discipline=workout.discipline,
        source=workout.source,
        started_at=workout.started_at,
        duration_seconds=workout.duration_seconds,
        moving_duration_seconds=moving_duration,
        duration_source="MOVING" if moving_duration is not None else "ELAPSED",
        distance_meters=getattr(detail, "distance_meters", None),
        canonical_pace_seconds_per_km=getattr(
            detail, "average_pace_seconds_per_km", None
        ),
        canonical_pace_seconds_per_100m=getattr(
            detail, "average_pace_seconds_per_100m", None
        ),
        average_speed_kph=getattr(detail, "average_speed_kph", None),
        average_power_watts=getattr(detail, "average_power_watts", None),
        max_power_watts=getattr(detail, "max_power_watts", None),
        average_heart_rate_bpm=getattr(detail, "average_heart_rate", None),
        max_heart_rate_bpm=getattr(detail, "max_heart_rate", None),
        heart_rate_observations=tuple(
            EvaluatorHeartRateObservation(
                started_at=item.started_at,
                ended_at=item.ended_at,
                beats_per_minute=item.beats_per_minute,
                temporal_quality=item.temporal_quality,
            )
            for item in observations
        ),
        quality_flags=quality_flags,
    )
