"""Owner-scoped synchronization for imported workout heart-rate facts."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utc_now
from app.db.models import Workout, WorkoutHeartRateObservation
from app.domain.enums import ActivitySource
from app.repositories.errors import OwnedRecordNotFoundError
from app.services.activities.contracts import (
    ActivityImportValidationError,
    ActivitySourceConflictError,
    HeartRateObservationData,
)
from app.services.activities.normalization import as_utc


class HeartRateObservationRepository:
    """Keep one source's observations aligned with its latest exact import."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def synchronize(
        self,
        *,
        user_id: uuid.UUID,
        workout_id: uuid.UUID,
        source: ActivitySource,
        observations: Sequence[HeartRateObservationData],
        import_job_id: uuid.UUID | None,
    ) -> bool:
        workout = await self._session.scalar(
            select(Workout).where(
                Workout.id == workout_id,
                Workout.athlete_id == user_id,
            )
        )
        if workout is None:
            raise OwnedRecordNotFoundError("heart-rate workout not found")

        incoming = {item.source_record_key: item for item in observations}
        if len(incoming) != len(observations):
            raise ActivityImportValidationError(
                "Duplicate heart-rate source record key"
            )
        for item in observations:
            if item.source is not source:
                raise ActivityImportValidationError(
                    "Heart-rate observation source does not match workout source"
                )

        current = tuple(
            (
                await self._session.scalars(
                    select(WorkoutHeartRateObservation).where(
                        WorkoutHeartRateObservation.user_id == user_id,
                        WorkoutHeartRateObservation.workout_id == workout_id,
                        WorkoutHeartRateObservation.source == source,
                    )
                )
            ).all()
        )
        current_by_key = {item.source_record_key: item for item in current}

        if incoming:
            collisions = tuple(
                (
                    await self._session.scalars(
                        select(WorkoutHeartRateObservation).where(
                            WorkoutHeartRateObservation.user_id == user_id,
                            WorkoutHeartRateObservation.source == source,
                            WorkoutHeartRateObservation.source_record_key.in_(incoming),
                        )
                    )
                ).all()
            )
            if any(item.workout_id != workout_id for item in collisions):
                raise ActivitySourceConflictError(
                    "Heart-rate source key belongs to another workout"
                )

        changed = False
        fitness_input_changed = False
        for key, persisted in current_by_key.items():
            if key not in incoming:
                await self._session.delete(persisted)
                changed = True
                fitness_input_changed = True

        for key, item in incoming.items():
            values = {
                "source_name": item.source_name,
                "started_at": as_utc(item.started_at),
                "ended_at": as_utc(item.ended_at),
                "beats_per_minute": item.beats_per_minute,
                "temporal_quality": item.temporal_quality,
                "import_job_id": import_job_id,
            }
            existing = current_by_key.get(key)
            if existing is None:
                self._session.add(
                    WorkoutHeartRateObservation(
                        user_id=user_id,
                        workout_id=workout_id,
                        source=source,
                        source_record_key=key,
                        **values,
                    )
                )
                changed = True
                fitness_input_changed = True
                continue
            for name, value in values.items():
                current_value = getattr(existing, name)
                same_value = (
                    as_utc(current_value) == as_utc(value)
                    if name in {"started_at", "ended_at"}
                    and isinstance(current_value, datetime)
                    and isinstance(value, datetime)
                    else current_value == value
                )
                if not same_value:
                    setattr(existing, name, value)
                    changed = True
                    if name in {
                        "started_at",
                        "ended_at",
                        "beats_per_minute",
                        "temporal_quality",
                    }:
                        fitness_input_changed = True

        if fitness_input_changed:
            # HR facts are calculator input, while their own rows have no
            # fitness watermark. Keep the parent workout's input version in
            # sync for an immutable baseline created in this same transaction.
            workout.fitness_input_updated_at = utc_now()
        await self._session.flush()
        return changed

    async def list_for_workout(
        self,
        *,
        user_id: uuid.UUID,
        workout_id: uuid.UUID,
    ) -> tuple[WorkoutHeartRateObservation, ...]:
        statement = (
            select(WorkoutHeartRateObservation)
            .join(Workout, Workout.id == WorkoutHeartRateObservation.workout_id)
            .where(
                WorkoutHeartRateObservation.user_id == user_id,
                WorkoutHeartRateObservation.workout_id == workout_id,
                Workout.athlete_id == user_id,
            )
            .order_by(
                WorkoutHeartRateObservation.started_at,
                WorkoutHeartRateObservation.source_record_key,
            )
        )
        return tuple((await self._session.scalars(statement)).all())


__all__ = ["HeartRateObservationRepository"]
