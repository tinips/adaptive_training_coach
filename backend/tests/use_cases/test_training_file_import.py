"""Unified training-file import use-case tests with synthetic documents."""

from __future__ import annotations

import asyncio
import shutil
import uuid
import zipfile
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
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
    BaselinePreference,
    CyclingWorkoutDetails,
    OnboardingSession,
    RunningWorkoutDetails,
    User,
    Workout,
)
from app.domain.enums import (
    ActivitySource,
    AppleHealthImportStatus,
    BaselinePreferenceStatus,
    BaselineSource,
    Discipline,
    OnboardingStatus,
    OnboardingStep,
    TrainingFileFormat,
    TrainingImportContext,
    UserStatus,
)
from app.repositories.onboarding import OnboardingRepository
from app.repositories.users import UserRepository
from app.schemas.common import TelegramIdentity
from app.services.apple_health import TelegramDocumentUpload
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
    factory = async_sessionmaker(
        engine,
        expire_on_commit=False,
        autoflush=False,
    )
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
    completed: bool = False,
) -> uuid.UUID:
    async with factory.begin() as session:
        user, _ = await UserRepository(session).get_or_create(
            telegram_user_id=owner.telegram_user_id,
            telegram_username=owner.telegram_username,
            first_name=owner.first_name,
        )
        onboarding, _ = await OnboardingRepository(session).get_or_create(
            user_id=user.id
        )
        if completed:
            onboarding.status = OnboardingStatus.COMPLETED
            onboarding.current_step = OnboardingStep.SUMMARY
            onboarding.completed_at = NOW
            user.status = UserStatus.PROFILE_COMPLETED
        else:
            onboarding.status = OnboardingStatus.ACTIVE
            onboarding.current_step = OnboardingStep.FILE_IMPORT_WAITING
            onboarding.answers = {
                "baseline_source": BaselineSource.FILE_IMPORT.value,
            }
        await session.flush()
        return user.id


def service(
    factory: async_sessionmaker[AsyncSession],
    *,
    temp_dir: Path,
    max_size_mb: int = 25,
) -> TrainingFileImportService:
    return TrainingFileImportService(
        session_factory=factory,
        settings=Settings(
            environment="test",
            database_url="sqlite+aiosqlite:///:memory:",
            telegram_bot_username=None,
            apple_health_import_temp_dir=temp_dir,
            apple_health_import_enabled=True,
            apple_health_import_max_compressed_size_mb=max_size_mb,
            tcx_import_enabled=True,
            tcx_import_max_size_mb=max_size_mb,
            strava_enabled=False,
        ),
        clock=lambda: NOW,
    )


def write_tcx(
    path: Path,
    *,
    activity_id: str,
    sport: str = "Running",
    duration_seconds: int = 1800,
    distance_meters: int = 5000,
    average_heart_rate: int | None = None,
) -> Path:
    heart_rate = (
        "<AverageHeartRateBpm>"
        f"<Value>{average_heart_rate}</Value>"
        "</AverageHeartRateBpm>"
        if average_heart_rate is not None
        else ""
    )
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<TrainingCenterDatabase
 xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2">
  <Activities>
    <Activity Sport="{sport}">
      <Id>{activity_id}</Id>
      <Lap StartTime="{activity_id}">
        <TotalTimeSeconds>{duration_seconds}</TotalTimeSeconds>
        <DistanceMeters>{distance_meters}</DistanceMeters>
        <Calories>250</Calories>
        {heart_rate}
        <Track/>
      </Lap>
    </Activity>
  </Activities>
</TrainingCenterDatabase>"""
    path.write_text(xml, encoding="utf-8")
    return path


def write_apple_zip(
    path: Path,
    *,
    start: str = "2026-07-25 08:00:00 +0000",
    end: str = "2026-07-25 09:00:00 +0000",
    workout_type: str = "HKWorkoutActivityTypeCycling",
) -> Path:
    xml = f"""<?xml version="1.0"?>
<HealthData>
  <Workout workoutActivityType="{workout_type}"
    duration="60" durationUnit="min" totalDistance="25"
    totalDistanceUnit="km" totalEnergyBurned="500"
    totalEnergyBurnedUnit="kcal" sourceName="Synthetic Watch"
    startDate="{start}" endDate="{end}"/>
  <Record type="HKQuantityTypeIdentifierHeartRate"
    sourceName="Synthetic Watch" unit="count/min" value="142"
    startDate="{start}" endDate="{start}"/>
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

    outcome = await import_service.process_upload(
        identity=owner,
        document=TelegramDocumentUpload(
            file_id=f"file-{update_id}",
            file_unique_id=f"unique-{update_id}",
            display_filename=f"../../{source.name}",
            file_size=None,
            update_id=update_id,
        ),
        download=download,
        progress=progress,
    )
    return outcome, stages


async def scalar_count(
    session: AsyncSession,
    model: type[object],
    *,
    user_id: uuid.UUID | None = None,
    owner_field: str = "user_id",
) -> int:
    statement = select(func.count()).select_from(model)
    if user_id is not None:
        statement = statement.where(getattr(model, owner_field) == user_id)
    return int((await session.scalar(statement)) or 0)


@pytest.mark.asyncio
async def test_onboarding_tcx_waits_until_finish_then_creates_file_baseline(
    persistence: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    tmp_path: Path,
) -> None:
    _, factory = persistence
    owner = identity(9101)
    user_id = await stage_user(factory, owner=owner)
    temp_dir = tmp_path / "temporary"
    import_service = service(factory, temp_dir=temp_dir)
    tcx = write_tcx(
        tmp_path / "workout.tcx",
        activity_id="2026-07-28T08:00:00Z",
        average_heart_rate=148,
    )

    outcome, stages = await upload(
        import_service,
        owner=owner,
        source=tcx,
        update_id=1001,
    )

    assert outcome.status is AppleHealthImportStatus.SUCCEEDED
    assert outcome.context is TrainingImportContext.ONBOARDING
    assert outcome.file_format is TrainingFileFormat.TCX
    assert outcome.activities_imported == 1
    assert outcome.activity_id is not None
    assert stages == [
        "detecting_format",
        "reading_tcx",
        "matching_data",
        "saving_activities",
    ]
    assert list(temp_dir.iterdir()) == []
    async with factory() as session:
        onboarding = await session.scalar(
            select(OnboardingSession).where(OnboardingSession.user_id == user_id)
        )
        assert onboarding is not None
        assert onboarding.current_step is OnboardingStep.FILE_IMPORT_WAITING
        assert await scalar_count(session, AthleteBaseline, user_id=user_id) == 0

    finished = await import_service.finish_onboarding_import(identity=owner)

    assert finished.status is AppleHealthImportStatus.SUCCEEDED
    assert finished.context is TrainingImportContext.ONBOARDING
    assert finished.file_format is TrainingFileFormat.UNKNOWN
    assert finished.workouts_found == 1
    assert finished.discipline_counts == {"RUNNING": 1}
    async with factory() as session:
        onboarding = await session.scalar(
            select(OnboardingSession).where(OnboardingSession.user_id == user_id)
        )
        baseline = await session.scalar(
            select(AthleteBaseline).where(AthleteBaseline.user_id == user_id)
        )
        assert onboarding is not None
        assert onboarding.current_step is OnboardingStep.FILE_IMPORT_COMPLETE
        assert onboarding.answers["baseline_source"] == BaselineSource.FILE_IMPORT
        assert baseline is not None
        assert baseline.source is BaselineSource.FILE_IMPORT


@pytest.mark.asyncio
async def test_onboarding_accepts_sequential_tcx_and_exact_duplicate(
    persistence: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    tmp_path: Path,
) -> None:
    _, factory = persistence
    owner = identity(9102)
    user_id = await stage_user(factory, owner=owner)
    import_service = service(factory, temp_dir=tmp_path / "temporary")
    first_file = write_tcx(
        tmp_path / "first.tcx",
        activity_id="2026-07-27T08:00:00Z",
    )
    second_file = write_tcx(
        tmp_path / "second.tcx",
        activity_id="2026-07-28T08:00:00Z",
    )

    first, _ = await upload(
        import_service,
        owner=owner,
        source=first_file,
        update_id=2001,
    )
    second, _ = await upload(
        import_service,
        owner=owner,
        source=second_file,
        update_id=2002,
    )
    duplicate, _ = await upload(
        import_service,
        owner=owner,
        source=first_file,
        update_id=2003,
    )

    assert first.activities_imported == 1
    assert second.activities_imported == 1
    assert duplicate.exact_file_duplicate is True
    assert duplicate.activities_imported == 0
    assert duplicate.activities_skipped == 1
    assert duplicate.activity_id == first.activity_id
    assert duplicate.discipline_counts == {"RUNNING": 2}
    async with factory() as session:
        assert (
            await scalar_count(
                session,
                Workout,
                user_id=user_id,
                owner_field="athlete_id",
            )
            == 2
        )
        assert await scalar_count(session, AppleHealthImportJob, user_id=user_id) == 3
        assert await scalar_count(session, AthleteBaseline, user_id=user_id) == 0
        onboarding = await session.scalar(
            select(OnboardingSession).where(OnboardingSession.user_id == user_id)
        )
        assert onboarding is not None
        assert onboarding.current_step is OnboardingStep.FILE_IMPORT_WAITING


@pytest.mark.asyncio
async def test_onboarding_accepts_mixed_apple_zip_and_tcx(
    persistence: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    tmp_path: Path,
) -> None:
    _, factory = persistence
    owner = identity(9103)
    user_id = await stage_user(factory, owner=owner)
    import_service = service(factory, temp_dir=tmp_path / "temporary")
    tcx = write_tcx(
        tmp_path / "run.tcx",
        activity_id="2026-07-28T08:00:00Z",
    )
    apple = write_apple_zip(tmp_path / "history.zip")

    tcx_outcome, _ = await upload(
        import_service,
        owner=owner,
        source=tcx,
        update_id=3001,
    )
    apple_outcome, _ = await upload(
        import_service,
        owner=owner,
        source=apple,
        update_id=3002,
    )

    assert tcx_outcome.file_format is TrainingFileFormat.TCX
    assert apple_outcome.file_format is TrainingFileFormat.APPLE_HEALTH_ZIP
    assert tcx_outcome.activities_imported == 1
    assert apple_outcome.activities_imported == 1
    assert apple_outcome.heart_rate_records_matched == 1
    async with factory() as session:
        workouts = tuple(
            (
                await session.scalars(
                    select(Workout).where(Workout.athlete_id == user_id)
                )
            ).all()
        )
        assert {workout.source for workout in workouts} == {
            ActivitySource.TCX,
            ActivitySource.APPLE_HEALTH,
        }
        assert {workout.discipline for workout in workouts} == {
            Discipline.RUNNING,
            Discipline.CYCLING,
        }
        assert (
            sum(
                isinstance(workout.running_details, RunningWorkoutDetails)
                for workout in workouts
            )
            == 1
        )
        assert (
            sum(
                isinstance(workout.cycling_details, CyclingWorkoutDetails)
                for workout in workouts
            )
            == 1
        )
        assert await scalar_count(session, AthleteBaseline, user_id=user_id) == 0

    finished = await import_service.finish_onboarding_import(identity=owner)

    assert finished.workouts_found == 2
    assert finished.activities_imported == 2
    assert finished.discipline_counts == {"CYCLING": 1, "RUNNING": 1}


@pytest.mark.asyncio
async def test_finish_with_no_valid_activities_stays_waiting(
    persistence: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    tmp_path: Path,
) -> None:
    _, factory = persistence
    owner = identity(9104)
    user_id = await stage_user(factory, owner=owner)
    import_service = service(factory, temp_dir=tmp_path / "temporary")

    with pytest.raises(
        OnboardingApplicationError,
        match="no_valid_imported_activities",
    ):
        await import_service.finish_onboarding_import(identity=owner)

    async with factory() as session:
        onboarding = await session.scalar(
            select(OnboardingSession).where(OnboardingSession.user_id == user_id)
        )
        assert onboarding is not None
        assert onboarding.current_step is OnboardingStep.FILE_IMPORT_WAITING
        assert await scalar_count(session, AthleteBaseline, user_id=user_id) == 0


@pytest.mark.asyncio
async def test_unsupported_file_stays_waiting_and_temp_file_is_deleted(
    persistence: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    tmp_path: Path,
) -> None:
    _, factory = persistence
    owner = identity(9105)
    user_id = await stage_user(factory, owner=owner)
    temp_dir = tmp_path / "temporary"
    import_service = service(factory, temp_dir=temp_dir)
    unsupported = tmp_path / "workout.fit"
    unsupported.write_bytes(b"not a supported training document")

    outcome, stages = await upload(
        import_service,
        owner=owner,
        source=unsupported,
        update_id=5001,
    )

    assert outcome.status is AppleHealthImportStatus.FAILED
    assert outcome.context is TrainingImportContext.ONBOARDING
    assert outcome.file_format is TrainingFileFormat.UNKNOWN
    assert outcome.safe_error_code == "unsupported_training_file"
    assert stages == ["detecting_format"]
    assert list(temp_dir.iterdir()) == []
    async with factory() as session:
        onboarding = await session.scalar(
            select(OnboardingSession).where(OnboardingSession.user_id == user_id)
        )
        assert onboarding is not None
        assert onboarding.current_step is OnboardingStep.FILE_IMPORT_WAITING
        assert (
            await scalar_count(
                session,
                Workout,
                user_id=user_id,
                owner_field="athlete_id",
            )
            == 0
        )
        assert await scalar_count(session, AthleteBaseline, user_id=user_id) == 0


@pytest.mark.asyncio
async def test_post_onboarding_tcx_returns_activity_and_recalculates(
    persistence: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    tmp_path: Path,
) -> None:
    _, factory = persistence
    owner = identity(9106)
    user_id = await stage_user(factory, owner=owner, completed=True)
    import_service = service(factory, temp_dir=tmp_path / "temporary")
    tcx = write_tcx(
        tmp_path / "daily.tcx",
        activity_id="2026-07-28T09:00:00Z",
        duration_seconds=2400,
        distance_meters=7200,
    )

    outcome, stages = await upload(
        import_service,
        owner=owner,
        source=tcx,
        update_id=6001,
    )

    assert outcome.status is AppleHealthImportStatus.SUCCEEDED
    assert outcome.context is TrainingImportContext.DAILY
    assert outcome.file_format is TrainingFileFormat.TCX
    assert outcome.activity_id is not None
    assert outcome.sport == "RUNNING"
    assert outcome.duration_seconds == 2400
    assert outcome.distance_meters == 7200
    assert "recalculating_baseline" in stages
    async with factory() as session:
        baseline = await session.scalar(
            select(AthleteBaseline).where(AthleteBaseline.user_id == user_id)
        )
        preference = await session.scalar(
            select(BaselinePreference).where(BaselinePreference.user_id == user_id)
        )
        user = await session.get(User, user_id)
        onboarding = await session.scalar(
            select(OnboardingSession).where(OnboardingSession.user_id == user_id)
        )
        assert baseline is not None
        assert baseline.source is BaselineSource.FILE_IMPORT
        assert preference is not None
        assert preference.selected_source is BaselineSource.FILE_IMPORT
        assert preference.status is BaselinePreferenceStatus.READY
        assert user is not None
        assert user.status is UserStatus.BASELINE_READY
        assert onboarding is not None
        assert onboarding.status is OnboardingStatus.COMPLETED
        assert onboarding.current_step is OnboardingStep.SUMMARY


@pytest.mark.asyncio
async def test_post_onboarding_apple_zip_import_recalculates(
    persistence: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    tmp_path: Path,
) -> None:
    _, factory = persistence
    owner = identity(9107)
    user_id = await stage_user(factory, owner=owner, completed=True)
    import_service = service(factory, temp_dir=tmp_path / "temporary")
    apple = write_apple_zip(tmp_path / "daily-history.zip")

    outcome, stages = await upload(
        import_service,
        owner=owner,
        source=apple,
        update_id=7001,
    )

    assert outcome.status is AppleHealthImportStatus.SUCCEEDED
    assert outcome.context is TrainingImportContext.DAILY
    assert outcome.file_format is TrainingFileFormat.APPLE_HEALTH_ZIP
    assert outcome.activities_imported == 1
    assert outcome.activity_id is not None
    assert outcome.sport == "CYCLING"
    assert outcome.average_heart_rate == 142
    assert "recalculating_baseline" in stages
    async with factory() as session:
        workout = await session.get(Workout, outcome.activity_id)
        baseline = await session.scalar(
            select(AthleteBaseline).where(AthleteBaseline.user_id == user_id)
        )
        assert workout is not None
        assert workout.athlete_id == user_id
        assert workout.discipline is Discipline.CYCLING
        assert workout.source is ActivitySource.APPLE_HEALTH
        assert isinstance(workout.cycling_details, CyclingWorkoutDetails)
        assert workout.cycling_details.distance_meters == 25_000
        assert workout.cycling_details.average_heart_rate == 142
        assert baseline is not None
        assert baseline.source is BaselineSource.FILE_IMPORT


@pytest.mark.asyncio
async def test_exact_file_dedup_and_finish_are_isolated_between_users(
    persistence: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    tmp_path: Path,
) -> None:
    _, factory = persistence
    first_owner = identity(9108)
    second_owner = identity(9109)
    first_user_id = await stage_user(factory, owner=first_owner)
    second_user_id = await stage_user(factory, owner=second_owner)
    import_service = service(factory, temp_dir=tmp_path / "temporary")
    tcx = write_tcx(
        tmp_path / "shared.tcx",
        activity_id="2026-07-28T10:00:00Z",
    )

    first, _ = await upload(
        import_service,
        owner=first_owner,
        source=tcx,
        update_id=8001,
    )
    second, _ = await upload(
        import_service,
        owner=second_owner,
        source=tcx,
        update_id=8001,
    )

    assert first.activities_imported == 1
    assert second.activities_imported == 1
    assert first.exact_file_duplicate is False
    assert second.exact_file_duplicate is False
    assert first.activity_id != second.activity_id

    await import_service.finish_onboarding_import(identity=first_owner)

    async with factory() as session:
        workouts = tuple((await session.scalars(select(Workout))).all())
        assert {workout.athlete_id for workout in workouts} == {
            first_user_id,
            second_user_id,
        }
        assert (
            await scalar_count(
                session,
                Workout,
                user_id=first_user_id,
                owner_field="athlete_id",
            )
            == 1
        )
        assert (
            await scalar_count(
                session,
                Workout,
                user_id=second_user_id,
                owner_field="athlete_id",
            )
            == 1
        )
        assert (
            await scalar_count(
                session,
                AthleteBaseline,
                user_id=first_user_id,
            )
            == 1
        )
        assert (
            await scalar_count(
                session,
                AthleteBaseline,
                user_id=second_user_id,
            )
            == 0
        )
        onboardings = {
            onboarding.user_id: onboarding
            for onboarding in (await session.scalars(select(OnboardingSession))).all()
        }
        assert (
            onboardings[first_user_id].current_step
            is OnboardingStep.FILE_IMPORT_COMPLETE
        )
        assert (
            onboardings[second_user_id].current_step
            is OnboardingStep.FILE_IMPORT_WAITING
        )


@pytest.mark.asyncio
async def test_bot_startup_recovery_recovers_all_prior_process_jobs(
    persistence: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    tmp_path: Path,
) -> None:
    _, factory = persistence
    recent_owner = identity(9110)
    expired_owner = identity(9111)
    recent_user_id = await stage_user(factory, owner=recent_owner)
    expired_user_id = await stage_user(factory, owner=expired_owner)
    async with factory.begin() as session:
        onboardings = {
            item.user_id: item
            for item in (await session.scalars(select(OnboardingSession))).all()
        }
        for user_id, started_at in (
            (recent_user_id, NOW - timedelta(minutes=5)),
            (expired_user_id, NOW - timedelta(minutes=31)),
        ):
            onboarding = onboardings[user_id]
            onboarding.current_step = OnboardingStep.FILE_IMPORT_PROCESSING
            session.add(
                AppleHealthImportJob(
                    user_id=user_id,
                    onboarding_session_id=onboarding.id,
                    telegram_file_id=f"file-{user_id}",
                    telegram_file_unique_id=f"unique-{user_id}",
                    display_filename="workout.tcx",
                    file_format=TrainingFileFormat.TCX,
                    context=TrainingImportContext.ONBOARDING,
                    status=AppleHealthImportStatus.PROCESSING,
                    started_at=started_at,
                )
            )

    recovered = await service(
        factory,
        temp_dir=tmp_path / "temporary",
    ).recover_stale_work()

    assert recovered == 2
    async with factory() as session:
        jobs = {
            job.user_id: job
            for job in (await session.scalars(select(AppleHealthImportJob))).all()
        }
        onboardings = {
            item.user_id: item
            for item in (await session.scalars(select(OnboardingSession))).all()
        }
    assert jobs[recent_user_id].status is AppleHealthImportStatus.FAILED
    assert jobs[recent_user_id].safe_error_code == "import_interrupted"
    assert (
        onboardings[recent_user_id].current_step is OnboardingStep.FILE_IMPORT_WAITING
    )
    assert jobs[expired_user_id].status is AppleHealthImportStatus.FAILED
    assert jobs[expired_user_id].safe_error_code == "import_interrupted"
    assert (
        onboardings[expired_user_id].current_step is OnboardingStep.FILE_IMPORT_WAITING
    )


@pytest.mark.asyncio
async def test_cancelled_job_cannot_persist_activity_after_parsing(
    persistence: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    tmp_path: Path,
) -> None:
    _, factory = persistence
    owner = identity(9112)
    user_id = await stage_user(factory, owner=owner)
    import_service = service(factory, temp_dir=tmp_path / "temporary")
    tcx = write_tcx(
        tmp_path / "cancelled.tcx",
        activity_id="2026-07-28T11:00:00Z",
    )

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
            file_size=tcx.stat().st_size,
            update_id=9001,
        ),
        download=download,
        progress=progress,
    )

    assert outcome.status is AppleHealthImportStatus.CANCELLED
    assert list((tmp_path / "temporary").iterdir()) == []
    async with factory() as session:
        assert (
            await scalar_count(
                session,
                Workout,
                user_id=user_id,
                owner_field="athlete_id",
            )
            == 0
        )
        assert await scalar_count(session, AthleteBaseline, user_id=user_id) == 0
        onboarding = await session.scalar(
            select(OnboardingSession).where(OnboardingSession.user_id == user_id)
        )
        assert onboarding is not None
        assert onboarding.current_step is OnboardingStep.FILE_IMPORT_WAITING


@pytest.mark.asyncio
async def test_actual_download_size_is_bounded_and_temp_metadata_is_cleared(
    persistence: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    tmp_path: Path,
) -> None:
    _, factory = persistence
    owner = identity(9113)
    user_id = await stage_user(factory, owner=owner)
    temp_dir = tmp_path / "temporary"
    import_service = service(
        factory,
        temp_dir=temp_dir,
        max_size_mb=1,
    )

    async def oversized_download(destination: Path) -> None:
        await asyncio.to_thread(
            destination.write_bytes,
            b"x" * (1024 * 1024 + 1),
        )

    outcome = await import_service.process_upload(
        identity=owner,
        document=TelegramDocumentUpload(
            file_id="oversized",
            file_unique_id="oversized-unique",
            display_filename="claimed-small.tcx",
            file_size=None,
            update_id=9002,
        ),
        download=oversized_download,
        progress=lambda _stage: _async_noop(),
    )

    assert outcome.status is AppleHealthImportStatus.FAILED
    assert outcome.safe_error_code == "training_file_size_exceeded"
    assert list(temp_dir.iterdir()) == []
    async with factory() as session:
        job = await session.scalar(
            select(AppleHealthImportJob).where(AppleHealthImportJob.user_id == user_id)
        )
        assert job is not None
        assert job.temporary_path is None


@pytest.mark.asyncio
async def test_restart_recovery_deletes_recorded_temp_file_and_unblocks_onboarding(
    persistence: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    tmp_path: Path,
) -> None:
    _, factory = persistence
    owner = identity(9114)
    user_id = await stage_user(factory, owner=owner)
    temp_dir = tmp_path / "temporary"
    temp_dir.mkdir()
    recorded_path = temp_dir / "training-import-crashed.upload"
    recorded_path.write_bytes(b"synthetic private workout content")
    async with factory.begin() as session:
        onboarding = await session.scalar(
            select(OnboardingSession).where(OnboardingSession.user_id == user_id)
        )
        assert onboarding is not None
        onboarding.current_step = OnboardingStep.FILE_IMPORT_PROCESSING
        session.add(
            AppleHealthImportJob(
                user_id=user_id,
                onboarding_session_id=onboarding.id,
                telegram_file_id="crashed",
                telegram_file_unique_id="crashed-unique",
                display_filename="workout.tcx",
                temporary_path=str(recorded_path),
                file_format=TrainingFileFormat.TCX,
                context=TrainingImportContext.ONBOARDING,
                status=AppleHealthImportStatus.PROCESSING,
                started_at=NOW,
            )
        )

    recovered = await service(
        factory,
        temp_dir=temp_dir,
    ).recover_stale_work()

    assert recovered == 1
    assert not recorded_path.exists()
    async with factory() as session:
        job = await session.scalar(
            select(AppleHealthImportJob).where(AppleHealthImportJob.user_id == user_id)
        )
        onboarding = await session.scalar(
            select(OnboardingSession).where(OnboardingSession.user_id == user_id)
        )
        assert job is not None
        assert job.status is AppleHealthImportStatus.FAILED
        assert job.temporary_path is None
        assert onboarding is not None
        assert onboarding.current_step is OnboardingStep.FILE_IMPORT_WAITING


@pytest.mark.asyncio
async def test_daily_import_preserves_existing_strava_baseline_choice(
    persistence: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    tmp_path: Path,
) -> None:
    _, factory = persistence
    owner = identity(9115)
    user_id = await stage_user(factory, owner=owner, completed=True)
    async with factory.begin() as session:
        user = await session.get(User, user_id)
        assert user is not None
        user.status = UserStatus.BASELINE_IMPORTING
        session.add(
            BaselinePreference(
                user_id=user_id,
                selected_source=BaselineSource.STRAVA,
                status=BaselinePreferenceStatus.IMPORTING,
            )
        )
    import_service = service(factory, temp_dir=tmp_path / "temporary")
    tcx = write_tcx(
        tmp_path / "strava-choice.tcx",
        activity_id="2026-07-28T12:00:00Z",
    )

    outcome, _ = await upload(
        import_service,
        owner=owner,
        source=tcx,
        update_id=9003,
    )

    assert outcome.status is AppleHealthImportStatus.SUCCEEDED
    assert outcome.baseline_limited is True
    async with factory() as session:
        preference = await session.scalar(
            select(BaselinePreference).where(BaselinePreference.user_id == user_id)
        )
        baseline = await session.scalar(
            select(AthleteBaseline)
            .where(AthleteBaseline.user_id == user_id)
            .order_by(AthleteBaseline.version.desc())
        )
        user = await session.get(User, user_id)
        assert preference is not None
        assert preference.selected_source is BaselineSource.STRAVA
        assert preference.status is BaselinePreferenceStatus.IMPORTING
        assert baseline is not None
        assert baseline.source is BaselineSource.STRAVA
        assert user is not None
        assert user.status is UserStatus.BASELINE_IMPORTING


async def _async_noop() -> None:
    return None
