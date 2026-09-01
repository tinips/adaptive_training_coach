"""Read-only workout-history dashboard behavior."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.db.base import Base
from app.domain.enums import ActivitySource, Discipline, RunningType, StrengthType
from app.repositories.activities import TrainingActivityRepository
from app.repositories.users import UserRepository
from app.schemas.common import TelegramIdentity
from app.schemas.workout_history import WorkoutHistoryQuery
from app.schemas.workouts import (
    RunningWorkoutDetailsData,
    StrengthWorkoutDetailsData,
    WorkoutCreate,
)
from app.services.workout_history import WorkoutHistoryService


@pytest_asyncio.fixture
async def database() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    yield factory
    await engine.dispose()


def _identity(telegram_user_id: int = 701) -> TelegramIdentity:
    return TelegramIdentity(
        telegram_user_id=telegram_user_id,
        telegram_username="athlete",
        first_name="Athlete",
        language_code="en",
    )


async def _user(
    session: AsyncSession, telegram_user_id: int, *, timezone: str = "Europe/Madrid"
) -> UUID:
    user, _ = await UserRepository(session).get_or_create(
        telegram_user_id=telegram_user_id,
        telegram_username="athlete",
        first_name="Athlete",
        timezone=timezone,
    )
    return user.id


async def _running(
    session: AsyncSession,
    *,
    athlete_id: UUID,
    started_at: datetime,
    title: str,
) -> None:
    await TrainingActivityRepository(session).create_manual(
        WorkoutCreate(
            athlete_id=athlete_id,
            discipline=Discipline.RUNNING,
            started_at=started_at,
            duration_seconds=1800,
            source=ActivitySource.MANUAL,
            title=title,
            notes="private note",
            details=RunningWorkoutDetailsData(
                running_type=RunningType.OUTDOOR,
                distance_meters=5000,
                moving_duration_seconds=1800,
                calories_kcal=420,
                average_heart_rate=151,
            ),
        )
    )


@pytest.mark.asyncio
async def test_history_is_owned_filtered_and_timezone_aware(
    database: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 9, 1, 8, tzinfo=UTC)
    async with database.begin() as session:
        owner_id = await _user(session, 701)
        other_id = await _user(session, 702)
        await _running(
            session,
            athlete_id=owner_id,
            started_at=now,
            title="Morning run",
        )
        await _running(
            session,
            athlete_id=owner_id,
            started_at=now - timedelta(days=1),
            title="Easy run",
        )
        await _running(
            session,
            athlete_id=other_id,
            started_at=now,
            title="Other athlete",
        )

    result = await WorkoutHistoryService(database).query(
        identity=_identity(),
        request=WorkoutHistoryQuery(
            start_date=date(2026, 8, 30),
            end_date=date(2026, 9, 1),
            discipline=Discipline.RUNNING,
        ),
    )

    assert result.timezone == "Europe/Madrid"
    assert result.totals.session_count == 2
    assert result.totals.duration_seconds == 3600
    assert result.totals.distance_meters == 10_000
    assert [item.title for item in result.workouts] == ["Morning run", "Easy run"]
    assert result.workouts[0].started_at.hour == 10
    assert result.workouts[0].average_heart_rate == 151
    assert "notes" not in result.workouts[0].model_dump()


@pytest.mark.asyncio
async def test_history_paginates_and_chart_keeps_missing_distance_sessions(
    database: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 9, 1, 8, tzinfo=UTC)
    async with database.begin() as session:
        owner_id = await _user(session, 701)
        for offset in range(20):
            await _running(
                session,
                athlete_id=owner_id,
                started_at=now - timedelta(minutes=offset),
                title=f"Run {offset}",
            )
        await TrainingActivityRepository(session).create_manual(
            WorkoutCreate(
                athlete_id=owner_id,
                discipline=Discipline.STRENGTH,
                started_at=now - timedelta(minutes=21),
                duration_seconds=2700,
                source=ActivitySource.MANUAL,
                title="Strength",
                details=StrengthWorkoutDetailsData(
                    strength_type=StrengthType.GYM,
                    exercises_jsonb=[],
                ),
            )
        )

    service = WorkoutHistoryService(database)
    request = WorkoutHistoryQuery(
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 1),
    )
    first = await service.query(identity=_identity(), request=request)
    second = await service.query(
        identity=_identity(),
        request=request.model_copy(update={"cursor": first.next_cursor}),
    )

    assert len(first.workouts) == 20
    assert first.next_cursor is not None
    assert len(second.workouts) == 1
    assert second.workouts[0].discipline is Discipline.STRENGTH
    assert first.totals.session_count == 21
    assert first.totals.distance_meters == 100_000
    bucket = first.chart_buckets[0]
    assert bucket.duration_seconds_by_discipline["STRENGTH"] == 2700
    assert "STRENGTH" not in bucket.distance_meters_by_discipline
