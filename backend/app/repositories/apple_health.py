"""Ownership-scoped persistence for Apple Health imports."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utc_now
from app.db.models import (
    Activity,
    AppleHealthImportJob,
    HeartRateObservation,
    OnboardingSession,
)
from app.domain.enums import (
    ActivitySource,
    AppleHealthImportStatus,
    OnboardingStep,
)
from app.integrations.apple_health.models import ParsedWorkout
from app.repositories.errors import OwnedRecordNotFoundError

ActivityUpsertOutcome = Literal["inserted", "updated", "unchanged"]


class AppleHealthRepository:
    """Persist import jobs and normalized records without taking transactions."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_received_job(
        self,
        *,
        user_id: uuid.UUID,
        onboarding_session_id: uuid.UUID,
        telegram_update_id: int | None,
        telegram_file_id: str,
        telegram_file_unique_id: str,
        display_filename: str,
    ) -> tuple[AppleHealthImportJob, bool]:
        if telegram_update_id is not None:
            existing = await self._session.scalar(
                select(AppleHealthImportJob).where(
                    AppleHealthImportJob.user_id == user_id,
                    AppleHealthImportJob.telegram_update_id == telegram_update_id,
                )
            )
            if existing is not None:
                return existing, False
        active = await self.get_active_job(user_id=user_id)
        if active is not None:
            if active.telegram_file_unique_id == telegram_file_unique_id:
                return active, False
            raise AppleHealthImportConflictError("import_already_active")
        job = AppleHealthImportJob(
            user_id=user_id,
            onboarding_session_id=onboarding_session_id,
            telegram_update_id=telegram_update_id,
            telegram_file_id=telegram_file_id,
            telegram_file_unique_id=telegram_file_unique_id,
            display_filename=display_filename[:255],
        )
        try:
            async with self._session.begin_nested():
                self._session.add(job)
                await self._session.flush()
        except IntegrityError as exc:
            active = await self.get_active_job(user_id=user_id)
            if active is not None:
                return active, False
            raise AppleHealthImportConflictError("import_already_active") from exc
        return job, True

    async def get_job(
        self,
        *,
        user_id: uuid.UUID,
        job_id: uuid.UUID,
        for_update: bool = False,
    ) -> AppleHealthImportJob | None:
        statement = select(AppleHealthImportJob).where(
            AppleHealthImportJob.user_id == user_id,
            AppleHealthImportJob.id == job_id,
        )
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def get_job_by_update(
        self,
        *,
        user_id: uuid.UUID,
        telegram_update_id: int,
    ) -> AppleHealthImportJob | None:
        result = await self._session.execute(
            select(AppleHealthImportJob).where(
                AppleHealthImportJob.user_id == user_id,
                AppleHealthImportJob.telegram_update_id == telegram_update_id,
            )
        )
        return result.scalar_one_or_none()

    async def require_job(
        self,
        *,
        user_id: uuid.UUID,
        job_id: uuid.UUID,
        for_update: bool = False,
    ) -> AppleHealthImportJob:
        job = await self.get_job(
            user_id=user_id,
            job_id=job_id,
            for_update=for_update,
        )
        if job is None:
            raise OwnedRecordNotFoundError("Apple Health import job not found")
        return job

    async def get_active_job(
        self,
        *,
        user_id: uuid.UUID,
    ) -> AppleHealthImportJob | None:
        result = await self._session.execute(
            select(AppleHealthImportJob)
            .where(
                AppleHealthImportJob.user_id == user_id,
                AppleHealthImportJob.status.in_(
                    (
                        AppleHealthImportStatus.RECEIVED,
                        AppleHealthImportStatus.PROCESSING,
                    )
                ),
            )
            .order_by(AppleHealthImportJob.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_latest_job(
        self,
        *,
        user_id: uuid.UUID,
    ) -> AppleHealthImportJob | None:
        result = await self._session.execute(
            select(AppleHealthImportJob)
            .where(AppleHealthImportJob.user_id == user_id)
            .order_by(AppleHealthImportJob.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def discipline_counts(
        self,
        *,
        user_id: uuid.UUID,
    ) -> dict[str, int]:
        rows = await self._session.execute(
            select(Activity.sport, func.count(Activity.id))
            .where(
                Activity.user_id == user_id,
                Activity.source == ActivitySource.APPLE_HEALTH,
                Activity.deleted_at.is_(None),
            )
            .group_by(Activity.sport)
        )
        return {discipline.value: count for discipline, count in rows.all()}

    async def get_successful_by_hash(
        self,
        *,
        user_id: uuid.UUID,
        file_sha256: str,
        excluding_job_id: uuid.UUID,
    ) -> AppleHealthImportJob | None:
        result = await self._session.execute(
            select(AppleHealthImportJob)
            .where(
                AppleHealthImportJob.user_id == user_id,
                AppleHealthImportJob.file_sha256 == file_sha256,
                AppleHealthImportJob.status == AppleHealthImportStatus.SUCCEEDED,
                AppleHealthImportJob.id != excluding_job_id,
            )
            .order_by(AppleHealthImportJob.completed_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def mark_processing(
        self,
        *,
        user_id: uuid.UUID,
        job_id: uuid.UUID,
        file_sha256: str,
    ) -> AppleHealthImportJob:
        job = await self.require_job(
            user_id=user_id,
            job_id=job_id,
            for_update=True,
        )
        if job.status is AppleHealthImportStatus.RECEIVED:
            job.status = AppleHealthImportStatus.PROCESSING
            job.started_at = utc_now()
        job.file_sha256 = file_sha256
        await self._session.flush()
        return job

    async def mark_succeeded(
        self,
        *,
        user_id: uuid.UUID,
        job_id: uuid.UUID,
        workouts_found: int,
        activities_imported: int,
        activities_updated: int,
        activities_skipped: int,
        heart_rate_records_matched: int,
        warning_count: int,
    ) -> AppleHealthImportJob:
        job = await self.require_job(
            user_id=user_id,
            job_id=job_id,
            for_update=True,
        )
        job.status = AppleHealthImportStatus.SUCCEEDED
        job.completed_at = utc_now()
        job.workouts_found = workouts_found
        job.activities_imported = activities_imported
        job.activities_updated = activities_updated
        job.activities_skipped = activities_skipped
        job.heart_rate_records_matched = heart_rate_records_matched
        job.warning_count = warning_count
        job.safe_error_code = None
        await self._session.flush()
        return job

    async def copy_success(
        self,
        *,
        user_id: uuid.UUID,
        job_id: uuid.UUID,
        source: AppleHealthImportJob,
    ) -> AppleHealthImportJob:
        return await self.mark_succeeded(
            user_id=user_id,
            job_id=job_id,
            workouts_found=source.workouts_found,
            activities_imported=0,
            activities_updated=0,
            activities_skipped=source.workouts_found,
            heart_rate_records_matched=source.heart_rate_records_matched,
            warning_count=source.warning_count,
        )

    async def mark_failed(
        self,
        *,
        user_id: uuid.UUID,
        job_id: uuid.UUID,
        safe_error_code: str,
    ) -> AppleHealthImportJob:
        job = await self.require_job(
            user_id=user_id,
            job_id=job_id,
            for_update=True,
        )
        if job.status in {
            AppleHealthImportStatus.RECEIVED,
            AppleHealthImportStatus.PROCESSING,
        }:
            job.status = AppleHealthImportStatus.FAILED
            job.completed_at = utc_now()
            job.safe_error_code = safe_error_code[:64]
            await self._session.flush()
        return job

    async def cancel_active(self, *, user_id: uuid.UUID) -> bool:
        result = await self._session.execute(
            update(AppleHealthImportJob)
            .where(
                AppleHealthImportJob.user_id == user_id,
                AppleHealthImportJob.status.in_(
                    (
                        AppleHealthImportStatus.RECEIVED,
                        AppleHealthImportStatus.PROCESSING,
                    )
                ),
            )
            .values(
                status=AppleHealthImportStatus.CANCELLED,
                completed_at=utc_now(),
                safe_error_code=None,
            )
            .returning(AppleHealthImportJob.id)
        )
        return result.scalar_one_or_none() is not None

    async def recover_abandoned(self, *, stale_before: datetime) -> int:
        """Fail expired processing leases and restore their onboarding state."""

        jobs = tuple(
            (
                await self._session.scalars(
                    select(AppleHealthImportJob).where(
                        AppleHealthImportJob.status.in_(
                            (
                                AppleHealthImportStatus.RECEIVED,
                                AppleHealthImportStatus.PROCESSING,
                            )
                        ),
                        or_(
                            AppleHealthImportJob.started_at < stale_before,
                            (
                                AppleHealthImportJob.started_at.is_(None)
                                & (AppleHealthImportJob.created_at < stale_before)
                            ),
                        ),
                    )
                )
            ).all()
        )
        for job in jobs:
            job.status = AppleHealthImportStatus.FAILED
            job.completed_at = utc_now()
            job.safe_error_code = "import_interrupted"
            await self._session.execute(
                update(OnboardingSession)
                .where(
                    OnboardingSession.id == job.onboarding_session_id,
                    OnboardingSession.user_id == job.user_id,
                    OnboardingSession.current_step
                    == OnboardingStep.APPLE_HEALTH_PROCESSING,
                )
                .values(current_step=OnboardingStep.APPLE_HEALTH_IMPORT_FAILED)
            )
        await self._session.flush()
        return len(jobs)

    async def upsert_workout(
        self,
        *,
        user_id: uuid.UUID,
        workout: ParsedWorkout,
    ) -> tuple[Activity, ActivityUpsertOutcome]:
        activity = await self._session.scalar(
            select(Activity).where(
                Activity.user_id == user_id,
                Activity.source == ActivitySource.APPLE_HEALTH,
                Activity.external_id == workout.source_record_key,
            )
        )
        values: dict[str, object] = {
            "sport": workout.discipline,
            "source_sport_type": workout.source_workout_type,
            "name": _activity_name(workout),
            "started_at": workout.started_at,
            "ended_at": workout.ended_at,
            "timezone": None,
            "duration_seconds": workout.duration_seconds,
            "moving_time_seconds": None,
            "distance_meters": workout.distance_meters,
            "elevation_gain_meters": None,
            "calories_kcal": workout.calories_kcal,
            "average_heart_rate": workout.average_heart_rate,
            "max_heart_rate": workout.max_heart_rate,
            "heart_rate_sample_count": workout.heart_rate_sample_count,
            "heart_rate_quality": workout.heart_rate_quality,
            "heart_rate_reliable": workout.heart_rate_reliable,
            "average_speed": None,
            "average_watts": None,
            "trainer": False,
            "commute": False,
            "manual": False,
            "raw_summary": None,
            "deleted_at": None,
        }
        if activity is None:
            activity = Activity(
                user_id=user_id,
                source=ActivitySource.APPLE_HEALTH,
                external_id=workout.source_record_key,
                **values,
            )
            try:
                async with self._session.begin_nested():
                    self._session.add(activity)
                    await self._session.flush()
            except IntegrityError:
                activity = await self._session.scalar(
                    select(Activity).where(
                        Activity.user_id == user_id,
                        Activity.source == ActivitySource.APPLE_HEALTH,
                        Activity.external_id == workout.source_record_key,
                    )
                )
                if activity is None:
                    raise
            else:
                await self._upsert_observations(
                    user_id=user_id,
                    activity=activity,
                    workout=workout,
                )
                return activity, "inserted"

        changed = any(
            not _values_equal(getattr(activity, key), value)
            for key, value in values.items()
        )
        if changed:
            for key, value in values.items():
                setattr(activity, key, value)
        observations_changed = await self._upsert_observations(
            user_id=user_id,
            activity=activity,
            workout=workout,
        )
        await self._session.flush()
        return activity, "updated" if changed or observations_changed else "unchanged"

    async def _upsert_observations(
        self,
        *,
        user_id: uuid.UUID,
        activity: Activity,
        workout: ParsedWorkout,
    ) -> bool:
        if not workout.observations:
            return False
        keys = [item.source_record_key for item in workout.observations]
        existing = {
            item.source_record_key: item
            for item in (
                await self._session.scalars(
                    select(HeartRateObservation).where(
                        HeartRateObservation.user_id == user_id,
                        HeartRateObservation.source_record_key.in_(keys),
                    )
                )
            ).all()
        }
        changed = False
        for observation in workout.observations:
            record = existing.get(observation.source_record_key)
            values = {
                "activity_id": activity.id,
                "source_name": observation.source_name,
                "started_at": observation.started_at,
                "ended_at": observation.ended_at,
                "beats_per_minute": observation.beats_per_minute,
                "temporal_quality": observation.temporal_quality,
            }
            if record is None:
                self._session.add(
                    HeartRateObservation(
                        user_id=user_id,
                        source_record_key=observation.source_record_key,
                        **values,
                    )
                )
                changed = True
            elif any(
                not _values_equal(getattr(record, key), value)
                for key, value in values.items()
            ):
                for key, value in values.items():
                    setattr(record, key, value)
                changed = True
        return changed


class AppleHealthImportConflictError(ValueError):
    """Stable concurrency conflict."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _activity_name(workout: ParsedWorkout) -> str:
    labels = {
        "RUN": "Apple Health run",
        "RIDE": "Apple Health ride",
        "SWIM": "Apple Health swim",
        "WALK_HIKE": "Apple Health walk or hike",
        "STRENGTH": "Apple Health strength workout",
        "OTHER": "Apple Health workout",
    }
    return labels[workout.discipline.value]


def _values_equal(persisted: object, incoming: object) -> bool:
    if isinstance(persisted, datetime) and isinstance(incoming, datetime):
        persisted_utc = (
            persisted.replace(tzinfo=UTC)
            if persisted.tzinfo is None
            else persisted.astimezone(UTC)
        )
        incoming_utc = (
            incoming.replace(tzinfo=UTC)
            if incoming.tzinfo is None
            else incoming.astimezone(UTC)
        )
        return persisted_utc == incoming_utc
    return persisted == incoming


__all__ = [
    "AppleHealthImportConflictError",
    "AppleHealthRepository",
]
