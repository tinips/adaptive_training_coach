"""Apple Health onboarding import orchestration tests."""

from __future__ import annotations

import shutil
import zipfile
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import Settings
from app.db.base import Base
from app.db.models import (
    AppleHealthImportJob,
    AthleteBaseline,
    Workout,
)
from app.domain.enums import (
    ActivitySource,
    AppleHealthImportStatus,
    BaselineSource,
    OnboardingStep,
)
from app.repositories.onboarding import OnboardingRepository
from app.repositories.users import UserRepository
from app.schemas.common import TelegramIdentity
from app.services.apple_health import (
    AppleHealthImportService,
    TelegramDocumentUpload,
)
from app.services.onboarding import OnboardingApplicationError

NOW = datetime(2026, 7, 28, 12, tzinfo=UTC)


def test_empty_configured_temp_dir_uses_system_default() -> None:
    settings = Settings(
        environment="test",
        database_url="sqlite+aiosqlite:///:memory:",
        apple_health_import_temp_dir="",
    )

    assert settings.apple_health_import_temp_dir is None


@pytest_asyncio.fixture
async def persistence() -> AsyncIterator[
    tuple[AsyncEngine, async_sessionmaker[AsyncSession]]
]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield (
        engine,
        async_sessionmaker(
            engine,
            expire_on_commit=False,
            autoflush=False,
        ),
    )
    await engine.dispose()


def identity() -> TelegramIdentity:
    return TelegramIdentity(
        telegram_user_id=8801,
        telegram_username="apple_runner",
        first_name="Apple",
        language_code="en",
    )


def export_zip(path: Path) -> None:
    xml = """<?xml version="1.0"?>
<HealthData>
  <Workout workoutActivityType="HKWorkoutActivityTypeRunning"
    duration="30" durationUnit="min" totalDistance="5"
    totalDistanceUnit="km" sourceName="Watch"
    startDate="2026-07-20 08:00:00 +0000"
    endDate="2026-07-20 08:30:00 +0000"/>
  <Record type="HKQuantityTypeIdentifierHeartRate" sourceName="Watch"
    unit="count/min" value="145"
    startDate="2026-07-20 08:10:00 +0000"
    endDate="2026-07-20 08:10:00 +0000"/>
  <Record type="HKClinicalTypeIdentifierConditionRecord" value="ignored"
    startDate="2026-07-20 08:10:00 +0000"
    endDate="2026-07-20 08:10:00 +0000"/>
</HealthData>"""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("Données Santé/mes données.xml", xml)


async def waiting_user(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with factory.begin() as session:
        user, _ = await UserRepository(session).get_or_create(
            telegram_user_id=identity().telegram_user_id,
            telegram_username=identity().telegram_username,
            first_name=identity().first_name,
        )
        onboarding, _ = await OnboardingRepository(session).get_or_create(
            user_id=user.id
        )
        onboarding.current_step = OnboardingStep.APPLE_HEALTH_WAITING_FOR_FILE
        onboarding.answers = {
            "baseline_source": BaselineSource.APPLE_HEALTH_EXPORT.value
        }


@pytest.mark.asyncio
async def test_complete_import_is_atomic_idempotent_and_deletes_upload(
    persistence: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    tmp_path: Path,
) -> None:
    _, factory = persistence
    await waiting_user(factory)
    source = tmp_path / "source.zip"
    export_zip(source)
    temp_dir = tmp_path / "temporary"
    settings = Settings(
        environment="test",
        database_url="sqlite+aiosqlite:///:memory:",
        apple_health_import_temp_dir=temp_dir,
        strava_enabled=False,
    )
    service = AppleHealthImportService(
        session_factory=factory,
        settings=settings,
        clock=lambda: NOW,
    )
    stages: list[str] = []

    async def download(destination: Path) -> None:
        shutil.copyfile(source, destination)

    async def progress(stage: str) -> None:
        stages.append(stage)

    outcome = await service.process_upload(
        identity=identity(),
        document=TelegramDocumentUpload(
            file_id="file-id",
            file_unique_id="unique-file",
            display_filename="../../private-export.zip",
            file_size=source.stat().st_size,
            update_id=1001,
        ),
        download=download,
        progress=progress,
    )

    assert outcome.status is AppleHealthImportStatus.SUCCEEDED
    assert outcome.workouts_found == 1
    assert outcome.activities_imported == 1
    assert outcome.heart_rate_records_matched == 1
    assert outcome.discipline_counts == {"RUNNING": 1}
    assert stages == [
        "validating_archive",
        "reading_workouts",
        "reading_heart_rate",
        "matching_data",
        "saving_activities",
        "recalculating_baseline",
    ]
    assert list(temp_dir.iterdir()) == []

    async with factory() as session:
        activity = await session.scalar(select(Workout))
        job = await session.scalar(select(AppleHealthImportJob))
        baseline = await session.scalar(select(AthleteBaseline))
        user = await UserRepository(session).get_by_telegram_id(
            identity().telegram_user_id
        )
        assert user is not None
        onboarding = await OnboardingRepository(session).require_for_user(
            user_id=user.id
        )

    assert activity is not None
    assert activity.source is ActivitySource.APPLE_HEALTH
    assert activity.running_details is not None
    assert activity.running_details.average_heart_rate == 145
    assert activity.running_details.max_heart_rate == 145
    assert len(activity.source_links) == 1
    assert activity.source_links[0].source_metadata_jsonb is not None
    assert activity.source_links[0].source_metadata_jsonb["source_name"] == "Watch"
    assert job is not None
    assert job.display_filename == "../../private-export.zip"
    assert job.status is AppleHealthImportStatus.SUCCEEDED
    assert baseline is not None
    assert baseline.source is BaselineSource.APPLE_HEALTH_EXPORT
    assert onboarding.current_step is OnboardingStep.APPLE_HEALTH_IMPORT_COMPLETE

    replay_downloaded = False

    async def replay_download(_: Path) -> None:
        nonlocal replay_downloaded
        replay_downloaded = True

    replay = await service.process_upload(
        identity=identity(),
        document=TelegramDocumentUpload(
            file_id="file-id",
            file_unique_id="unique-file",
            display_filename="replayed.zip",
            file_size=source.stat().st_size,
            update_id=1001,
        ),
        download=replay_download,
        progress=progress,
    )
    assert replay.status is AppleHealthImportStatus.SUCCEEDED
    assert replay_downloaded is False

    # A cumulative retry with a new Telegram update enriches or skips existing
    # canonical records rather than duplicating them.
    async with factory.begin() as session:
        onboarding = await OnboardingRepository(session).require_for_user(
            user_id=user.id
        )
        onboarding.current_step = OnboardingStep.APPLE_HEALTH_WAITING_FOR_FILE
    repeated = await service.process_upload(
        identity=identity(),
        document=TelegramDocumentUpload(
            file_id="file-id-2",
            file_unique_id="unique-file-2",
            display_filename="export.zip",
            file_size=source.stat().st_size,
            update_id=1002,
        ),
        download=download,
        progress=progress,
    )
    assert repeated.status is AppleHealthImportStatus.SUCCEEDED
    assert repeated.activities_imported == 0
    assert repeated.activities_skipped == 1
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(Workout)) == 1


@pytest.mark.asyncio
async def test_invalid_upload_fails_safely_and_cleans_temp_file(
    persistence: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    tmp_path: Path,
) -> None:
    _, factory = persistence
    await waiting_user(factory)
    source = tmp_path / "not-a-zip.zip"
    source.write_bytes(b"not a real archive")
    temp_dir = tmp_path / "temporary"
    service = AppleHealthImportService(
        session_factory=factory,
        settings=Settings(
            environment="test",
            database_url="sqlite+aiosqlite:///:memory:",
            apple_health_import_temp_dir=temp_dir,
        ),
        clock=lambda: NOW,
    )

    async def download(destination: Path) -> None:
        shutil.copyfile(source, destination)

    async def progress(_: str) -> None:
        return None

    outcome = await service.process_upload(
        identity=identity(),
        document=TelegramDocumentUpload(
            file_id="bad-file",
            file_unique_id="bad-unique",
            display_filename="bad.zip",
            file_size=source.stat().st_size,
            update_id=2001,
        ),
        download=download,
        progress=progress,
    )

    assert outcome.status is AppleHealthImportStatus.FAILED
    assert outcome.safe_error_code == "archive_not_zip"
    assert list(temp_dir.iterdir()) == []
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(Workout)) == 0
        job = await session.scalar(select(AppleHealthImportJob))
        assert job is not None
        assert job.safe_error_code == "archive_not_zip"
        assert job.file_sha256 is not None


@pytest.mark.asyncio
async def test_zip_is_not_downloaded_before_privacy_continue(
    persistence: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = persistence
    await waiting_user(factory)
    async with factory.begin() as session:
        user = await UserRepository(session).get_by_telegram_id(
            identity().telegram_user_id
        )
        assert user is not None
        onboarding = await OnboardingRepository(session).require_for_user(
            user_id=user.id
        )
        onboarding.current_step = OnboardingStep.APPLE_HEALTH_PRIVACY_NOTICE
    service = AppleHealthImportService(
        session_factory=factory,
        settings=Settings(
            environment="test",
            database_url="sqlite+aiosqlite:///:memory:",
        ),
        clock=lambda: NOW,
    )
    downloaded = False

    async def download(_: Path) -> None:
        nonlocal downloaded
        downloaded = True

    async def progress(_: str) -> None:
        return None

    with pytest.raises(
        OnboardingApplicationError,
        match="apple_health_file_not_expected",
    ):
        await service.process_upload(
            identity=identity(),
            document=TelegramDocumentUpload(
                file_id="early",
                file_unique_id="early-unique",
                display_filename="export.zip",
                file_size=1,
                update_id=3001,
            ),
            download=download,
            progress=progress,
        )
    assert downloaded is False


@pytest.mark.asyncio
async def test_recovery_marks_abandoned_processing_job_retryable(
    persistence: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = persistence
    await waiting_user(factory)
    async with factory.begin() as session:
        user = await UserRepository(session).get_by_telegram_id(
            identity().telegram_user_id
        )
        assert user is not None
        onboarding = await OnboardingRepository(session).require_for_user(
            user_id=user.id
        )
        onboarding.current_step = OnboardingStep.APPLE_HEALTH_PROCESSING
        session.add(
            AppleHealthImportJob(
                user_id=user.id,
                onboarding_session_id=onboarding.id,
                telegram_file_id="file",
                telegram_file_unique_id="unique",
                display_filename="export.zip",
                status=AppleHealthImportStatus.PROCESSING,
                started_at=datetime(2026, 7, 28, 10, tzinfo=UTC),
            )
        )
    service = AppleHealthImportService(
        session_factory=factory,
        settings=Settings(
            environment="test",
            database_url="sqlite+aiosqlite:///:memory:",
        ),
        clock=lambda: NOW,
    )

    assert await service.recover_stale_work() == 1
    async with factory() as session:
        job = await session.scalar(select(AppleHealthImportJob))
        user = await UserRepository(session).get_by_telegram_id(
            identity().telegram_user_id
        )
        assert user is not None
        onboarding = await OnboardingRepository(session).require_for_user(
            user_id=user.id
        )
    assert job is not None
    assert job.status is AppleHealthImportStatus.FAILED
    assert job.safe_error_code == "import_interrupted"
    assert onboarding.current_step is OnboardingStep.APPLE_HEALTH_IMPORT_FAILED
