"""Owner-scoped workout persistence with exact source deduplication."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, cast

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utc_now
from app.db.models import Workout
from app.domain.enums import ActivitySource
from app.integrations.apple_health.models import ParsedWorkout
from app.repositories.activity_source_links import ActivitySourceLinkRepository
from app.repositories.errors import OwnedRecordNotFoundError
from app.repositories.heart_rate_observations import HeartRateObservationRepository
from app.repositories.workout_detail_mapper import (
    apply_exact_detail,
    details_for_import,
    replace_detail,
)
from app.schemas.workouts import (
    WorkoutCreate,
    main_detail,
    serialize_workout,
)
from app.services.activities.adapters.apple_health import from_apple_health
from app.services.activities.adapters.tcx import from_tcx
from app.services.activities.contracts import (
    ActivityImportData,
    ActivityImportValidationError,
    ActivitySourceConflictError,
    ActivityUpsertOutcome,
)
from app.services.activities.normalization import (
    as_utc,
    finalize_source_metadata,
    normalize_import,
    record_canonical_conflicts,
    validate_import,
)

if TYPE_CHECKING:
    from app.integrations.tcx.models import ParsedTCXActivity
    from app.schemas.workouts import WorkoutDetailsData, WorkoutRead


class TrainingActivityRepository:
    """Main workout repository entry point; deduplication is exact only."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._source_links = ActivitySourceLinkRepository(session)
        self._heart_rate = HeartRateObservationRepository(session)

    async def get_by_source_key(
        self,
        *,
        user_id: uuid.UUID,
        source: ActivitySource,
        external_id: str,
    ) -> Workout | None:
        """Resolve exactly athlete/source/external-id."""

        return await self._source_links.get_workout(
            user_id=user_id,
            source=source,
            external_id=external_id,
        )

    async def get_owned(
        self,
        *,
        user_id: uuid.UUID,
        workout_id: uuid.UUID,
    ) -> Workout | None:
        """Load one workout strictly within its athlete ownership boundary."""

        workout: Workout | None = await self._session.scalar(
            select(Workout).where(
                Workout.id == workout_id,
                Workout.athlete_id == user_id,
            )
        )
        return workout

    async def create_manual(self, request: WorkoutCreate) -> Workout:
        """Create one validated manual workout."""

        if request.source is not ActivitySource.MANUAL:
            raise ActivityImportValidationError(
                "Manual creation requires MANUAL source"
            )
        workout = Workout(
            athlete_id=request.athlete_id,
            discipline=request.discipline,
            started_at=as_utc(request.started_at),
            duration_seconds=request.duration_seconds,
            source=request.source,
            external_id=None,
            title=request.title,
            notes=request.notes,
        )
        self._session.add(workout)
        replace_detail(workout, request.details)
        await self._session.flush()
        main_detail(workout)
        return workout

    async def serialize_owned(
        self,
        *,
        user_id: uuid.UUID,
        workout_id: uuid.UUID,
    ) -> WorkoutRead:
        """Return the generic workout plus its discipline detail."""

        workout = await self.get_owned(user_id=user_id, workout_id=workout_id)
        if workout is None:
            raise OwnedRecordNotFoundError("Workout not found")
        return serialize_workout(workout)

    async def import_apple_workout(
        self,
        *,
        user_id: uuid.UUID,
        workout: ParsedWorkout,
        file_sha256: str | None,
        import_job_id: uuid.UUID | None,
    ) -> tuple[Workout, ActivityUpsertOutcome]:
        """Import one Apple workout using its deterministic fingerprint."""

        return await self._import_activity(
            user_id=user_id,
            incoming=from_apple_health(workout),
            file_sha256=file_sha256,
            import_job_id=import_job_id,
        )

    async def import_tcx_activity(
        self,
        *,
        user_id: uuid.UUID,
        parsed: ParsedTCXActivity,
        file_sha256: str | None,
        import_job_id: uuid.UUID | None,
    ) -> tuple[Workout, ActivityUpsertOutcome]:
        """Import one TCX workout using exact provider identity or fingerprint."""

        return await self._import_activity(
            user_id=user_id,
            incoming=from_tcx(parsed),
            file_sha256=file_sha256,
            import_job_id=import_job_id,
        )

    async def _import_activity(
        self,
        *,
        user_id: uuid.UUID,
        incoming: ActivityImportData,
        file_sha256: str | None,
        import_job_id: uuid.UUID | None,
    ) -> tuple[Workout, ActivityUpsertOutcome]:
        normalize_import(incoming)
        validate_import(incoming)
        details = details_for_import(incoming)
        record_canonical_conflicts(incoming, details)
        finalize_source_metadata(incoming)
        external_id = cast(str, incoming.external_id)

        existing = await self.get_by_source_key(
            user_id=user_id,
            source=incoming.source,
            external_id=external_id,
        )
        if existing is not None:
            return await self._refresh_exact(
                user_id=user_id,
                workout=existing,
                incoming=incoming,
                details=details,
                file_sha256=file_sha256,
                import_job_id=import_job_id,
            )

        inserted = Workout(
            athlete_id=user_id,
            source=incoming.source,
            external_id=external_id,
            discipline=incoming.discipline,
            title=incoming.title,
            notes=incoming.notes,
            started_at=as_utc(incoming.started_at),
            duration_seconds=incoming.duration_seconds,
        )
        try:
            async with self._session.begin_nested():
                self._session.add(inserted)
                replace_detail(inserted, details)
                await self._session.flush()
                await self._source_links.ensure(
                    user_id=user_id,
                    workout=inserted,
                    incoming=incoming,
                    file_sha256=file_sha256,
                    import_job_id=import_job_id,
                )
        except IntegrityError:
            winner = await self.get_by_source_key(
                user_id=user_id,
                source=incoming.source,
                external_id=external_id,
            )
            if winner is None:
                raise
            return await self._refresh_exact(
                user_id=user_id,
                workout=winner,
                incoming=incoming,
                details=details,
                file_sha256=file_sha256,
                import_job_id=import_job_id,
            )

        await self._source_links.attach_import_job(
            user_id=user_id,
            import_job_id=import_job_id,
            workout=inserted,
        )
        await self._heart_rate.synchronize(
            user_id=user_id,
            workout_id=inserted.id,
            source=incoming.source,
            observations=incoming.heart_rate_observations,
            import_job_id=import_job_id,
        )
        await self._session.flush()
        main_detail(inserted)
        return inserted, "inserted"

    async def _refresh_exact(
        self,
        *,
        user_id: uuid.UUID,
        workout: Workout,
        incoming: ActivityImportData,
        details: WorkoutDetailsData,
        file_sha256: str | None,
        import_job_id: uuid.UUID | None,
    ) -> tuple[Workout, ActivityUpsertOutcome]:
        external_id = cast(str, incoming.external_id)
        if workout.source is not incoming.source or workout.external_id != external_id:
            raise ActivitySourceConflictError(
                "Exact source identity points to a legacy cross-source workout"
            )

        workout_changed, fitness_input_changed = _assign_workout_values(
            workout,
            discipline=incoming.discipline,
            started_at=as_utc(incoming.started_at),
            duration_seconds=incoming.duration_seconds,
            title=incoming.title,
            notes=incoming.notes,
        )
        detail_changed = apply_exact_detail(workout, details)
        changed = workout_changed or detail_changed
        _link, link_changed = await self._source_links.ensure(
            user_id=user_id,
            workout=workout,
            incoming=incoming,
            file_sha256=file_sha256,
            import_job_id=import_job_id,
        )
        changed = changed or link_changed
        heart_rate_changed = await self._heart_rate.synchronize(
            user_id=user_id,
            workout_id=workout.id,
            source=incoming.source,
            observations=incoming.heart_rate_observations,
            import_job_id=import_job_id,
        )
        changed = heart_rate_changed or changed
        if fitness_input_changed or detail_changed:
            workout.fitness_input_updated_at = utc_now()
        await self._source_links.attach_import_job(
            user_id=user_id,
            import_job_id=import_job_id,
            workout=workout,
        )
        await self._session.flush()
        main_detail(workout)
        return workout, "updated" if changed else "unchanged"


def _assign_workout_values(
    workout: Workout,
    *,
    discipline: object,
    started_at: datetime,
    duration_seconds: int,
    title: str | None,
    notes: str | None,
) -> tuple[bool, bool]:
    changed = False
    fitness_input_changed = False
    for name, value in {
        "discipline": discipline,
        "started_at": started_at,
        "duration_seconds": duration_seconds,
        "title": title,
        "notes": notes,
    }.items():
        current = getattr(workout, name)
        same_value = (
            as_utc(current) == as_utc(value)
            if name == "started_at"
            and isinstance(current, datetime)
            and isinstance(value, datetime)
            else current == value
        )
        if not same_value:
            setattr(workout, name, value)
            changed = True
            if name in {"discipline", "started_at", "duration_seconds"}:
                fitness_input_changed = True
    return changed, fitness_input_changed


__all__ = [
    "ActivityImportData",
    "ActivityImportValidationError",
    "ActivitySourceConflictError",
    "ActivityUpsertOutcome",
    "TrainingActivityRepository",
]
