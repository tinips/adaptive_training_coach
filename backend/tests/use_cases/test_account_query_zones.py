"""Zones read-model coverage: profile + baseline compose into display zones."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.db.base import Base
from app.domain.enums import AthleteGender, CoachingStyle, Discipline
from app.repositories.athlete_baselines import AthleteBaselineRepository
from app.repositories.profiles import ProfileRepository
from app.repositories.users import UserRepository
from app.schemas.baseline import (
    AthleteBaselineData,
    RunningBaseline,
    TrainingPreferences,
)
from app.schemas.common import TelegramIdentity
from app.services.accounts.service import AccountQueryService


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


def _identity() -> TelegramIdentity:
    return TelegramIdentity(
        telegram_user_id=5001, telegram_username="zones_test", first_name="Z"
    )


@pytest.mark.asyncio
async def test_zones_composes_birth_year_and_baseline(
    database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = database
    identity = _identity()
    async with factory.begin() as session:
        user, _ = await UserRepository(session).get_or_create(
            telegram_user_id=identity.telegram_user_id,
            telegram_username=identity.telegram_username,
            first_name=identity.first_name,
        )
        await ProfileRepository(session).upsert_mandatory_athlete_profile(
            user_id=user.id,
            birth_year=1990,
            gender=AthleteGender.MALE,
            weight_kg=74,
            height_cm=179,
        )
        await AthleteBaselineRepository(session).upsert(
            athlete_id=user.id,
            goal_signature="test-signature",
            baseline=AthleteBaselineData(
                running=RunningBaseline(
                    typical_weekly_sessions=3,
                    typical_weekly_duration_minutes=150,
                    longest_recent_run_minutes=60,
                ),
                preferences=TrainingPreferences(
                    coaching_style=CoachingStyle.NORMAL,
                    desired_weekly_sessions={Discipline.RUNNING: 3},
                ),
            ),
        )

    service = AccountQueryService(factory)

    zones = await service.zones(identity)

    assert zones is not None
    assert zones.heart_rate is not None
    assert zones.running is None  # no recent_race_result in the baseline above


@pytest.mark.asyncio
async def test_zones_returns_none_for_unknown_athlete(
    database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = database
    service = AccountQueryService(factory)

    zones = await service.zones(
        TelegramIdentity(telegram_user_id=999, telegram_username=None, first_name=None)
    )

    assert zones is None
