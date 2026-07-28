"""Orchestrate secure Telegram Apple Health ZIP imports."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import tempfile
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.models import AppleHealthImportJob
from app.domain.enums import (
    AppleHealthImportStatus,
    BaselineSource,
    OnboardingStatus,
    OnboardingStep,
)
from app.integrations.apple_health import (
    AppleHealthArchiveLimits,
    AppleHealthParser,
    AppleHealthParserError,
)
from app.integrations.apple_health.models import ParsedWorkout
from app.repositories.apple_health import (
    AppleHealthImportConflictError,
    AppleHealthRepository,
)
from app.repositories.baselines import BaselineRepository
from app.repositories.onboarding import OnboardingRepository
from app.repositories.strava import StravaRepository
from app.repositories.users import UserRepository
from app.schemas.common import TelegramIdentity
from app.services.baseline import BaselineService
from app.services.onboarding import OnboardingApplicationError

logger = logging.getLogger(__name__)

DownloadCallback = Callable[[Path], Awaitable[None]]
ProgressCallback = Callable[[str], Awaitable[None]]


@dataclass(slots=True, frozen=True)
class TelegramDocumentUpload:
    """Safe Telegram metadata; the original filename is display-only."""

    file_id: str
    file_unique_id: str
    display_filename: str
    file_size: int | None
    update_id: int | None


@dataclass(slots=True, frozen=True)
class AppleHealthImportOutcome:
    """Safe import result used by Telegram rendering and resume."""

    status: AppleHealthImportStatus
    workouts_found: int = 0
    activities_imported: int = 0
    activities_updated: int = 0
    activities_skipped: int = 0
    heart_rate_records_matched: int = 0
    warning_count: int = 0
    discipline_counts: dict[str, int] | None = None
    safe_error_code: str | None = None


class AppleHealthImportService:
    """Own temporary files, parsing, atomic persistence, and baseline refresh."""

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
        self._parser = AppleHealthParser(
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

    async def process_upload(
        self,
        *,
        identity: TelegramIdentity,
        document: TelegramDocumentUpload,
        download: DownloadCallback,
        progress: ProgressCallback,
    ) -> AppleHealthImportOutcome:
        """Download to a generated path and process only in the waiting state."""

        if not self._settings.apple_health_import_enabled:
            raise OnboardingApplicationError("apple_health_import_disabled")

        job, user_id, claimed = await self._begin(
            identity=identity,
            document=document,
        )
        if not claimed:
            return await self._outcome(job)
        if job.status is AppleHealthImportStatus.SUCCEEDED:
            return await self._outcome(job)
        if job.status is AppleHealthImportStatus.FAILED:
            return await self._outcome(job)

        temp_path: Path | None = None
        try:
            if (
                document.file_size is not None
                and document.file_size > self._parser.max_compressed_bytes
            ):
                raise AppleHealthParserError("archive_compressed_size_exceeded")
            temp_path = self._create_temp_path()
            await download(temp_path)
            await progress("validating_archive")
            file_sha256 = await asyncio.to_thread(_sha256_file, temp_path)
            await self._mark_processing(
                user_id=user_id,
                job_id=job.id,
                file_sha256=file_sha256,
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
                return await self._outcome(copied)

            member = await asyncio.to_thread(self._parser.validate, temp_path)
            await progress("reading_workouts")
            workouts, found, duplicates, warnings = await asyncio.to_thread(
                self._parser.read_workouts,
                temp_path,
                member,
            )
            await progress("reading_heart_rate")
            matched = await asyncio.to_thread(
                self._parser.read_heart_rate,
                temp_path,
                member,
                workouts,
            )
            await progress("matching_data")
            await progress("saving_activities")
            completed = await self._complete(
                user_id=user_id,
                job_id=job.id,
                workouts=workouts,
                workouts_found=found,
                duplicate_workouts=duplicates,
                heart_rate_records_matched=matched,
                warning_count=len(warnings),
                progress=progress,
            )
            return await self._outcome(completed)
        except (
            AppleHealthParserError,
            AppleHealthImportConflictError,
            OnboardingApplicationError,
        ) as exc:
            code = getattr(exc, "code", "apple_health_import_failed")
            return await self._fail(user_id=user_id, job_id=job.id, code=code)
        except Exception as exc:
            logger.error(
                "Apple Health import failed user_id=%s error_type=%s",
                user_id,
                type(exc).__name__,
            )
            return await self._fail(
                user_id=user_id,
                job_id=job.id,
                code="apple_health_import_failed",
            )
        finally:
            if (
                temp_path is not None
                and not self._settings.apple_health_import_keep_original_files
            ):
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    logger.warning(
                        "Apple Health temporary file cleanup failed user_id=%s",
                        user_id,
                    )

    async def latest_outcome(
        self,
        *,
        user_id: uuid.UUID,
    ) -> AppleHealthImportOutcome | None:
        async with self._session_factory() as session:
            repository = AppleHealthRepository(session)
            job = await repository.get_latest_job(user_id=user_id)
            if job is None:
                return None
            counts = await repository.discipline_counts(user_id=user_id)
            return _job_outcome(job, counts)

    async def cancel_active(self, *, user_id: uuid.UUID) -> None:
        async with self._session_factory.begin() as session:
            await AppleHealthRepository(session).cancel_active(user_id=user_id)

    async def recover_stale_work(self) -> int:
        """Mark work abandoned by a prior process as safely retryable."""

        now = self._aware_now()
        async with self._session_factory.begin() as session:
            return await AppleHealthRepository(session).recover_abandoned(
                stale_before=now - timedelta(minutes=30)
            )

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
            if document.update_id is not None:
                replay = await AppleHealthRepository(session).get_job_by_update(
                    user_id=user.id,
                    telegram_update_id=document.update_id,
                )
                if replay is not None:
                    return replay, user.id, False
            if (
                onboarding.status is not OnboardingStatus.ACTIVE
                or onboarding.current_step
                not in {
                    OnboardingStep.APPLE_HEALTH_WAITING_FOR_FILE,
                    OnboardingStep.APPLE_HEALTH_PROCESSING,
                }
            ):
                raise OnboardingApplicationError("apple_health_file_not_expected")
            job, created = await AppleHealthRepository(session).create_received_job(
                user_id=user.id,
                onboarding_session_id=onboarding.id,
                telegram_update_id=document.update_id,
                telegram_file_id=document.file_id,
                telegram_file_unique_id=document.file_unique_id,
                display_filename=document.display_filename,
            )
            if job.status in {
                AppleHealthImportStatus.RECEIVED,
                AppleHealthImportStatus.PROCESSING,
            }:
                onboarding.current_step = OnboardingStep.APPLE_HEALTH_PROCESSING
                await session.flush()
            return job, user.id, created

    async def _mark_processing(
        self,
        *,
        user_id: uuid.UUID,
        job_id: uuid.UUID,
        file_sha256: str,
    ) -> None:
        async with self._session_factory.begin() as session:
            await AppleHealthRepository(session).mark_processing(
                user_id=user_id,
                job_id=job_id,
                file_sha256=file_sha256,
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
            onboarding = await OnboardingRepository(session).lock_for_user(
                user_id=user_id
            )
            onboarding.current_step = OnboardingStep.APPLE_HEALTH_IMPORT_COMPLETE
            await session.flush()
            return copied

    async def _complete(
        self,
        *,
        user_id: uuid.UUID,
        job_id: uuid.UUID,
        workouts: list[ParsedWorkout],
        workouts_found: int,
        duplicate_workouts: int,
        heart_rate_records_matched: int,
        warning_count: int,
        progress: ProgressCallback,
    ) -> AppleHealthImportJob:
        async with self._session_factory.begin() as session:
            apple_repository = AppleHealthRepository(session)
            imported = 0
            updated = 0
            unchanged = 0
            for workout in workouts:
                _, outcome = await apple_repository.upsert_workout(
                    user_id=user_id,
                    workout=workout,
                )
                if outcome == "inserted":
                    imported += 1
                elif outcome == "updated":
                    updated += 1
                else:
                    unchanged += 1
            await progress("recalculating_baseline")
            await BaselineService(
                activities=StravaRepository(session),
                baselines=BaselineRepository(session),
                analysis_days=self._settings.strava_initial_sync_days,
                clock=self._clock,
            ).recalculate(
                user_id=user_id,
                source=BaselineSource.APPLE_HEALTH_EXPORT,
            )
            job = await apple_repository.mark_succeeded(
                user_id=user_id,
                job_id=job_id,
                workouts_found=workouts_found,
                activities_imported=imported,
                activities_updated=updated,
                activities_skipped=unchanged + duplicate_workouts,
                heart_rate_records_matched=heart_rate_records_matched,
                warning_count=warning_count,
            )
            onboarding = await OnboardingRepository(session).lock_for_user(
                user_id=user_id
            )
            onboarding.current_step = OnboardingStep.APPLE_HEALTH_IMPORT_COMPLETE
            await session.flush()
            return job

    async def _fail(
        self,
        *,
        user_id: uuid.UUID,
        job_id: uuid.UUID,
        code: str,
    ) -> AppleHealthImportOutcome:
        async with self._session_factory.begin() as session:
            repository = AppleHealthRepository(session)
            job = await repository.mark_failed(
                user_id=user_id,
                job_id=job_id,
                safe_error_code=code,
            )
            onboarding = await OnboardingRepository(session).lock_for_user(
                user_id=user_id
            )
            if onboarding.status is OnboardingStatus.ACTIVE:
                onboarding.current_step = OnboardingStep.APPLE_HEALTH_IMPORT_FAILED
            await session.flush()
            return _job_outcome(job, {})

    async def _outcome(
        self,
        job: AppleHealthImportJob,
    ) -> AppleHealthImportOutcome:
        async with self._session_factory() as session:
            counts = await AppleHealthRepository(session).discipline_counts(
                user_id=job.user_id
            )
        return _job_outcome(job, counts)

    def _create_temp_path(self) -> Path:
        configured = self._settings.apple_health_import_temp_dir
        if configured is not None:
            configured.mkdir(parents=True, exist_ok=True)
        descriptor, raw_path = tempfile.mkstemp(
            prefix="apple-health-",
            suffix=".zip",
            dir=configured,
        )
        os.close(descriptor)
        return Path(raw_path)

    def _aware_now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("The Apple Health import clock must be aware.")
        return now.astimezone(UTC)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _job_outcome(
    job: AppleHealthImportJob,
    discipline_counts: dict[str, int],
) -> AppleHealthImportOutcome:
    return AppleHealthImportOutcome(
        status=job.status,
        workouts_found=job.workouts_found,
        activities_imported=job.activities_imported,
        activities_updated=job.activities_updated,
        activities_skipped=job.activities_skipped,
        heart_rate_records_matched=job.heart_rate_records_matched,
        warning_count=job.warning_count,
        discipline_counts=discipline_counts,
        safe_error_code=job.safe_error_code,
    )
