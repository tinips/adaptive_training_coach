"""Ownership-scoped persistence helpers for immutable fitness baselines."""

from __future__ import annotations

import uuid
from collections.abc import Collection
from datetime import datetime
from typing import cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import AthleteBaselineAssessment, SwimmingWorkoutDetails, Workout
from app.domain.enums import Discipline


class FitnessRepository:
    """Read owned workout evidence and immutable baseline rows."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def workouts_for_window(
        self,
        *,
        athlete_id: uuid.UUID,
        disciplines: Collection[Discipline],
        started_at: datetime,
        ended_at: datetime,
    ) -> tuple[Workout, ...]:
        """Load only owned workouts and their calculator inputs."""

        if not disciplines:
            return ()
        result = await self._session.scalars(
            select(Workout)
            .where(
                Workout.athlete_id == athlete_id,
                Workout.discipline.in_(tuple(disciplines)),
                Workout.started_at >= started_at,
                Workout.started_at <= ended_at,
            )
            .options(
                selectinload(Workout.heart_rate_observations),
                selectinload(Workout.running_details),
                selectinload(Workout.cycling_details),
                selectinload(Workout.hiking_details),
                selectinload(Workout.swimming_details).selectinload(
                    SwimmingWorkoutDetails.pool_details
                ),
                selectinload(Workout.strength_details),
                selectinload(Workout.other_details),
            )
            .order_by(Workout.discipline, Workout.started_at, Workout.id)
        )
        return tuple(result.unique().all())

    async def baseline_for_discipline(
        self,
        *,
        athlete_id: uuid.UUID,
        discipline: Discipline,
    ) -> AthleteBaselineAssessment | None:
        return cast(
            AthleteBaselineAssessment | None,
            await self._session.scalar(
                select(AthleteBaselineAssessment).where(
                    AthleteBaselineAssessment.athlete_id == athlete_id,
                    AthleteBaselineAssessment.discipline == discipline,
                )
            ),
        )

    async def latest_workout_started_at(
        self,
        *,
        athlete_id: uuid.UUID,
        discipline: Discipline,
    ) -> datetime | None:
        """Return the latest workout owned by this athlete and discipline."""

        return cast(
            datetime | None,
            await self._session.scalar(
                select(Workout.started_at)
                .where(
                    Workout.athlete_id == athlete_id,
                    Workout.discipline == discipline,
                )
                .order_by(Workout.started_at.desc(), Workout.id.desc())
                .limit(1)
            ),
        )
