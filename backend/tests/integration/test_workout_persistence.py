"""Relational invariants for generic workouts, details, and feedback."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.db.base import Base
from app.db.models import (
    ActivityFeedback,
    ActivitySourceLink,
    AppleHealthImportJob,
    CyclingWorkoutDetails,
    HikingWorkoutDetails,
    OtherWorkoutDetails,
    PoolSwimmingDetails,
    RunningWorkoutDetails,
    StrengthWorkoutDetails,
    SwimmingWorkoutDetails,
    User,
    Workout,
)
from app.domain.enums import (
    ActivitySource,
    AppleHealthImportStatus,
    Discipline,
    RunningType,
    SwimmingEnvironment,
)
from app.integrations.apple_health.models import ParsedWorkout
from app.repositories.activities import TrainingActivityRepository
from app.repositories.apple_health import AppleHealthRepository
from app.repositories.errors import OwnedRecordNotFoundError
from app.repositories.users import UserRepository
from app.schemas.strava import NormalizedStravaActivity
from app.schemas.workouts import (
    PoolSwimmingDetailsData,
    RunningWorkoutDetailsData,
    SwimmingWorkoutDetailsData,
    WorkoutCreate,
)

NOW = datetime(2026, 7, 30, 10, tzinfo=UTC)


@pytest_asyncio.fixture
async def database() -> AsyncIterator[
    tuple[AsyncEngine, async_sessionmaker[AsyncSession]]
]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def enable_foreign_keys(dbapi_connection: object, _: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    yield engine, factory
    await engine.dispose()


async def create_user(
    session: AsyncSession,
    *,
    telegram_user_id: int,
) -> uuid.UUID:
    user, created = await UserRepository(session).get_or_create(
        telegram_user_id=telegram_user_id,
        telegram_username=None,
        first_name="Athlete",
    )
    assert created
    return user.id


def test_generic_workout_columns_are_exact_and_details_are_one_to_one() -> None:
    assert set(Workout.__table__.columns.keys()) == {
        "athlete_id",
        "discipline",
        "started_at",
        "duration_seconds",
        "source",
        "external_id",
        "title",
        "notes",
        "id",
        "created_at",
        "updated_at",
    }
    forbidden_generic_fields = {
        "distance_meters",
        "moving_duration_seconds",
        "average_heart_rate",
        "calories_kcal",
        "elevation_gain_meters",
        "pace",
        "speed",
        "cadence",
        "power",
        "watts",
        "mobility_done",
        "reported_rpe",
    }
    assert forbidden_generic_fields.isdisjoint(Workout.__table__.columns.keys())

    for model in (
        RunningWorkoutDetails,
        CyclingWorkoutDetails,
        HikingWorkoutDetails,
        SwimmingWorkoutDetails,
        StrengthWorkoutDetails,
        OtherWorkoutDetails,
    ):
        workout_id = model.__table__.c.workout_id
        assert workout_id.primary_key
        foreign_key = next(iter(workout_id.foreign_keys))
        assert foreign_key.target_fullname == "workouts.id"
        assert foreign_key.ondelete == "CASCADE"

    pool_workout_id = PoolSwimmingDetails.__table__.c.workout_id
    assert pool_workout_id.primary_key
    assert {
        foreign_key.target_fullname: foreign_key.ondelete
        for foreign_key in pool_workout_id.foreign_keys
    } == {
        "swimming_workout_details.workout_id": "CASCADE",
        "workouts.id": "CASCADE",
    }
    assert "is_indoor" not in CyclingWorkoutDetails.__table__.columns
    assert not any(
        "watt" in column.name or "power" in column.name
        for column in CyclingWorkoutDetails.__table__.columns
    )


@pytest.mark.asyncio
async def test_ownership_and_cascade_cover_pool_details_and_feedback(
    database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = database
    async with factory.begin() as session:
        owner_id = await create_user(session, telegram_user_id=31_001)
        other_id = await create_user(session, telegram_user_id=31_002)
        repository = TrainingActivityRepository(session)
        workout = await repository.create_manual(
            WorkoutCreate(
                athlete_id=owner_id,
                discipline=Discipline.SWIMMING,
                started_at=NOW,
                duration_seconds=1200,
                source=ActivitySource.MANUAL,
                details=SwimmingWorkoutDetailsData(
                    swimming_environment=SwimmingEnvironment.POOL,
                    distance_meters=1000,
                    moving_duration_seconds=1200,
                    pool_details=PoolSwimmingDetailsData(
                        pool_length_meters=25,
                        total_lengths=40,
                    ),
                ),
            )
        )
        workout_id = workout.id
        session.add(
            ActivityFeedback(
                user_id=owner_id,
                workout_id=workout_id,
                mobility_done=True,
            )
        )
        await session.flush()

        assert (
            await repository.get_owned(
                user_id=other_id,
                workout_id=workout_id,
            )
            is None
        )
        with pytest.raises(OwnedRecordNotFoundError):
            await repository.serialize_owned(
                user_id=other_id,
                workout_id=workout_id,
            )

    async with factory.begin() as session:
        persisted = await session.get(Workout, workout_id)
        assert persisted is not None
        await session.delete(persisted)

    async with factory() as session:
        assert await session.get(Workout, workout_id) is None
        assert await session.get(SwimmingWorkoutDetails, workout_id) is None
        assert await session.get(PoolSwimmingDetails, workout_id) is None
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ActivityFeedback)
                .where(ActivityFeedback.workout_id == workout_id)
            )
            == 0
        )
        assert await session.get(User, owner_id) is not None
        assert await session.get(User, other_id) is not None


@pytest.mark.asyncio
async def test_feedback_mobility_round_trips_true_false_and_null(
    database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = database
    expected: dict[uuid.UUID, bool | None] = {}
    async with factory.begin() as session:
        user_id = await create_user(session, telegram_user_id=32_001)
        repository = TrainingActivityRepository(session)
        for index, mobility_done in enumerate((True, False, None)):
            workout = await repository.create_manual(
                WorkoutCreate(
                    athlete_id=user_id,
                    discipline=Discipline.RUNNING,
                    started_at=NOW + timedelta(days=index),
                    duration_seconds=3600,
                    source=ActivitySource.MANUAL,
                    details=RunningWorkoutDetailsData(
                        running_type=RunningType.OUTDOOR,
                        distance_meters=10_000,
                        moving_duration_seconds=3600,
                    ),
                )
            )
            session.add(
                ActivityFeedback(
                    user_id=user_id,
                    workout_id=workout.id,
                    mobility_done=mobility_done,
                )
            )
            expected[workout.id] = mobility_done

    async with factory() as session:
        persisted = {
            feedback.workout_id: feedback.mobility_done
            for feedback in (
                await session.scalars(
                    select(ActivityFeedback).where(ActivityFeedback.user_id == user_id)
                )
            ).all()
        }

    assert persisted == expected


@pytest.mark.asyncio
async def test_provider_delete_is_idempotent_and_reimport_reactivates_link(
    database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = database
    normalized = NormalizedStravaActivity(
        external_id="strava-32001",
        sport=Discipline.RUNNING,
        source_sport_type="Run",
        name="Imported run",
        started_at=NOW,
        duration_seconds=3600,
        moving_time_seconds=3500,
        distance_meters=10_000,
    )
    first_deleted_at = NOW + timedelta(hours=1)

    async with factory.begin() as session:
        user_id = await create_user(session, telegram_user_id=32_002)
        repository = TrainingActivityRepository(session)
        workout, outcome = await repository.import_strava_activity(
            user_id=user_id,
            normalized=normalized,
        )
        assert outcome == "inserted"
        assert await repository.mark_source_deleted(
            user_id=user_id,
            source=ActivitySource.STRAVA,
            external_id=normalized.external_id,
            deleted_at=first_deleted_at,
        )
        assert not await repository.mark_source_deleted(
            user_id=user_id,
            source=ActivitySource.STRAVA,
            external_id=normalized.external_id,
            deleted_at=first_deleted_at + timedelta(minutes=1),
        )

        replayed, replay_outcome = await repository.import_strava_activity(
            user_id=user_id,
            normalized=normalized,
        )
        assert replayed.id == workout.id
        assert replay_outcome == "updated"
        assert replayed.source_links[0].deleted_at is None

    async with factory() as session:
        link = await session.scalar(
            select(ActivitySourceLink).where(
                ActivitySourceLink.user_id == user_id,
                ActivitySourceLink.external_id == normalized.external_id,
            )
        )
        assert link is not None
        assert link.deleted_at is None


@pytest.mark.asyncio
async def test_strength_import_preserves_metrics_outside_strength_detail(
    database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = database
    parsed = ParsedWorkout(
        source_record_key="apple-strength-1",
        source_workout_type="HKWorkoutActivityTypeFunctionalStrengthTraining",
        discipline=Discipline.STRENGTH,
        source_name="Watch",
        source_version=None,
        device=None,
        started_at=NOW,
        ended_at=NOW + timedelta(minutes=45),
        duration_seconds=2700,
        distance_meters=123.0,
        calories_kcal=321.0,
    )

    async with factory.begin() as session:
        user_id = await create_user(session, telegram_user_id=32_003)
        workout, outcome = await TrainingActivityRepository(
            session
        ).import_apple_workout(
            user_id=user_id,
            workout=parsed,
            file_sha256="a" * 64,
            import_job_id=None,
        )

        assert outcome == "inserted"
        assert workout.strength_details is not None
        assert workout.strength_details.exercises_jsonb == []
        assert len(workout.source_links) == 1
        metadata = workout.source_links[0].source_metadata_jsonb
        assert metadata is not None
        assert metadata["distance_meters"] == 123.0
        assert metadata["calories_kcal"] == 321.0


@pytest.mark.asyncio
async def test_legacy_apple_upsert_preserves_unified_file_and_job_provenance(
    database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = database
    parsed = ParsedWorkout(
        source_record_key="apple-running-with-job",
        source_workout_type="HKWorkoutActivityTypeRunning",
        discipline=Discipline.RUNNING,
        source_name="Watch",
        source_version=None,
        device=None,
        started_at=NOW,
        ended_at=NOW + timedelta(minutes=30),
        duration_seconds=1800,
        distance_meters=5000,
        calories_kcal=300,
    )

    async with factory.begin() as session:
        user_id = await create_user(session, telegram_user_id=32_004)
        job = AppleHealthImportJob(
            user_id=user_id,
            telegram_file_id="telegram-file",
            telegram_file_unique_id="telegram-unique",
            display_filename="export.zip",
            file_sha256="f" * 64,
            status=AppleHealthImportStatus.SUCCEEDED,
            completed_at=NOW,
        )
        session.add(job)
        await session.flush()

        workout, inserted = await TrainingActivityRepository(
            session
        ).import_apple_workout(
            user_id=user_id,
            workout=parsed,
            file_sha256=job.file_sha256,
            import_job_id=job.id,
        )
        workout.source_links[0].source_metadata_jsonb = {
            "migration_revision": "0004_discipline_workout_models",
            "legacy_activity": {
                "legacy_average_watts": 210.5,
                "legacy_raw_summary": {"provider": "legacy"},
            },
            "canonical_snapshot": {"workout": {"id": str(workout.id)}},
        }
        await session.flush()
        replayed, replay_outcome = await AppleHealthRepository(session).upsert_workout(
            user_id=user_id,
            workout=parsed,
        )

        assert inserted == "inserted"
        assert replayed.id == workout.id
        assert replay_outcome == "updated"
        assert len(replayed.source_links) == 1
        link = replayed.source_links[0]
        assert link.file_sha256 == "f" * 64
        assert link.import_job_id == job.id
        assert link.source_metadata_jsonb is not None
        assert link.source_metadata_jsonb["source_name"] == "Watch"
        migration_provenance = link.source_metadata_jsonb["migration_provenance"]
        assert isinstance(migration_provenance, dict)
        legacy_activity = migration_provenance["legacy_activity"]
        assert isinstance(legacy_activity, dict)
        assert legacy_activity["legacy_average_watts"] == 210.5
        assert legacy_activity["legacy_raw_summary"] == {"provider": "legacy"}
        latest = await AppleHealthRepository(session).latest_imported_activity(
            user_id=user_id,
        )
        assert latest is not None
        assert latest.id == workout.id
