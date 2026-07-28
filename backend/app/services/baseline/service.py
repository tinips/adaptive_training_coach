"""Application service for user-owned, versioned baseline persistence."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

from app.domain.enums import BaselineSource, Discipline
from app.schemas.baseline import BaselineActivity, BaselineCalculation
from app.services.baseline.engine import BaselineEngine


class ActivityRecord(Protocol):
    """Fields required from a persisted activity."""

    id: UUID
    sport: Discipline
    started_at: datetime
    duration_seconds: int
    distance_meters: float | None
    average_heart_rate: float | None


class BaselineActivityRepository(Protocol):
    """User-owned activity query boundary."""

    async def list_activities(
        self,
        *,
        user_id: UUID,
        started_at_or_after: datetime,
        started_at_or_before: datetime,
        include_deleted: bool = False,
    ) -> Sequence[ActivityRecord]: ...


class BaselineResultRepository(Protocol):
    """Versioned baseline persistence boundary."""

    async def create(
        self,
        *,
        user_id: UUID,
        generated_at: datetime,
        analysis_start: datetime,
        analysis_end: datetime,
        source: object,
        status: object,
        overall_confidence: float,
        disciplines: list[dict[str, object]],
    ) -> object: ...


class BaselineService:
    """Recalculate and append one baseline version for exactly one user."""

    def __init__(
        self,
        *,
        activities: BaselineActivityRepository,
        baselines: BaselineResultRepository,
        engine: BaselineEngine | None = None,
        analysis_days: int = 56,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if analysis_days < 1:
            raise ValueError("analysis_days must be positive.")
        self._activities = activities
        self._baselines = baselines
        self._engine = engine or BaselineEngine()
        self._analysis_days = analysis_days
        self._clock = clock or (lambda: datetime.now(UTC))

    async def recalculate(
        self,
        *,
        user_id: UUID,
        analysis_end: datetime | None = None,
        source: BaselineSource = BaselineSource.STRAVA,
    ) -> BaselineCalculation:
        """Calculate from user-scoped activities and persist a new version."""

        end = analysis_end or self._clock()
        if end.tzinfo is None or end.utcoffset() is None:
            raise ValueError("analysis_end must be timezone-aware.")
        end = end.astimezone(UTC)
        start = end - timedelta(days=self._analysis_days)
        records = await self._activities.list_activities(
            user_id=user_id,
            started_at_or_after=start,
            started_at_or_before=end,
            include_deleted=False,
        )
        inputs = [
            BaselineActivity(
                id=record.id,
                discipline=record.sport,
                started_at=self._persisted_utc(record.started_at),
                duration_seconds=record.duration_seconds,
                distance_meters=record.distance_meters,
                average_heart_rate=record.average_heart_rate,
            )
            for record in records
        ]
        generated_at = self._clock()
        if generated_at.tzinfo is None or generated_at.utcoffset() is None:
            raise ValueError("The baseline clock must return an aware timestamp.")
        generated_at = generated_at.astimezone(UTC)
        calculation = self._engine.calculate(
            activities=inputs,
            analysis_start=start,
            analysis_end=end,
            generated_at=generated_at,
        ).model_copy(update={"source": source})
        await self._baselines.create(
            user_id=user_id,
            generated_at=calculation.generated_at,
            analysis_start=calculation.analysis_start,
            analysis_end=calculation.analysis_end,
            source=calculation.source,
            status=calculation.status,
            overall_confidence=calculation.overall_confidence,
            disciplines=[
                item.model_dump(mode="python") for item in calculation.disciplines
            ],
        )
        return calculation

    @staticmethod
    def _persisted_utc(value: datetime) -> datetime:
        # PostgreSQL retains offsets. SQLite drops them in portable repository
        # tests; application persistence defines every stored timestamp as UTC.
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
