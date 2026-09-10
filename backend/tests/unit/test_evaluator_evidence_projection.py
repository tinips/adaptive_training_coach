"""Evaluator evidence projection: numeric power/speed/HR, quality, precedence.

Test-first step 4 of docs/briefs/backlog/first-week-evaluator.md.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.db.base import Base
from app.db.models import User, WorkoutHeartRateObservation
from app.domain.enums import (
    ActivitySource,
    CyclingType,
    Discipline,
    HeartRateTemporalQuality,
    RunningType,
    StrengthType,
    SwimmingEnvironment,
)
from app.repositories.activities import TrainingActivityRepository
from app.repositories.users import UserRepository
from app.schemas.workouts import (
    CyclingWorkoutDetailsData,
    RunningWorkoutDetailsData,
    StrengthWorkoutDetailsData,
    SwimmingWorkoutDetailsData,
    WorkoutCreate,
)
from app.services.weekly_evaluation.evidence import build_evaluator_workout_evidence


@pytest_asyncio.fixture
async def database() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine: AsyncEngine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    await engine.dispose()


async def _make_user(session: AsyncSession) -> User:
    user, _ = await UserRepository(session).get_or_create(
        telegram_user_id=100, telegram_username="ada", first_name="Ada"
    )
    return user


@pytest.mark.asyncio
async def test_cycling_power_and_speed_become_numeric_evidence(
    database: async_sessionmaker[AsyncSession],
) -> None:
    async with database() as session:
        athlete = await _make_user(session)
        workout = await TrainingActivityRepository(session).create_manual(
            WorkoutCreate(
                athlete_id=athlete.id,
                discipline=Discipline.CYCLING,
                started_at=datetime(2026, 9, 15, tzinfo=UTC),
                duration_seconds=3600,
                source=ActivitySource.MANUAL,
                details=CyclingWorkoutDetailsData(
                    cycling_type=CyclingType.ROAD,
                    distance_meters=30000.0,
                    moving_duration_seconds=3500,
                    average_power_watts=210.0,
                    max_power_watts=280.0,
                    average_heart_rate=142.0,
                    max_heart_rate=158.0,
                ),
            )
        )
        await session.commit()

        evidence = await build_evaluator_workout_evidence(session, workout)

        assert evidence.average_power_watts == 210.0
        assert evidence.max_power_watts == 280.0
        assert evidence.average_speed_kph is not None  # derived at write time
        assert evidence.average_heart_rate_bpm == 142.0
        assert evidence.max_heart_rate_bpm == 158.0
        assert evidence.distance_meters == 30000.0


@pytest.mark.asyncio
async def test_moving_duration_is_preferred_source_over_elapsed(
    database: async_sessionmaker[AsyncSession],
) -> None:
    async with database() as session:
        athlete = await _make_user(session)
        workout = await TrainingActivityRepository(session).create_manual(
            WorkoutCreate(
                athlete_id=athlete.id,
                discipline=Discipline.RUNNING,
                started_at=datetime(2026, 9, 15, tzinfo=UTC),
                duration_seconds=2700,  # elapsed, includes a paused stretch
                source=ActivitySource.MANUAL,
                details=RunningWorkoutDetailsData(
                    running_type=RunningType.OUTDOOR,
                    distance_meters=8000.0,
                    moving_duration_seconds=2400,
                ),
            )
        )
        await session.commit()

        evidence = await build_evaluator_workout_evidence(session, workout)

        assert evidence.duration_source == "MOVING"
        assert evidence.moving_duration_seconds == 2400
        assert evidence.duration_seconds == 2700  # elapsed is still retained


@pytest.mark.asyncio
async def test_elapsed_duration_is_the_source_when_moving_duration_is_absent(
    database: async_sessionmaker[AsyncSession],
) -> None:
    async with database() as session:
        athlete = await _make_user(session)
        workout = await TrainingActivityRepository(session).create_manual(
            WorkoutCreate(
                athlete_id=athlete.id,
                discipline=Discipline.STRENGTH,
                started_at=datetime(2026, 9, 15, tzinfo=UTC),
                duration_seconds=1800,
                source=ActivitySource.MANUAL,
                details=StrengthWorkoutDetailsData(strength_type=StrengthType.GYM),
            )
        )
        await session.commit()

        evidence = await build_evaluator_workout_evidence(session, workout)

        assert evidence.duration_source == "ELAPSED"
        assert evidence.moving_duration_seconds is None
        assert evidence.distance_meters is None  # strength has no distance


@pytest.mark.asyncio
async def test_canonical_running_pace_is_the_stored_value(
    database: async_sessionmaker[AsyncSession],
) -> None:
    async with database() as session:
        athlete = await _make_user(session)
        workout = await TrainingActivityRepository(session).create_manual(
            WorkoutCreate(
                athlete_id=athlete.id,
                discipline=Discipline.RUNNING,
                started_at=datetime(2026, 9, 15, tzinfo=UTC),
                duration_seconds=2400,
                source=ActivitySource.MANUAL,
                details=RunningWorkoutDetailsData(
                    running_type=RunningType.OUTDOOR,
                    distance_meters=8000.0,
                    moving_duration_seconds=2400,
                ),
            )
        )
        await session.commit()

        evidence = await build_evaluator_workout_evidence(session, workout)

        # 2400s / 8km = 300 s/km, derived and stored at write time.
        assert evidence.canonical_pace_seconds_per_km == pytest.approx(300.0)
        assert evidence.canonical_pace_seconds_per_100m is None


@pytest.mark.asyncio
async def test_canonical_swim_pace_is_the_stored_value(
    database: async_sessionmaker[AsyncSession],
) -> None:
    async with database() as session:
        athlete = await _make_user(session)
        workout = await TrainingActivityRepository(session).create_manual(
            WorkoutCreate(
                athlete_id=athlete.id,
                discipline=Discipline.SWIMMING,
                started_at=datetime(2026, 9, 15, tzinfo=UTC),
                duration_seconds=1800,
                source=ActivitySource.MANUAL,
                details=SwimmingWorkoutDetailsData(
                    swimming_environment=SwimmingEnvironment.OPEN_WATER,
                    distance_meters=1500.0,
                    moving_duration_seconds=1800,
                ),
            )
        )
        await session.commit()

        evidence = await build_evaluator_workout_evidence(session, workout)

        # 1800s / 15 (100m units) = 120 s/100m.
        assert evidence.canonical_pace_seconds_per_100m == pytest.approx(120.0)
        assert evidence.canonical_pace_seconds_per_km is None


@pytest.mark.asyncio
async def test_missing_metrics_remain_none_not_zero(
    database: async_sessionmaker[AsyncSession],
) -> None:
    async with database() as session:
        athlete = await _make_user(session)
        workout = await TrainingActivityRepository(session).create_manual(
            WorkoutCreate(
                athlete_id=athlete.id,
                discipline=Discipline.RUNNING,
                started_at=datetime(2026, 9, 15, tzinfo=UTC),
                duration_seconds=2400,
                source=ActivitySource.MANUAL,
                details=RunningWorkoutDetailsData(running_type=RunningType.OUTDOOR),
            )
        )
        await session.commit()

        evidence = await build_evaluator_workout_evidence(session, workout)

        assert evidence.distance_meters is None
        assert evidence.canonical_pace_seconds_per_km is None
        assert evidence.average_heart_rate_bpm is None
        assert evidence.max_heart_rate_bpm is None


@pytest.mark.asyncio
async def test_timestamped_hr_observations_retain_their_quality(
    database: async_sessionmaker[AsyncSession],
) -> None:
    async with database() as session:
        athlete = await _make_user(session)
        workout = await TrainingActivityRepository(session).create_manual(
            WorkoutCreate(
                athlete_id=athlete.id,
                discipline=Discipline.RUNNING,
                started_at=datetime(2026, 9, 15, 8, tzinfo=UTC),
                duration_seconds=2400,
                source=ActivitySource.MANUAL,
                details=RunningWorkoutDetailsData(
                    running_type=RunningType.OUTDOOR,
                    distance_meters=8000.0,
                    moving_duration_seconds=2400,
                ),
            )
        )
        session.add(
            WorkoutHeartRateObservation(
                user_id=athlete.id,
                workout_id=workout.id,
                source=ActivitySource.MANUAL,
                source_record_key=str(uuid.uuid4()),
                started_at=datetime(2026, 9, 15, 8, 5, tzinfo=UTC),
                ended_at=datetime(2026, 9, 15, 8, 6, tzinfo=UTC),
                beats_per_minute=132.0,
                temporal_quality=HeartRateTemporalQuality.EXACT_SAMPLE,
            )
        )
        await session.commit()

        evidence = await build_evaluator_workout_evidence(session, workout)

        assert len(evidence.heart_rate_observations) == 1
        observation = evidence.heart_rate_observations[0]
        assert observation.beats_per_minute == 132.0
        assert observation.temporal_quality == HeartRateTemporalQuality.EXACT_SAMPLE


@pytest.mark.asyncio
async def test_missing_discipline_detail_row_is_flagged(
    database: async_sessionmaker[AsyncSession],
) -> None:
    """A data anomaly (endurance workout with no detail row) is flagged."""

    from app.db.models import Workout

    async with database() as session:
        athlete = await _make_user(session)
        workout = Workout(
            athlete_id=athlete.id,
            discipline=Discipline.RUNNING,
            started_at=datetime(2026, 9, 15, tzinfo=UTC),
            duration_seconds=2400,
            source=ActivitySource.MANUAL,
        )
        session.add(workout)
        await session.flush()
        await session.commit()

        evidence = await build_evaluator_workout_evidence(session, workout)

        assert "MISSING_DISCIPLINE_DETAIL" in evidence.quality_flags
        assert evidence.distance_meters is None
