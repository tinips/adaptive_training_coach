"""Secure, durable Apple Health ZIP and TCX import orchestration."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import tempfile
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.models import Activity, AppleHealthImportJob
from app.domain.enums import (
    AppleHealthImportStatus,
    BaselinePreferenceStatus,
    BaselineSource,
    BaselineStatus,
    LevelLabel,
    OnboardingStatus,
    OnboardingStep,
    TrainingFileFormat,
    TrainingImportContext,
    UserStatus,
)
from app.integrations.apple_health import (
    AppleHealthArchiveLimits,
    AppleHealthParser,
    AppleHealthParserError,
)
from app.integrations.tcx import TCXParser, TCXParserError, TCXParserLimits
from app.repositories.activities import (
    ActivityImportValidationError,
    ActivitySourceConflictError,
    TrainingActivityRepository,
)
from app.repositories.apple_health import (
    AppleHealthImportConflictError,
    AppleHealthRepository,
)
from app.repositories.baselines import BaselineRepository
from app.repositories.onboarding import OnboardingRepository
from app.repositories.profiles import ProfileRepository
from app.repositories.strava import StravaRepository
from app.repositories.users import UserRepository
from app.schemas.baseline import BaselineCalculation
from app.schemas.common import TelegramIdentity
from app.services.apple_health import TelegramDocumentUpload
from app.services.baseline import BaselineService
from app.services.onboarding import OnboardingApplicationError

logger = logging.getLogger(__name__)

DownloadCallback = Callable[[Path], Awaitable[None]]
ProgressCallback = Callable[[str], Awaitable[None]]

_COMPLETED_PROFILE_STATES = {
    UserStatus.PROFILE_COMPLETED,
    UserStatus.BASELINE_PENDING,
    UserStatus.BASELINE_IMPORTING,
    UserStatus.BASELINE_READY,
    UserStatus.BASELINE_FAILED,
}
_ONBOARDING_IMPORT_STEPS = {
    OnboardingStep.FILE_IMPORT_WAITING,
    OnboardingStep.FILE_IMPORT_PROCESSING,
    # Accept an interrupted pre-unification Apple flow without losing data.
    OnboardingStep.APPLE_HEALTH_WAITING_FOR_FILE,
    OnboardingStep.APPLE_HEALTH_PROCESSING,
}
_ZIP_MAGIC = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")


@dataclass(slots=True, frozen=True)
class TrainingFileImportOutcome:
    """Safe persisted import result used by Telegram rendering and resume."""

    status: AppleHealthImportStatus
    context: TrainingImportContext
    file_format: TrainingFileFormat
    job_id: uuid.UUID | None = None
    activity_id: uuid.UUID | None = None
    workouts_found: int = 0
    activities_imported: int = 0
    activities_updated: int = 0
    activities_skipped: int = 0
    heart_rate_records_matched: int = 0
    warning_count: int = 0
    discipline_counts: dict[str, int] | None = None
    safe_error_code: str | None = None
    exact_file_duplicate: bool = False
    match_kind: str | None = None
    sport: str | None = None
    started_at: datetime | None = None
    duration_seconds: int | None = None
    distance_meters: float | None = None
    average_heart_rate: float | None = None
    heart_rate_reliable: bool = False
    baseline_limited: bool = False


class TrainingFileImportService:
    """Own temp files, format detection, canonical imports, and baseline refresh."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._clock = clock or (lambda: datetime.now(UTC))
        self._apple_parser = AppleHealthParser(
            AppleHealthArchiveLimits(
                max_compressed_bytes=(
                    settings.apple_health_import_max_compressed_size_mb * 1024 * 1024
                ),
                max_uncompressed_bytes=(
                    settings.apple_health_import_max_uncompressed_size_mb * 1024 * 1024
                ),
                max_members=settings.apple_health_import_max_zip_members,
                max_compression_ratio=(
                    settings.apple_health_import_max_compression_ratio
                ),
            )
        )
        self._tcx_parser = TCXParser(
            TCXParserLimits(
                max_bytes=settings.tcx_import_max_size_mb * 1024 * 1024,
            )
        )

    async def process_upload(
        self,
        *,
        identity: TelegramIdentity,
        document: TelegramDocumentUpload,
        download: DownloadCallback,
        progress: ProgressCallback,
    ) -> TrainingFileImportOutcome:
        """Import one owned document and always delete its generated temp file."""

        if not (
            self._settings.apple_health_import_enabled
            or self._settings.tcx_import_enabled
        ):
            raise OnboardingApplicationError("training_file_import_disabled")

        job, user_id, claimed = await self._begin(
            identity=identity,
            document=document,
        )
        if not claimed or job.status not in {
            AppleHealthImportStatus.RECEIVED,
            AppleHealthImportStatus.PROCESSING,
        }:
            return await self._outcome(job, exact_file_duplicate=not claimed)

        temp_path: Path | None = None
        try:
            enabled_limits = [
                *(
                    [self._apple_parser.max_compressed_bytes]
                    if self._settings.apple_health_import_enabled
                    else []
                ),
                *(
                    [self._tcx_parser.max_bytes]
                    if self._settings.tcx_import_enabled
                    else []
                ),
            ]
            maximum_hint = max(enabled_limits)
            if document.file_size is not None and document.file_size > maximum_hint:
                raise TrainingFileImportError("training_file_size_exceeded")
            temp_path = self._create_temp_path()
            await self._record_temporary_path(
                user_id=user_id,
                job_id=job.id,
                path=temp_path,
            )
            await _download_with_limit(
                download,
                temp_path,
                maximum_hint,
            )
            await progress("detecting_format")
            file_sha256, file_format = await asyncio.to_thread(
                _inspect_file,
                temp_path,
                self._tcx_parser.max_bytes,
                self._apple_parser.max_compressed_bytes,
            )
            self._require_enabled(file_format)
            await self._mark_processing(
                user_id=user_id,
                job_id=job.id,
                file_sha256=file_sha256,
                file_format=file_format,
            )

            duplicate = await self._successful_duplicate(
                user_id=user_id,
                job_id=job.id,
                file_sha256=file_sha256,
            )
            if duplicate is not None:
                copied = await self._finish_duplicate(
                    user_id=user_id,
                    job_id=job.id,
                    duplicate=duplicate,
                )
                baseline_limited = False
                if (
                    copied.status is AppleHealthImportStatus.SUCCEEDED
                    and copied.context is TrainingImportContext.DAILY
                ):
                    calculation = await self._recalculate_daily(user_id=user_id)
                    baseline_limited = _baseline_is_limited(calculation)
                result = await self._outcome(
                    copied,
                    exact_file_duplicate=True,
                )
                return replace(result, baseline_limited=baseline_limited)

            if file_format is TrainingFileFormat.APPLE_HEALTH_ZIP:
                return await self._process_apple(
                    user_id=user_id,
                    job_id=job.id,
                    path=temp_path,
                    file_sha256=file_sha256,
                    progress=progress,
                )
            return await self._process_tcx(
                user_id=user_id,
                job_id=job.id,
                path=temp_path,
                file_sha256=file_sha256,
                progress=progress,
            )
        except (
            AppleHealthParserError,
            TCXParserError,
            TrainingFileImportError,
            AppleHealthImportConflictError,
            ActivityImportValidationError,
            ActivitySourceConflictError,
            OnboardingApplicationError,
        ) as exc:
            code = getattr(exc, "code", "training_file_import_failed")
            return await self._fail(user_id=user_id, job_id=job.id, code=code)
        except Exception as exc:
            logger.error(
                "Training file import failed user_id=%s error_type=%s",
                user_id,
                type(exc).__name__,
            )
            return await self._fail(
                user_id=user_id,
                job_id=job.id,
                code="training_file_import_failed",
            )
        finally:
            if temp_path is not None:
                if self._delete_temporary_path(temp_path, user_id=user_id):
                    await self._clear_temporary_path(
                        user_id=user_id,
                        job_id=job.id,
                    )

    async def finish_onboarding_import(
        self,
        *,
        identity: TelegramIdentity,
    ) -> TrainingFileImportOutcome:
        """Finish a non-empty multi-file session and append its baseline once."""

        async with self._session_factory.begin() as session:
            user = await UserRepository(session).get_by_telegram_id(
                identity.telegram_user_id
            )
            if user is None:
                raise OnboardingApplicationError("user_not_found")
            onboarding = await OnboardingRepository(session).lock_for_user(
                user_id=user.id
            )
            if (
                onboarding.status is not OnboardingStatus.ACTIVE
                or onboarding.current_step
                not in {
                    OnboardingStep.FILE_IMPORT_WAITING,
                    OnboardingStep.FILE_IMPORT_COMPLETE,
                }
            ):
                raise OnboardingApplicationError("stale_action")
            repository = AppleHealthRepository(session)
            active = await repository.get_active_job(user_id=user.id)
            if active is not None:
                raise OnboardingApplicationError("import_already_active")
            totals = await repository.onboarding_totals(
                user_id=user.id,
                onboarding_session_id=onboarding.id,
            )
            if totals["workouts_found"] < 1:
                raise OnboardingApplicationError("no_valid_imported_activities")
            calculation: BaselineCalculation | None = None
            if onboarding.current_step is OnboardingStep.FILE_IMPORT_WAITING:
                calculation = await BaselineService(
                    activities=StravaRepository(session),
                    baselines=BaselineRepository(session),
                    analysis_days=self._settings.strava_initial_sync_days,
                    clock=self._clock,
                ).recalculate(
                    user_id=user.id,
                    source=BaselineSource.FILE_IMPORT,
                )
                answers = dict(onboarding.answers)
                answers[OnboardingStep.BASELINE_SOURCE.value.lower()] = (
                    BaselineSource.FILE_IMPORT.value
                )
                onboarding.answers = answers
                onboarding.current_step = OnboardingStep.FILE_IMPORT_COMPLETE
                await session.flush()
            if calculation is None:
                latest_baseline = await BaselineRepository(session).get_latest(
                    user_id=user.id
                )
                baseline_limited = (
                    latest_baseline is None
                    or latest_baseline.status is not BaselineStatus.READY
                    or any(
                        discipline.level_label is LevelLabel.UNKNOWN
                        for discipline in latest_baseline.discipline_baselines
                    )
                )
            else:
                baseline_limited = _baseline_is_limited(calculation)
            counts = await repository.all_discipline_counts(user_id=user.id)
            latest = await repository.latest_imported_activity(
                user_id=user.id,
                onboarding_session_id=onboarding.id,
            )
            return TrainingFileImportOutcome(
                status=AppleHealthImportStatus.SUCCEEDED,
                context=TrainingImportContext.ONBOARDING,
                file_format=TrainingFileFormat.UNKNOWN,
                activity_id=latest.id if latest is not None else None,
                workouts_found=totals["workouts_found"],
                activities_imported=totals["activities_imported"],
                activities_updated=totals["activities_updated"],
                activities_skipped=totals["activities_skipped"],
                discipline_counts=counts,
                baseline_limited=baseline_limited,
            )

    async def latest_outcome(
        self,
        *,
        user_id: uuid.UUID,
    ) -> TrainingFileImportOutcome | None:
        async with self._session_factory() as session:
            job = await AppleHealthRepository(session).get_latest_job(user_id=user_id)
            if job is None:
                return None
            return await self._outcome_in_session(session, job)

    async def latest_onboarding_activity(
        self,
        *,
        user_id: uuid.UUID,
    ) -> Activity | None:
        async with self._session_factory() as session:
            onboarding = await OnboardingRepository(session).get_for_user(
                user_id=user_id
            )
            if onboarding is None:
                return None
            return await AppleHealthRepository(session).latest_imported_activity(
                user_id=user_id,
                onboarding_session_id=onboarding.id,
            )

    async def cancel_active(self, *, user_id: uuid.UUID) -> None:
        async with self._session_factory.begin() as session:
            cancelled = await AppleHealthRepository(session).cancel_active(
                user_id=user_id
            )
            if cancelled:
                onboarding = await OnboardingRepository(session).lock_for_user(
                    user_id=user_id
                )
                if (
                    onboarding.status is OnboardingStatus.ACTIVE
                    and onboarding.current_step is OnboardingStep.FILE_IMPORT_PROCESSING
                ):
                    onboarding.current_step = OnboardingStep.FILE_IMPORT_WAITING
                    await session.flush()

    async def recover_stale_work(self) -> int:
        """Fail all prior-process active jobs and restore onboarding to waiting."""

        async with self._session_factory.begin() as session:
            count, recorded_paths = await AppleHealthRepository(
                session
            ).recover_abandoned_with_temporary_paths(
                stale_before=None,
            )
        for user_id, job_id, raw_path in recorded_paths:
            if self._delete_temporary_path(Path(raw_path), user_id=user_id):
                await self._clear_temporary_path(
                    user_id=user_id,
                    job_id=job_id,
                )
        return count

    async def _begin(
        self,
        *,
        identity: TelegramIdentity,
        document: TelegramDocumentUpload,
    ) -> tuple[AppleHealthImportJob, uuid.UUID, bool]:
        async with self._session_factory.begin() as session:
            user = await UserRepository(session).get_by_telegram_id(
                identity.telegram_user_id
            )
            if user is None:
                raise OnboardingApplicationError("user_not_found")
            onboarding = await OnboardingRepository(session).lock_for_user(
                user_id=user.id
            )
            repository = AppleHealthRepository(session)
            if document.update_id is not None:
                replay = await repository.get_job_by_update(
                    user_id=user.id,
                    telegram_update_id=document.update_id,
                )
                if replay is not None:
                    return replay, user.id, False

            onboarding_session_id: uuid.UUID | None = None
            if (
                onboarding.status is OnboardingStatus.ACTIVE
                and onboarding.current_step in _ONBOARDING_IMPORT_STEPS
            ):
                context = TrainingImportContext.ONBOARDING
                onboarding_session_id = onboarding.id
            elif (
                onboarding.status is OnboardingStatus.COMPLETED
                and user.status in _COMPLETED_PROFILE_STATES
            ):
                context = TrainingImportContext.DAILY
            else:
                raise OnboardingApplicationError("training_file_not_expected")

            job, created = await repository.create_received_job(
                user_id=user.id,
                onboarding_session_id=onboarding_session_id,
                telegram_update_id=document.update_id,
                telegram_file_id=document.file_id,
                telegram_file_unique_id=document.file_unique_id,
                display_filename=document.display_filename,
                file_format=TrainingFileFormat.UNKNOWN,
                context=context,
            )
            if (
                created
                and context is TrainingImportContext.ONBOARDING
                and job.status
                in {
                    AppleHealthImportStatus.RECEIVED,
                    AppleHealthImportStatus.PROCESSING,
                }
            ):
                onboarding.current_step = OnboardingStep.FILE_IMPORT_PROCESSING
                await session.flush()
            return job, user.id, created

    async def _process_apple(
        self,
        *,
        user_id: uuid.UUID,
        job_id: uuid.UUID,
        path: Path,
        file_sha256: str,
        progress: ProgressCallback,
    ) -> TrainingFileImportOutcome:
        await progress("validating_archive")
        member = await asyncio.to_thread(self._apple_parser.validate, path)
        await progress("reading_workouts")
        workouts, found, duplicates, warnings = await asyncio.to_thread(
            self._apple_parser.read_workouts,
            path,
            member,
        )
        await progress("reading_heart_rate")
        matched = await asyncio.to_thread(
            self._apple_parser.read_heart_rate,
            path,
            member,
            workouts,
        )
        await progress("matching_data")
        await progress("saving_activities")
        async with self._session_factory.begin() as session:
            jobs = AppleHealthRepository(session)
            job = await jobs.require_job(
                user_id=user_id,
                job_id=job_id,
                for_update=True,
            )
            if job.status not in {
                AppleHealthImportStatus.RECEIVED,
                AppleHealthImportStatus.PROCESSING,
            }:
                await self._restore_onboarding_waiting(session, job)
                return await self._outcome_in_session(session, job)
            activities = TrainingActivityRepository(session)
            imported = 0
            updated = 0
            unchanged = 0
            latest: Activity | None = None
            for workout in workouts:
                activity, outcome = await activities.import_apple_workout(
                    user_id=user_id,
                    workout=workout,
                    file_sha256=file_sha256,
                    import_job_id=job.id,
                )
                if latest is None or activity.started_at > latest.started_at:
                    latest = activity
                if outcome == "inserted":
                    imported += 1
                elif outcome == "updated":
                    updated += 1
                else:
                    unchanged += 1
            calculation: BaselineCalculation | None = None
            if job.context is TrainingImportContext.DAILY:
                await progress("recalculating_baseline")
                calculation = await self._recalculate_in_session(
                    session,
                    user_id=user_id,
                )
            completed = await jobs.mark_succeeded(
                user_id=user_id,
                job_id=job.id,
                workouts_found=found,
                activities_imported=imported,
                activities_updated=updated,
                activities_skipped=unchanged + duplicates,
                heart_rate_records_matched=matched,
                warning_count=len(warnings),
                activity_id=latest.id if len(workouts) == 1 and latest else None,
                file_format=TrainingFileFormat.APPLE_HEALTH_ZIP,
            )
            await self._restore_onboarding_waiting(session, completed)
            result = await self._outcome_in_session(session, completed)
            return replace(
                result,
                baseline_limited=(
                    _baseline_is_limited(calculation)
                    if calculation is not None
                    else False
                ),
            )

    async def _process_tcx(
        self,
        *,
        user_id: uuid.UUID,
        job_id: uuid.UUID,
        path: Path,
        file_sha256: str,
        progress: ProgressCallback,
    ) -> TrainingFileImportOutcome:
        await progress("reading_tcx")
        parsed = await asyncio.to_thread(self._tcx_parser.parse, path)
        await progress("matching_data")
        await progress("saving_activities")
        async with self._session_factory.begin() as session:
            jobs = AppleHealthRepository(session)
            job = await jobs.require_job(
                user_id=user_id,
                job_id=job_id,
                for_update=True,
            )
            if job.status not in {
                AppleHealthImportStatus.RECEIVED,
                AppleHealthImportStatus.PROCESSING,
            }:
                await self._restore_onboarding_waiting(session, job)
                return await self._outcome_in_session(session, job)
            activity, outcome, match_kind = await TrainingActivityRepository(
                session
            ).import_tcx_activity(
                user_id=user_id,
                parsed=parsed,
                file_sha256=file_sha256,
                import_job_id=job.id,
            )
            calculation: BaselineCalculation | None = None
            if job.context is TrainingImportContext.DAILY:
                await progress("recalculating_baseline")
                calculation = await self._recalculate_in_session(
                    session,
                    user_id=user_id,
                )
            completed = await jobs.mark_succeeded(
                user_id=user_id,
                job_id=job.id,
                workouts_found=1,
                activities_imported=1 if outcome == "inserted" else 0,
                activities_updated=1 if outcome == "updated" else 0,
                activities_skipped=1 if outcome == "unchanged" else 0,
                heart_rate_records_matched=parsed.heart_rate_sample_count,
                warning_count=len(parsed.warnings),
                activity_id=activity.id,
                file_format=TrainingFileFormat.TCX,
            )
            await self._restore_onboarding_waiting(session, completed)
            result = await self._outcome_in_session(session, completed)
            return replace(
                result,
                match_kind=match_kind,
                baseline_limited=(
                    _baseline_is_limited(calculation)
                    if calculation is not None
                    else False
                ),
            )

    async def _mark_processing(
        self,
        *,
        user_id: uuid.UUID,
        job_id: uuid.UUID,
        file_sha256: str,
        file_format: TrainingFileFormat,
    ) -> None:
        async with self._session_factory.begin() as session:
            await AppleHealthRepository(session).mark_processing(
                user_id=user_id,
                job_id=job_id,
                file_sha256=file_sha256,
                file_format=file_format,
            )

    async def _successful_duplicate(
        self,
        *,
        user_id: uuid.UUID,
        job_id: uuid.UUID,
        file_sha256: str,
    ) -> AppleHealthImportJob | None:
        async with self._session_factory() as session:
            return await AppleHealthRepository(session).get_successful_by_hash(
                user_id=user_id,
                file_sha256=file_sha256,
                excluding_job_id=job_id,
            )

    async def _finish_duplicate(
        self,
        *,
        user_id: uuid.UUID,
        job_id: uuid.UUID,
        duplicate: AppleHealthImportJob,
    ) -> AppleHealthImportJob:
        async with self._session_factory.begin() as session:
            repository = AppleHealthRepository(session)
            copied = await repository.copy_success(
                user_id=user_id,
                job_id=job_id,
                source=duplicate,
            )
            await self._restore_onboarding_waiting(session, copied)
            return copied

    async def _fail(
        self,
        *,
        user_id: uuid.UUID,
        job_id: uuid.UUID,
        code: str,
    ) -> TrainingFileImportOutcome:
        async with self._session_factory.begin() as session:
            repository = AppleHealthRepository(session)
            job = await repository.mark_failed(
                user_id=user_id,
                job_id=job_id,
                safe_error_code=code,
            )
            await self._restore_onboarding_waiting(session, job)
            return await self._outcome_in_session(session, job)

    async def _restore_onboarding_waiting(
        self,
        session: AsyncSession,
        job: AppleHealthImportJob,
    ) -> None:
        if (
            job.context is not TrainingImportContext.ONBOARDING
            or job.onboarding_session_id is None
        ):
            return
        onboarding = await OnboardingRepository(session).lock_for_user(
            user_id=job.user_id
        )
        if (
            onboarding.status is OnboardingStatus.ACTIVE
            and onboarding.id == job.onboarding_session_id
            and onboarding.current_step is OnboardingStep.FILE_IMPORT_PROCESSING
        ):
            onboarding.current_step = OnboardingStep.FILE_IMPORT_WAITING
            await session.flush()

    async def _recalculate_daily(
        self,
        *,
        user_id: uuid.UUID,
    ) -> BaselineCalculation:
        async with self._session_factory.begin() as session:
            return await self._recalculate_in_session(session, user_id=user_id)

    async def _recalculate_in_session(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
    ) -> BaselineCalculation:
        bundle = await ProfileRepository(session).get_bundle(user_id=user_id)
        source = (
            bundle.baseline_preference.selected_source
            if bundle.baseline_preference is not None
            else BaselineSource.FILE_IMPORT
        )
        calculation = await BaselineService(
            activities=StravaRepository(session),
            baselines=BaselineRepository(session),
            analysis_days=self._settings.strava_initial_sync_days,
            clock=self._clock,
        ).recalculate(
            user_id=user_id,
            source=source,
        )
        if bundle.baseline_preference is None:
            await ProfileRepository(session).upsert_baseline_preference(
                user_id=user_id,
                selected_source=BaselineSource.FILE_IMPORT,
                status=BaselinePreferenceStatus.READY,
            )
            await UserRepository(session).update_status(
                user_id=user_id,
                status=UserStatus.BASELINE_READY,
            )
        return calculation

    async def _record_temporary_path(
        self,
        *,
        user_id: uuid.UUID,
        job_id: uuid.UUID,
        path: Path,
    ) -> None:
        async with self._session_factory.begin() as session:
            await AppleHealthRepository(session).set_temporary_path(
                user_id=user_id,
                job_id=job_id,
                temporary_path=str(path),
            )

    async def _clear_temporary_path(
        self,
        *,
        user_id: uuid.UUID,
        job_id: uuid.UUID,
    ) -> None:
        try:
            async with self._session_factory.begin() as session:
                await AppleHealthRepository(session).clear_temporary_path(
                    user_id=user_id,
                    job_id=job_id,
                )
        except Exception as exc:
            logger.warning(
                "Training import cleanup metadata update failed "
                "user_id=%s error_type=%s",
                user_id,
                type(exc).__name__,
            )

    async def _outcome(
        self,
        job: AppleHealthImportJob,
        *,
        exact_file_duplicate: bool = False,
    ) -> TrainingFileImportOutcome:
        async with self._session_factory() as session:
            return await self._outcome_in_session(
                session,
                job,
                exact_file_duplicate=exact_file_duplicate,
            )

    async def _outcome_in_session(
        self,
        session: AsyncSession,
        job: AppleHealthImportJob,
        *,
        exact_file_duplicate: bool = False,
    ) -> TrainingFileImportOutcome:
        counts = await AppleHealthRepository(session).all_discipline_counts(
            user_id=job.user_id
        )
        activity: Activity | None = None
        if job.activity_id is not None:
            activity = await session.scalar(
                select(Activity).where(
                    Activity.user_id == job.user_id,
                    Activity.id == job.activity_id,
                )
            )
        return TrainingFileImportOutcome(
            status=job.status,
            context=job.context,
            file_format=job.file_format,
            job_id=job.id,
            activity_id=activity.id if activity is not None else job.activity_id,
            workouts_found=job.workouts_found,
            activities_imported=job.activities_imported,
            activities_updated=job.activities_updated,
            activities_skipped=job.activities_skipped,
            heart_rate_records_matched=job.heart_rate_records_matched,
            warning_count=job.warning_count,
            discipline_counts=counts,
            safe_error_code=job.safe_error_code,
            exact_file_duplicate=exact_file_duplicate,
            sport=activity.sport.value if activity is not None else None,
            started_at=activity.started_at if activity is not None else None,
            duration_seconds=(
                activity.duration_seconds if activity is not None else None
            ),
            distance_meters=(
                activity.distance_meters if activity is not None else None
            ),
            average_heart_rate=(
                activity.average_heart_rate if activity is not None else None
            ),
            heart_rate_reliable=(
                activity.heart_rate_reliable if activity is not None else False
            ),
        )

    def _require_enabled(self, file_format: TrainingFileFormat) -> None:
        if (
            file_format is TrainingFileFormat.APPLE_HEALTH_ZIP
            and not self._settings.apple_health_import_enabled
        ):
            raise OnboardingApplicationError("apple_health_import_disabled")
        if (
            file_format is TrainingFileFormat.TCX
            and not self._settings.tcx_import_enabled
        ):
            raise OnboardingApplicationError("tcx_import_disabled")

    def _create_temp_path(self) -> Path:
        configured = self._settings.apple_health_import_temp_dir
        if configured is not None:
            configured.mkdir(parents=True, exist_ok=True)
        descriptor, raw_path = tempfile.mkstemp(
            prefix="training-import-",
            suffix=".upload",
            dir=configured,
        )
        os.close(descriptor)
        return Path(raw_path)

    def _delete_temporary_path(
        self,
        path: Path,
        *,
        user_id: uuid.UUID,
    ) -> bool:
        if not self._is_generated_temporary_path(path):
            logger.warning(
                "Training import refused unexpected temporary path user_id=%s",
                user_id,
            )
            return False
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.warning(
                "Training import temporary file cleanup failed user_id=%s",
                user_id,
            )
            return False
        return True

    def _is_generated_temporary_path(self, path: Path) -> bool:
        expected_parent = (
            self._settings.apple_health_import_temp_dir or Path(tempfile.gettempdir())
        ).resolve()
        resolved = path.resolve(strict=False)
        return (
            resolved.parent == expected_parent
            and resolved.name.startswith("training-import-")
            and resolved.name.endswith(".upload")
        )

    def _aware_now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("The training import clock must be aware.")
        return now.astimezone(UTC)


class TrainingFileImportError(ValueError):
    """Stable, content-safe unified import error."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _inspect_file(
    path: Path,
    tcx_max_bytes: int,
    zip_max_bytes: int,
) -> tuple[str, TrainingFileFormat]:
    size = path.stat().st_size
    with path.open("rb") as stream:
        prefix = stream.read(4096)
    if prefix.startswith(_ZIP_MAGIC):
        if size > zip_max_bytes:
            raise TrainingFileImportError("archive_compressed_size_exceeded")
        file_format = TrainingFileFormat.APPLE_HEALTH_ZIP
    else:
        if size > tcx_max_bytes:
            raise TrainingFileImportError("tcx_size_exceeded")
        xml_prefix = prefix.lstrip(b"\xef\xbb\xbf \t\r\n")
        if not xml_prefix.startswith(b"<"):
            raise TrainingFileImportError("unsupported_training_file")
        file_format = TrainingFileFormat.TCX
    return _sha256_file(path), file_format


async def _download_with_limit(
    download: DownloadCallback,
    path: Path,
    max_bytes: int,
) -> None:
    """Monitor an async Telegram download and stop it once the limit is crossed."""

    task: asyncio.Future[None] = asyncio.ensure_future(download(path))
    try:
        while not task.done():
            await asyncio.wait({task}, timeout=0.05)
            if (await asyncio.to_thread(_file_size, path)) > max_bytes:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                raise TrainingFileImportError("training_file_size_exceeded")
        await task
        if (await asyncio.to_thread(_file_size, path)) > max_bytes:
            raise TrainingFileImportError("training_file_size_exceeded")
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


def _baseline_is_limited(calculation: BaselineCalculation) -> bool:
    return calculation.status is not BaselineStatus.READY or any(
        discipline.level_label is LevelLabel.UNKNOWN
        for discipline in calculation.disciplines
    )


def _file_size(path: Path) -> int:
    return path.stat().st_size


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
