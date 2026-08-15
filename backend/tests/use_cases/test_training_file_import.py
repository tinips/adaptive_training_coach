"""Focused daily training-file import use-case tests."""

from __future__ import annotations

import shutil
import uuid
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
    ActivitySourceLink,
    AppleHealthImportJob,
    CyclingWorkoutDetails,
    OnboardingSession,
    SwimmingWorkoutDetails,
    Workout,
    WorkoutHeartRateObservation,
)
from app.domain.enums import (
    ActivitySource,
    AppleHealthImportStatus,
    Discipline,
    OnboardingStatus,
    OnboardingStep,
    SwimmingEnvironment,
    TrainingFileFormat,
    TrainingImportContext,
    UserStatus,
)
from app.repositories.users import UserRepository
from app.schemas.common import TelegramIdentity
from app.schemas.training_import import TelegramDocumentUpload
from app.services.onboarding import OnboardingApplicationError
from app.services.training_import import (
    TrainingFileImportOutcome,
    TrainingFileImportService,
)

NOW = datetime(2026, 7, 29, 12, tzinfo=UTC)


@pytest_asyncio.fixture
async def persistence() -> AsyncIterator[
    tuple[AsyncEngine, async_sessionmaker[AsyncSession]]
]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    yield engine, factory
    await engine.dispose()


def identity(telegram_user_id: int) -> TelegramIdentity:
    return TelegramIdentity(
        telegram_user_id=telegram_user_id,
        telegram_username=f"athlete_{telegram_user_id}",
        first_name="Athlete",
        language_code="en",
    )


async def stage_user(
    factory: async_sessionmaker[AsyncSession],
    *,
    owner: TelegramIdentity,
    status: UserStatus = UserStatus.ONBOARDING_COMPLETED,
) -> uuid.UUID:
    async with factory.begin() as session:
        user, _ = await UserRepository(session).get_or_create(
            telegram_user_id=owner.telegram_user_id,
            telegram_username=owner.telegram_username,
            first_name=owner.first_name,
        )
        user.status = status
        await session.flush()
        return user.id


def service(
    factory: async_sessionmaker[AsyncSession],
    *,
    temp_dir: Path,
) -> TrainingFileImportService:
    return TrainingFileImportService(
        session_factory=factory,
        settings=Settings(
            environment="test",
            database_url="sqlite+aiosqlite:///:memory:",
            telegram_bot_username=None,
            apple_health_import_temp_dir=temp_dir,
            apple_health_import_enabled=True,
            apple_health_import_max_compressed_size_mb=25,
            tcx_import_enabled=True,
            tcx_import_max_size_mb=25,
        ),
        clock=lambda: NOW,
    )


def write_tcx(
    path: Path,
    *,
    activity_id: str,
    duration_seconds: int = 1800,
    distance_meters: int = 5000,
) -> Path:
    path.write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<TrainingCenterDatabase
 xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2">
  <Activities>
    <Activity Sport="Running">
      <Id>{activity_id}</Id>
      <Lap StartTime="{activity_id}">
        <TotalTimeSeconds>{duration_seconds}</TotalTimeSeconds>
        <DistanceMeters>{distance_meters}</DistanceMeters>
        <Calories>250</Calories>
        <Track/>
      </Lap>
    </Activity>
  </Activities>
</TrainingCenterDatabase>""",
        encoding="utf-8",
    )
    return path


def write_apple_zip(path: Path) -> Path:
    start = "2026-07-25 08:00:00 +0000"
    xml = f"""<?xml version="1.0"?>
<HealthData>
  <Workout workoutActivityType="HKWorkoutActivityTypeCycling"
    duration="60" durationUnit="min" totalDistance="25"
    totalDistanceUnit="km" totalEnergyBurned="500"
    totalEnergyBurnedUnit="kcal" sourceName="Synthetic Watch"
    creationDate="2026-07-25 09:01:00 +0000"
    startDate="{start}" endDate="2026-07-25 09:00:00 +0000">
    <WorkoutStatistics type="HKQuantityTypeIdentifierActiveEnergyBurned"
      startDate="{start}" endDate="2026-07-25 09:00:00 +0000"
      sum="500" unit="kcal"/>
  </Workout>
  <Record type="HKQuantityTypeIdentifierHeartRate"
    sourceName="Synthetic Watch" unit="count/min" value="142"
    startDate="{start}" endDate="{start}"/>
</HealthData>"""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("apple_health_export/export.xml", xml)
    return path


def write_empty_apple_zip(path: Path) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "apple_health_export/export.xml",
            "<HealthData><Record type='ignored'/></HealthData>",
        )
    return path


def write_unknown_swim_zip(path: Path) -> Path:
    xml = """<HealthData>
      <Workout workoutActivityType="HKWorkoutActivityTypeSwimming"
        duration="30" durationUnit="min" sourceName="Synthetic Watch"
        startDate="2026-07-26 08:00:00 +0000"
        endDate="2026-07-26 08:30:00 +0000">
        <WorkoutStatistics type="HKQuantityTypeIdentifierDistanceSwimming"
          startDate="2026-07-26 08:00:00 +0000"
          endDate="2026-07-26 08:30:00 +0000" sum="1000" unit="m"/>
      </Workout>
    </HealthData>"""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("apple_health_export/export.xml", xml)
    return path


async def upload(
    import_service: TrainingFileImportService,
    *,
    owner: TelegramIdentity,
    source: Path,
    update_id: int,
) -> tuple[TrainingFileImportOutcome, list[str]]:
    stages: list[str] = []

    async def download(destination: Path) -> None:
        shutil.copyfile(source, destination)

    async def progress(stage: str) -> None:
        stages.append(stage)

    result = await import_service.process_upload(
        identity=owner,
        document=TelegramDocumentUpload(
            file_id=f"file-{update_id}",
            file_unique_id=f"unique-{update_id}",
            display_filename=source.name,
            file_size=None,
            update_id=update_id,
        ),
        download=download,
        progress=progress,
    )
    return result, stages


@pytest.mark.asyncio
async def test_file_upload_is_not_an_onboarding_action(
    persistence: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    tmp_path: Path,
) -> None:
    _, factory = persistence
    owner = identity(9100)
    await stage_user(
        factory,
        owner=owner,
        status=UserStatus.ONBOARDING_IN_PROGRESS,
    )
    tcx = write_tcx(tmp_path / "blocked.tcx", activity_id="2026-07-28T08:00:00Z")

    with pytest.raises(OnboardingApplicationError) as error:
        await upload(
            service(factory, temp_dir=tmp_path / "temporary"),
            owner=owner,
            source=tcx,
            update_id=100,
        )

    assert error.value.code == "training_file_not_expected"


@pytest.mark.asyncio
async def test_existing_athlete_tcx_imports(
    persistence: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    tmp_path: Path,
) -> None:
    _, factory = persistence
    owner = identity(9101)
    user_id = await stage_user(factory, owner=owner)
    tcx = write_tcx(
        tmp_path / "daily.tcx",
        activity_id="2026-07-28T09:00:00Z",
        duration_seconds=2400,
        distance_meters=7200,
    )

    outcome, stages = await upload(
        service(factory, temp_dir=tmp_path / "temporary"),
        owner=owner,
        source=tcx,
        update_id=101,
    )

    assert outcome.status is AppleHealthImportStatus.SUCCEEDED
    assert outcome.file_format is TrainingFileFormat.TCX
    assert outcome.sport == "RUNNING"
    assert outcome.duration_seconds == 2400
    assert outcome.distance_meters == 7200
    assert "saving_activities" in stages
    async with factory() as session:
        workout = await session.get(Workout, outcome.activity_id)
        assert workout is not None
        assert workout.athlete_id == user_id
        assert workout.source is ActivitySource.TCX


@pytest.mark.asyncio
async def test_existing_athlete_apple_history_import_stays_available(
    persistence: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    tmp_path: Path,
) -> None:
    _, factory = persistence
    owner = identity(9102)
    user_id = await stage_user(factory, owner=owner)

    outcome, stages = await upload(
        service(factory, temp_dir=tmp_path / "temporary"),
        owner=owner,
        source=write_apple_zip(tmp_path / "daily-history.zip"),
        update_id=102,
    )

    assert outcome.status is AppleHealthImportStatus.SUCCEEDED
    assert outcome.file_format is TrainingFileFormat.APPLE_HEALTH_ZIP
    assert outcome.average_heart_rate == 142
    assert "saving_activities" in stages
    async with factory() as session:
        workout = await session.get(Workout, outcome.activity_id)
        assert workout is not None
        assert workout.athlete_id == user_id
        assert workout.discipline is Discipline.CYCLING
        assert workout.source is ActivitySource.APPLE_HEALTH
        assert isinstance(workout.cycling_details, CyclingWorkoutDetails)
        assert workout.cycling_details.distance_meters == 25_000
        observations = tuple(
            (
                await session.scalars(
                    select(WorkoutHeartRateObservation).where(
                        WorkoutHeartRateObservation.user_id == user_id,
                        WorkoutHeartRateObservation.workout_id == workout.id,
                    )
                )
            ).all()
        )
        assert len(observations) == 1
        assert observations[0].beats_per_minute == 142
        link = await session.scalar(
            select(ActivitySourceLink).where(
                ActivitySourceLink.user_id == user_id,
                ActivitySourceLink.workout_id == workout.id,
            )
        )
        assert link is not None
        assert link.external_id == workout.external_id
        assert not link.external_id.startswith("fingerprint:")
        assert link.source_metadata_jsonb is not None
        assert link.source_metadata_jsonb["creation_date"] == (
            "2026-07-25T09:01:00+00:00"
        )
        assert link.source_metadata_jsonb["workout_statistics"] == [
            {
                "type": "HKQuantityTypeIdentifierActiveEnergyBurned",
                "startDate": "2026-07-25 08:00:00 +0000",
                "endDate": "2026-07-25 09:00:00 +0000",
                "sum": "500",
                "unit": "kcal",
            }
        ]


@pytest.mark.asyncio
async def test_unknown_swimming_environment_remains_a_swim(
    persistence: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    tmp_path: Path,
) -> None:
    _, factory = persistence
    owner = identity(9108)
    await stage_user(factory, owner=owner)

    outcome, _ = await upload(
        service(factory, temp_dir=tmp_path / "temporary"),
        owner=owner,
        source=write_unknown_swim_zip(tmp_path / "unknown-swim.zip"),
        update_id=108,
    )

    assert outcome.status is AppleHealthImportStatus.SUCCEEDED
    async with factory() as session:
        workout = await session.get(Workout, outcome.activity_id)
        assert workout is not None
        assert workout.discipline is Discipline.SWIMMING
        assert isinstance(workout.swimming_details, SwimmingWorkoutDetails)
        assert (
            workout.swimming_details.swimming_environment is SwimmingEnvironment.UNKNOWN
        )


@pytest.mark.asyncio
async def test_exact_apple_reimport_keeps_workout_and_observation_counts_stable(
    persistence: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    tmp_path: Path,
) -> None:
    _, factory = persistence
    owner = identity(9109)
    user_id = await stage_user(factory, owner=owner)
    import_service = service(factory, temp_dir=tmp_path / "temporary")
    source = write_apple_zip(tmp_path / "same-history.zip")

    first, _ = await upload(import_service, owner=owner, source=source, update_id=109)
    duplicate, _ = await upload(
        import_service, owner=owner, source=source, update_id=110
    )

    assert first.status is AppleHealthImportStatus.SUCCEEDED
    assert duplicate.status is AppleHealthImportStatus.SUCCEEDED
    assert duplicate.exact_file_duplicate is True
    async with factory() as session:
        workout_count = await session.scalar(
            select(func.count())
            .select_from(Workout)
            .where(Workout.athlete_id == user_id)
        )
        observation_count = await session.scalar(
            select(func.count())
            .select_from(WorkoutHeartRateObservation)
            .where(WorkoutHeartRateObservation.user_id == user_id)
        )
        assert workout_count == 1
        assert observation_count == 1


@pytest.mark.asyncio
async def test_onboarding_history_import_completes_atomically(
    persistence: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    tmp_path: Path,
) -> None:
    _, factory = persistence
    owner = identity(9110)
    user_id = await stage_user(
        factory,
        owner=owner,
        status=UserStatus.ONBOARDING_IN_PROGRESS,
    )
    async with factory.begin() as session:
        session.add(
            OnboardingSession(
                user_id=user_id,
                status=OnboardingStatus.ACTIVE,
                current_step=OnboardingStep.TRAINING_HISTORY_IMPORT,
                answers={"consent": True},
            )
        )

    outcome, _ = await upload(
        service(factory, temp_dir=tmp_path / "temporary"),
        owner=owner,
        source=write_apple_zip(tmp_path / "onboarding-history.zip"),
        update_id=110,
    )

    assert outcome.status is AppleHealthImportStatus.SUCCEEDED
    assert outcome.context is TrainingImportContext.ONBOARDING_HISTORY
    assert outcome.completed_onboarding is True
    async with factory() as session:
        persisted_user = await UserRepository(session).require_by_id(user_id)
        onboarding = await session.scalar(
            select(OnboardingSession).where(OnboardingSession.user_id == user_id)
        )
        job = await session.scalar(
            select(AppleHealthImportJob).where(
                AppleHealthImportJob.id == outcome.job_id,
                AppleHealthImportJob.user_id == user_id,
            )
        )
        assert persisted_user.status is UserStatus.ONBOARDING_COMPLETED
        assert onboarding is not None
        assert onboarding.status is OnboardingStatus.COMPLETED
        assert onboarding.current_step is OnboardingStep.TRAINING_HISTORY_IMPORT
        assert job is not None
        assert job.context is TrainingImportContext.ONBOARDING_HISTORY
        assert job.onboarding_session_id == onboarding.id


@pytest.mark.asyncio
async def test_zero_workout_archive_keeps_onboarding_active(
    persistence: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    tmp_path: Path,
) -> None:
    _, factory = persistence
    owner = identity(9111)
    user_id = await stage_user(
        factory,
        owner=owner,
        status=UserStatus.ONBOARDING_IN_PROGRESS,
    )
    async with factory.begin() as session:
        session.add(
            OnboardingSession(
                user_id=user_id,
                status=OnboardingStatus.ACTIVE,
                current_step=OnboardingStep.TRAINING_HISTORY_IMPORT,
                answers={},
            )
        )

    outcome, _ = await upload(
        service(factory, temp_dir=tmp_path / "temporary"),
        owner=owner,
        source=write_empty_apple_zip(tmp_path / "empty-history.zip"),
        update_id=111,
    )

    assert outcome.status is AppleHealthImportStatus.FAILED
    assert outcome.safe_error_code == "training_file_no_workouts"
    assert outcome.completed_onboarding is False
    async with factory() as session:
        persisted_user = await UserRepository(session).require_by_id(user_id)
        onboarding = await session.scalar(
            select(OnboardingSession).where(OnboardingSession.user_id == user_id)
        )
        workout_count = await session.scalar(
            select(func.count())
            .select_from(Workout)
            .where(Workout.athlete_id == user_id)
        )
        assert persisted_user.status is UserStatus.ONBOARDING_IN_PROGRESS
        assert onboarding is not None
        assert onboarding.status is OnboardingStatus.ACTIVE
        assert onboarding.current_step is OnboardingStep.TRAINING_HISTORY_IMPORT
        assert workout_count == 0


@pytest.mark.asyncio
async def test_exact_daily_file_duplicate_does_not_duplicate_workout(
    persistence: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    tmp_path: Path,
) -> None:
    _, factory = persistence
    owner = identity(9103)
    user_id = await stage_user(factory, owner=owner)
    import_service = service(factory, temp_dir=tmp_path / "temporary")
    tcx = write_tcx(tmp_path / "same.tcx", activity_id="2026-07-28T10:00:00Z")

    first, _ = await upload(import_service, owner=owner, source=tcx, update_id=103)
    duplicate, _ = await upload(import_service, owner=owner, source=tcx, update_id=104)

    assert first.activity_id == duplicate.activity_id
    assert duplicate.exact_file_duplicate is True
    async with factory() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(Workout)
            .where(Workout.athlete_id == user_id)
        )
        assert count == 1


@pytest.mark.asyncio
async def test_cancelled_daily_job_cannot_persist_activity(
    persistence: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    tmp_path: Path,
) -> None:
    _, factory = persistence
    owner = identity(9104)
    user_id = await stage_user(factory, owner=owner)
    import_service = service(factory, temp_dir=tmp_path / "temporary")
    tcx = write_tcx(tmp_path / "cancelled.tcx", activity_id="2026-07-28T11:00:00Z")

    async def download(destination: Path) -> None:
        shutil.copyfile(tcx, destination)

    async def progress(stage: str) -> None:
        if stage == "saving_activities":
            await import_service.cancel_active(user_id=user_id)

    outcome = await import_service.process_upload(
        identity=owner,
        document=TelegramDocumentUpload(
            file_id="cancelled",
            file_unique_id="cancelled-unique",
            display_filename="cancelled.tcx",
            file_size=None,
            update_id=105,
        ),
        download=download,
        progress=progress,
    )

    assert outcome.status is AppleHealthImportStatus.CANCELLED
    async with factory() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(Workout)
            .where(Workout.athlete_id == user_id)
        )
        assert count == 0


@pytest.mark.asyncio
async def test_startup_recovery_cleans_recorded_daily_temp_file(
    persistence: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    tmp_path: Path,
) -> None:
    _, factory = persistence
    owner = identity(9105)
    user_id = await stage_user(factory, owner=owner)
    temp_dir = tmp_path / "temporary"
    temp_dir.mkdir()
    recorded_path = temp_dir / "training-import-crashed.upload"
    recorded_path.write_bytes(b"synthetic private workout content")
    async with factory.begin() as session:
        session.add(
            AppleHealthImportJob(
                user_id=user_id,
                telegram_file_id="crashed",
                telegram_file_unique_id="crashed-unique",
                display_filename="workout.tcx",
                temporary_path=str(recorded_path),
                file_format=TrainingFileFormat.TCX,
                status=AppleHealthImportStatus.PROCESSING,
                started_at=NOW,
            )
        )

    recovered = await service(factory, temp_dir=temp_dir).recover_stale_work()

    assert recovered == 1
    assert not recorded_path.exists()
    async with factory() as session:
        job = await session.scalar(
            select(AppleHealthImportJob).where(AppleHealthImportJob.user_id == user_id)
        )
        assert job is not None
        assert job.status is AppleHealthImportStatus.FAILED
        assert job.safe_error_code == "import_interrupted"
        assert job.temporary_path is None
