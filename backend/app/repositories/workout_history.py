"""Owned reads for the workout-history dashboard."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import SwimmingWorkoutDetails, Workout
from app.domain.enums import Discipline


class WorkoutHistoryRepository:
    """Load only the detail records needed to render one athlete's history."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_owned_for_range(
        self,
        *,
        athlete_id: uuid.UUID,
        started_at: datetime,
        ended_before: datetime,
        discipline: Discipline | None,
    ) -> tuple[Workout, ...]:
        statement = select(Workout).where(
            Workout.athlete_id == athlete_id,
            Workout.started_at >= started_at,
            Workout.started_at < ended_before,
        )
        if discipline is not None:
            statement = statement.where(Workout.discipline == discipline)
        result = await self._session.scalars(
            statement.options(
                selectinload(Workout.running_details),
                selectinload(Workout.cycling_details),
                selectinload(Workout.hiking_details),
                selectinload(Workout.swimming_details).selectinload(
                    SwimmingWorkoutDetails.pool_details
                ),
                selectinload(Workout.strength_details),
                selectinload(Workout.other_details),
            ).order_by(Workout.started_at.desc(), Workout.id.desc())
        )
        return tuple(result.unique().all())

    async def available_disciplines_for_range(
        self,
        *,
        athlete_id: uuid.UUID,
        started_at: datetime,
        ended_before: datetime,
    ) -> tuple[Discipline, ...]:
        result = await self._session.scalars(
            select(Workout.discipline)
            .where(
                Workout.athlete_id == athlete_id,
                Workout.started_at >= started_at,
                Workout.started_at < ended_before,
            )
            .distinct()
            .order_by(Workout.discipline)
        )
        return tuple(result.all())
