"""Compatibility reads for profiles completed before onboarding was reduced."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import (
    AthleteProfile,
    AvailabilityRule,
    BaselinePreference,
    CoachPreference,
    GoalType,
    TrainingGoal,
)
from app.domain.enums import (
    BaselinePreferenceStatus,
    BaselineSource,
    CoachTone,
    DayOfWeek,
    DetailLevel,
    GoalPriority,
    PrimarySport,
    UserStatus,
)
from app.repositories.profiles import ProfileRepository
from app.repositories.users import UserRepository
from app.services.profiles import (
    BaselineSelectionUnavailableError,
    ProfileService,
)


@pytest_asyncio.fixture
async def database() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    yield factory
    await engine.dispose()


async def _historical_profile(
    session: AsyncSession,
    *,
    telegram_id: int,
    status: UserStatus = UserStatus.PROFILE_COMPLETED,
) -> UUID:
    user, _ = await UserRepository(session).get_or_create(
        telegram_user_id=telegram_id,
        telegram_username=f"legacy_{telegram_id}",
        first_name="Legacy Athlete",
    )
    user.status = status
    session.add_all(
        [
            AthleteProfile(
                user_id=user.id,
                age=37,
                height_cm=178.0,
                weight_kg=72.0,
                primary_sport=PrimarySport.RUNNING,
            ),
            TrainingGoal(
                user_id=user.id,
                goal_type=GoalType.MARATHON,
                event_name="Barcelona Marathon",
                event_date=date(2027, 3, 14),
                goal_priority=GoalPriority.FINISH_SAFELY,
            ),
            AvailabilityRule(
                user_id=user.id,
                day_of_week=DayOfWeek.MONDAY,
                available_minutes=60,
                is_variable=False,
            ),
            CoachPreference(
                user_id=user.id,
                tone=CoachTone.CONCISE_PRACTICAL,
                detail_level=DetailLevel.MEDIUM,
            ),
            BaselinePreference(
                user_id=user.id,
                selected_source=BaselineSource.MANUAL,
                status=BaselinePreferenceStatus.NOT_IMPLEMENTED,
            ),
        ]
    )
    await session.flush()
    return user.id


@pytest.mark.asyncio
async def test_historical_normalized_profile_remains_readable_and_owned(
    database: async_sessionmaker[AsyncSession],
) -> None:
    async with database.begin() as session:
        owner_id = await _historical_profile(session, telegram_id=7101)
        other, _ = await UserRepository(session).get_or_create(
            telegram_user_id=7102,
            telegram_username="other",
            first_name="Other",
        )
        other_id = other.id

    service = ProfileService(database)
    profile = await service.get(user_id=owner_id)

    assert profile is not None
    assert profile.primary_sport is PrimarySport.RUNNING
    assert profile.goal_type is GoalType.MARATHON
    assert profile.event_name == "Barcelona Marathon"
    assert await service.get(user_id=other_id) is None


@pytest.mark.asyncio
async def test_existing_profile_can_still_choose_pending_manual_baseline(
    database: async_sessionmaker[AsyncSession],
) -> None:
    async with database.begin() as session:
        user_id = await _historical_profile(session, telegram_id=7103)

    selected = await ProfileService(database).select_pending_baseline_source(
        user_id=user_id,
        source=BaselineSource.CALIBRATION,
    )

    assert selected.baseline_source is BaselineSource.CALIBRATION
    async with database() as session:
        bundle = await ProfileRepository(session).get_bundle(user_id=user_id)
        user = await UserRepository(session).require_by_id(user_id)
        assert bundle.baseline_preference is not None
        assert (
            bundle.baseline_preference.status
            is BaselinePreferenceStatus.NOT_IMPLEMENTED
        )
        assert user.status is UserStatus.BASELINE_PENDING


@pytest.mark.asyncio
async def test_ready_existing_profile_rejects_stale_baseline_change(
    database: async_sessionmaker[AsyncSession],
) -> None:
    async with database.begin() as session:
        user_id = await _historical_profile(
            session,
            telegram_id=7104,
            status=UserStatus.BASELINE_READY,
        )

    with pytest.raises(BaselineSelectionUnavailableError):
        await ProfileService(database).select_pending_baseline_source(
            user_id=user_id,
            source=BaselineSource.MANUAL,
        )
