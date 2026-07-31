"""Central manual-workout creation and joined serialization tests."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.db.base import Base
from app.domain.enums import (
    ActivitySource,
    CyclingType,
    Discipline,
    HikingType,
    RunningType,
    StrengthType,
    SwimmingEnvironment,
    SwimmingStroke,
)
from app.repositories.activities import TrainingActivityRepository
from app.repositories.users import UserRepository
from app.schemas.workouts import (
    CyclingWorkoutDetailsData,
    CyclingWorkoutDetailsRead,
    HikingWorkoutDetailsData,
    HikingWorkoutDetailsRead,
    OtherWorkoutDetailsData,
    OtherWorkoutDetailsRead,
    PoolSwimmingDetailsData,
    RunningWorkoutDetailsData,
    RunningWorkoutDetailsRead,
    StrengthWorkoutDetailsData,
    StrengthWorkoutDetailsRead,
    SwimmingWorkoutDetailsData,
    SwimmingWorkoutDetailsRead,
    WorkoutCreate,
    WorkoutDetailsData,
    main_detail,
)

NOW = datetime(2026, 7, 30, 9, tzinfo=UTC)


@pytest_asyncio.fixture
async def database() -> AsyncIterator[
    tuple[AsyncEngine, async_sessionmaker[AsyncSession]]
]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
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
        telegram_username=f"athlete_{telegram_user_id}",
        first_name="Athlete",
    )
    assert created
    return user.id


DISCIPLINE_CASES: tuple[
    tuple[Discipline, WorkoutDetailsData, type[object]],
    ...,
] = (
    (
        Discipline.RUNNING,
        RunningWorkoutDetailsData(
            running_type=RunningType.OUTDOOR,
            distance_meters=5000,
            moving_duration_seconds=1800,
        ),
        RunningWorkoutDetailsRead,
    ),
    (
        Discipline.CYCLING,
        CyclingWorkoutDetailsData(
            cycling_type=CyclingType.ROAD,
            distance_meters=20_000,
            moving_duration_seconds=3600,
        ),
        CyclingWorkoutDetailsRead,
    ),
    (
        Discipline.HIKING,
        HikingWorkoutDetailsData(
            hiking_type=HikingType.HIKING,
            distance_meters=8000,
            moving_duration_seconds=7200,
            pack_weight_kg=6,
        ),
        HikingWorkoutDetailsRead,
    ),
    (
        Discipline.SWIMMING,
        SwimmingWorkoutDetailsData(
            swimming_environment=SwimmingEnvironment.OPEN_WATER,
            distance_meters=1500,
            moving_duration_seconds=1800,
        ),
        SwimmingWorkoutDetailsRead,
    ),
    (
        Discipline.STRENGTH,
        StrengthWorkoutDetailsData(
            strength_type=StrengthType.GYM,
            exercises_jsonb=[],
        ),
        StrengthWorkoutDetailsRead,
    ),
    (
        Discipline.OTHER,
        OtherWorkoutDetailsData(
            activity_name="Padel",
            activity_description="90-minute doubles match",
            raw_sport="padel",
            metrics_jsonb={"court": "outdoor"},
        ),
        OtherWorkoutDetailsRead,
    ),
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("discipline", "details", "read_type"),
    [
        pytest.param(discipline, details, read_type, id=discipline.value.lower())
        for discipline, details, read_type in DISCIPLINE_CASES
    ],
)
async def test_create_manual_persists_every_discipline_and_serializes_details(
    database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    discipline: Discipline,
    details: WorkoutDetailsData,
    read_type: type[object],
) -> None:
    _, factory = database
    async with factory.begin() as session:
        user_id = await create_user(
            session,
            telegram_user_id=20_000 + list(Discipline).index(discipline),
        )
        repository = TrainingActivityRepository(session)
        workout = await repository.create_manual(
            WorkoutCreate(
                athlete_id=user_id,
                discipline=discipline,
                started_at=NOW,
                duration_seconds=5400,
                source=ActivitySource.MANUAL,
                title=f"Synthetic {discipline.value.lower()}",
                notes="Validated manual entry",
                details=details,
            )
        )
        serialized = await repository.serialize_owned(
            user_id=user_id,
            workout_id=workout.id,
        )

    assert workout.external_id is None
    assert workout.athlete_id == user_id
    assert workout.discipline is discipline
    assert main_detail(workout).workout_id == workout.id  # type: ignore[attr-defined]
    assert isinstance(serialized.details, read_type)
    assert serialized.details.workout_id == workout.id
    generic = serialized.model_dump(exclude={"details"})
    assert set(generic) == {
        "id",
        "athlete_id",
        "discipline",
        "started_at",
        "duration_seconds",
        "source",
        "external_id",
        "title",
        "notes",
        "created_at",
        "updated_at",
    }
    assert "distance_meters" not in generic


@pytest.mark.asyncio
@pytest.mark.parametrize("running_type", list(RunningType))
async def test_create_manual_supports_every_running_type(
    database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    running_type: RunningType,
) -> None:
    _, factory = database
    async with factory.begin() as session:
        user_id = await create_user(
            session,
            telegram_user_id=21_000 + list(RunningType).index(running_type),
        )
        workout = await TrainingActivityRepository(session).create_manual(
            WorkoutCreate(
                athlete_id=user_id,
                discipline=Discipline.RUNNING,
                started_at=NOW,
                duration_seconds=3600,
                source=ActivitySource.MANUAL,
                details=RunningWorkoutDetailsData(
                    running_type=running_type,
                    distance_meters=10_000,
                    moving_duration_seconds=3600,
                ),
            )
        )

    assert workout.running_details is not None
    assert workout.running_details.running_type is running_type
    assert workout.running_details.average_pace_seconds_per_km == 360


@pytest.mark.asyncio
async def test_stationary_cycling_has_no_indoor_or_power_fields(
    database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = database
    async with factory.begin() as session:
        user_id = await create_user(session, telegram_user_id=22_001)
        workout = await TrainingActivityRepository(session).create_manual(
            WorkoutCreate(
                athlete_id=user_id,
                discipline=Discipline.CYCLING,
                started_at=NOW,
                duration_seconds=3600,
                source=ActivitySource.MANUAL,
                details=CyclingWorkoutDetailsData(
                    cycling_type=CyclingType.STATIONARY,
                    distance_meters=30_000,
                    moving_duration_seconds=3600,
                ),
            )
        )

    details = workout.cycling_details
    assert details is not None
    assert details.cycling_type is CyclingType.STATIONARY
    assert details.average_speed_kph == 30
    assert not hasattr(details, "is_indoor")
    assert not any(
        "watt" in column.name or "power" in column.name
        for column in details.__table__.columns
    )


@pytest.mark.asyncio
async def test_pool_and_open_water_swims_persist_their_distinct_aggregates(
    database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = database
    async with factory.begin() as session:
        user_id = await create_user(session, telegram_user_id=23_001)
        repository = TrainingActivityRepository(session)
        pool = await repository.create_manual(
            WorkoutCreate(
                athlete_id=user_id,
                discipline=Discipline.SWIMMING,
                started_at=NOW,
                duration_seconds=1200,
                source=ActivitySource.MANUAL,
                title="Pool intervals",
                details=SwimmingWorkoutDetailsData(
                    swimming_environment=SwimmingEnvironment.POOL,
                    distance_meters=1000,
                    moving_duration_seconds=1200,
                    pool_details=PoolSwimmingDetailsData(
                        pool_length_meters=25,
                        total_lengths=40,
                        primary_stroke=SwimmingStroke.FREESTYLE,
                    ),
                ),
            )
        )
        open_water = await repository.create_manual(
            WorkoutCreate(
                athlete_id=user_id,
                discipline=Discipline.SWIMMING,
                started_at=NOW + timedelta(days=1),
                duration_seconds=1800,
                source=ActivitySource.MANUAL,
                title="Sea swim",
                details=SwimmingWorkoutDetailsData(
                    swimming_environment=SwimmingEnvironment.OPEN_WATER,
                    distance_meters=1500,
                    moving_duration_seconds=1800,
                ),
            )
        )
        serialized_pool = await repository.serialize_owned(
            user_id=user_id,
            workout_id=pool.id,
        )
        serialized_open_water = await repository.serialize_owned(
            user_id=user_id,
            workout_id=open_water.id,
        )

    assert pool.swimming_details is not None
    assert pool.swimming_details.pool_details is not None
    assert pool.swimming_details.pool_details.pool_length_meters == 25
    assert open_water.swimming_details is not None
    assert open_water.swimming_details.pool_details is None
    assert isinstance(serialized_pool.details, SwimmingWorkoutDetailsRead)
    assert serialized_pool.details.pool_details is not None
    assert isinstance(serialized_open_water.details, SwimmingWorkoutDetailsRead)
    assert serialized_open_water.details.pool_details is None


@pytest.mark.asyncio
async def test_manual_other_preserves_understandable_and_raw_activity_data(
    database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = database
    async with factory.begin() as session:
        user_id = await create_user(session, telegram_user_id=24_001)
        workout = await TrainingActivityRepository(session).create_manual(
            WorkoutCreate(
                athlete_id=user_id,
                discipline=Discipline.OTHER,
                started_at=NOW,
                duration_seconds=5400,
                source=ActivitySource.MANUAL,
                title="Padel match",
                details=OtherWorkoutDetailsData(
                    activity_name="Padel",
                    activity_description="90-minute doubles match",
                    raw_sport="padel",
                    raw_sub_sport="doubles",
                    average_heart_rate=132,
                    metrics_jsonb={"sets_won": 2},
                ),
            )
        )

    assert workout.other_details is not None
    assert workout.other_details.activity_name == "Padel"
    assert workout.other_details.raw_sport == "padel"
    assert workout.other_details.raw_sub_sport == "doubles"
    assert workout.other_details.metrics_jsonb == {"sets_won": 2}
