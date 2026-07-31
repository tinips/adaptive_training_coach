"""Ownership-scoped persistence for Apple Health imports."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import exists, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utc_now
from app.db.models import (
    ActivitySourceLink,
    AppleHealthImportJob,
    OnboardingSession,
    Workout,
)
from app.domain.enums import (
    ActivitySource,
    AppleHealthImportStatus,
    OnboardingStep,
    TrainingFileFormat,
    TrainingImportContext,
)
from app.integrations.apple_health.models import ParsedWorkout
from app.repositories.activities import TrainingActivityRepository
from app.repositories.errors import OwnedRecordNotFoundError
from app.services.activities.contracts import ActivityUpsertOutcome


class AppleHealthRepository:
    """Persist import jobs and normalized records without taking transactions."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_received_job(
        self,
        *,
        user_id: uuid.UUID,
        onboarding_session_id: uuid.UUID | None = None,
        telegram_update_id: int | None,
        telegram_file_id: str,
        telegram_file_unique_id: str,
        display_filename: str,
        file_format: TrainingFileFormat = TrainingFileFormat.APPLE_HEALTH_ZIP,
        context: TrainingImportContext = TrainingImportContext.ONBOARDING,
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
            file_format=file_format,
            context=context,
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
            select(Workout.discipline, func.count(Workout.id.distinct()))
            .join(
                ActivitySourceLink,
                ActivitySourceLink.workout_id == Workout.id,
            )
            .where(
                Workout.athlete_id == user_id,
                ActivitySourceLink.user_id == user_id,
                ActivitySourceLink.source == ActivitySource.APPLE_HEALTH,
                ActivitySourceLink.deleted_at.is_(None),
            )
            .group_by(Workout.discipline)
        )
        return {discipline.value: count for discipline, count in rows.all()}

    async def all_discipline_counts(
        self,
        *,
        user_id: uuid.UUID,
    ) -> dict[str, int]:
        """Count every owned canonical activity exactly once."""

        active_source_exists = exists(
            select(ActivitySourceLink.id).where(
                ActivitySourceLink.workout_id == Workout.id,
                ActivitySourceLink.user_id == user_id,
                ActivitySourceLink.deleted_at.is_(None),
            )
        )
        rows = await self._session.execute(
            select(Workout.discipline, func.count(Workout.id))
            .where(
                Workout.athlete_id == user_id,
                or_(
                    Workout.source == ActivitySource.MANUAL,
                    active_source_exists,
                ),
            )
            .group_by(Workout.discipline)
        )
        return {discipline.value: count for discipline, count in rows.all()}

    async def onboarding_totals(
        self,
        *,
        user_id: uuid.UUID,
        onboarding_session_id: uuid.UUID,
    ) -> dict[str, int]:
        """Aggregate successful files in one owned onboarding import session."""

        row = (
            await self._session.execute(
                select(
                    func.count(AppleHealthImportJob.id),
                    func.coalesce(func.sum(AppleHealthImportJob.workouts_found), 0),
                    func.coalesce(
                        func.sum(AppleHealthImportJob.activities_imported),
                        0,
                    ),
                    func.coalesce(
                        func.sum(AppleHealthImportJob.activities_updated),
                        0,
                    ),
                    func.coalesce(
                        func.sum(AppleHealthImportJob.activities_skipped),
                        0,
                    ),
                ).where(
                    AppleHealthImportJob.user_id == user_id,
                    AppleHealthImportJob.onboarding_session_id == onboarding_session_id,
                    AppleHealthImportJob.context == TrainingImportContext.ONBOARDING,
                    AppleHealthImportJob.status == AppleHealthImportStatus.SUCCEEDED,
                )
            )
        ).one()
        return {
            "successful_files": int(row[0]),
            "workouts_found": int(row[1]),
            "activities_imported": int(row[2]),
            "activities_updated": int(row[3]),
            "activities_skipped": int(row[4]),
        }

    async def latest_imported_activity(
        self,
        *,
        user_id: uuid.UUID,
        onboarding_session_id: uuid.UUID | None = None,
    ) -> Workout | None:
        """Return the latest canonical activity linked by an owned import job."""

        statement = (
            select(Workout)
            .join(
                ActivitySourceLink,
                ActivitySourceLink.workout_id == Workout.id,
            )
            .join(
                AppleHealthImportJob,
                AppleHealthImportJob.id == ActivitySourceLink.import_job_id,
            )
            .where(
                Workout.athlete_id == user_id,
                ActivitySourceLink.user_id == user_id,
                AppleHealthImportJob.user_id == user_id,
                AppleHealthImportJob.status == AppleHealthImportStatus.SUCCEEDED,
                ActivitySourceLink.deleted_at.is_(None),
            )
        )
        if onboarding_session_id is not None:
            statement = statement.where(
                AppleHealthImportJob.onboarding_session_id == onboarding_session_id,
            )
        result = await self._session.scalars(
            statement.order_by(Workout.started_at.desc(), Workout.id).limit(1)
        )
        return result.first()

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
        file_format: TrainingFileFormat | None = None,
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
        if file_format is not None:
            job.file_format = file_format
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
        activity_id: uuid.UUID | None = None,
        file_format: TrainingFileFormat | None = None,
    ) -> AppleHealthImportJob:
        job = await self.require_job(
            user_id=user_id,
            job_id=job_id,
            for_update=True,
        )
        if job.status not in {
            AppleHealthImportStatus.RECEIVED,
            AppleHealthImportStatus.PROCESSING,
        }:
            return job
        job.status = AppleHealthImportStatus.SUCCEEDED
        job.completed_at = utc_now()
        job.workouts_found = workouts_found
        job.activities_imported = activities_imported
        job.activities_updated = activities_updated
        job.activities_skipped = activities_skipped
        job.heart_rate_records_matched = heart_rate_records_matched
        job.warning_count = warning_count
        job.workout_id = activity_id
        if file_format is not None:
            job.file_format = file_format
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
            activity_id=source.workout_id,
            file_format=source.file_format,
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

    async def set_temporary_path(
        self,
        *,
        user_id: uuid.UUID,
        job_id: uuid.UUID,
        temporary_path: str,
    ) -> None:
        """Associate a generated upload path before personal data is written."""

        if len(temporary_path) > 1024:
            raise ValueError("temporary_path is too long")
        job = await self.require_job(
            user_id=user_id,
            job_id=job_id,
            for_update=True,
        )
        if job.status in {
            AppleHealthImportStatus.RECEIVED,
            AppleHealthImportStatus.PROCESSING,
        }:
            job.temporary_path = temporary_path
            await self._session.flush()

    async def clear_temporary_path(
        self,
        *,
        user_id: uuid.UUID,
        job_id: uuid.UUID,
    ) -> None:
        """Clear one owned path after verified filesystem cleanup."""

        await self._session.execute(
            update(AppleHealthImportJob)
            .where(
                AppleHealthImportJob.user_id == user_id,
                AppleHealthImportJob.id == job_id,
            )
            .values(temporary_path=None)
        )

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

    async def recover_abandoned(
        self,
        *,
        stale_before: datetime | None,
    ) -> int:
        """Fail expired processing leases and restore their onboarding state."""

        jobs = await self._recoverable_jobs(stale_before=stale_before)
        await self._fail_recovered_jobs(jobs)
        return len(jobs)

    async def recover_abandoned_with_temporary_paths(
        self,
        *,
        stale_before: datetime | None,
    ) -> tuple[
        int,
        tuple[tuple[uuid.UUID, uuid.UUID, str], ...],
    ]:
        """Recover jobs and return recorded paths that are safe to clean later."""

        jobs = await self._recoverable_jobs(stale_before=stale_before)
        await self._fail_recovered_jobs(jobs)
        recovered_ids = {job.id for job in jobs}
        path_jobs = tuple(
            (
                await self._session.scalars(
                    select(AppleHealthImportJob).where(
                        AppleHealthImportJob.temporary_path.is_not(None),
                        or_(
                            AppleHealthImportJob.status.not_in(
                                (
                                    AppleHealthImportStatus.RECEIVED,
                                    AppleHealthImportStatus.PROCESSING,
                                )
                            ),
                            AppleHealthImportJob.id.in_(recovered_ids),
                        ),
                    )
                )
            ).all()
        )
        return (
            len(jobs),
            tuple(
                (job.user_id, job.id, job.temporary_path)
                for job in path_jobs
                if job.temporary_path is not None
            ),
        )

    async def _recoverable_jobs(
        self,
        *,
        stale_before: datetime | None,
    ) -> tuple[AppleHealthImportJob, ...]:
        statement = select(AppleHealthImportJob).where(
            AppleHealthImportJob.status.in_(
                (
                    AppleHealthImportStatus.RECEIVED,
                    AppleHealthImportStatus.PROCESSING,
                )
            )
        )
        if stale_before is not None:
            statement = statement.where(
                or_(
                    AppleHealthImportJob.started_at < stale_before,
                    (
                        AppleHealthImportJob.started_at.is_(None)
                        & (AppleHealthImportJob.created_at < stale_before)
                    ),
                )
            )
        return tuple((await self._session.scalars(statement)).all())

    async def _fail_recovered_jobs(
        self,
        jobs: tuple[AppleHealthImportJob, ...],
    ) -> None:
        jobs = tuple(
            job
            for job in jobs
            if job.status
            in {
                AppleHealthImportStatus.RECEIVED,
                AppleHealthImportStatus.PROCESSING,
            }
        )
        for job in jobs:
            job.status = AppleHealthImportStatus.FAILED
            job.completed_at = utc_now()
            job.safe_error_code = "import_interrupted"
            if job.onboarding_session_id is not None:
                await self._session.execute(
                    update(OnboardingSession)
                    .where(
                        OnboardingSession.id == job.onboarding_session_id,
                        OnboardingSession.user_id == job.user_id,
                        OnboardingSession.current_step
                        == OnboardingStep.FILE_IMPORT_PROCESSING,
                    )
                    .values(current_step=OnboardingStep.FILE_IMPORT_WAITING)
                )
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

    async def upsert_workout(
        self,
        *,
        user_id: uuid.UUID,
        workout: ParsedWorkout,
    ) -> tuple[Workout, ActivityUpsertOutcome]:
        return await TrainingActivityRepository(self._session).import_apple_workout(
            user_id=user_id,
            workout=workout,
            file_sha256=None,
            import_job_id=None,
        )


class AppleHealthImportConflictError(ValueError):
    """Stable concurrency conflict."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


__all__ = [
    "AppleHealthImportConflictError",
    "AppleHealthRepository",
]
