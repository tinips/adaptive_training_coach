"""Atomic normalized profile finalization scenarios."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.bot import messages
from app.db.base import Base
from app.db.models import (
    AthleteBaseline,
    AthleteProfile,
    AvailabilityRule,
    BaselinePreference,
    BodyArea,
    EquipmentAccess,
    EquipmentType,
    HealthConstraint,
    OnboardingSession,
    User,
)
from app.domain.enums import (
    BaselinePreferenceStatus,
    BaselineSource,
    ConnectionStatus,
    OnboardingStatus,
    OnboardingStep,
    UserStatus,
)
from app.repositories.onboarding import OnboardingRepository
from app.repositories.strava import StravaRepository
from app.repositories.users import UserRepository
from app.schemas.common import TelegramIdentity
from app.services.accounts import AccountQueryService
from app.services.profiles import (
    BaselineSelectionUnavailableError,
    IncompleteProfileError,
    ProfileService,
)


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


def complete_answers() -> dict[str, object]:
    return {
        "consent": True,
        "primary_sport": "RUNNING",
        "goal_type": "TEN_K",
        "event_status": False,
        "goal_priority": "FINISH_SAFELY",
        "age": 35,
        "height": None,
        "weight": 68.5,
        "training_days": ["MONDAY", "SATURDAY"],
        "weekday_duration": 60,
        "weekend_duration": 120,
        "equipment": ["RUNNING_SHOES", "SPORTS_WATCH"],
        "health_areas": ["NONE"],
        "coach_tone": "CONCISE_PRACTICAL",
        "coach_detail": "MEDIUM",
        "baseline_source": "SKIP_FOR_NOW",
    }


async def stage_user(
    factory: async_sessionmaker[AsyncSession],
    *,
    telegram_id: int,
    answers: dict[str, object],
) -> uuid.UUID:
    async with factory.begin() as session:
        user, _ = await UserRepository(session).get_or_create(
            telegram_user_id=telegram_id,
            telegram_username=None,
            first_name="Athlete",
        )
        repository = OnboardingRepository(session)
        await repository.get_or_create(user_id=user.id)
        await repository.save_progress(
            user_id=user.id,
            current_step=OnboardingStep.SUMMARY,
            answers=answers,
        )
        return user.id


@pytest.mark.asyncio
async def test_finalization_is_atomic_and_idempotent(
    database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = database
    user_id = await stage_user(
        factory,
        telegram_id=101,
        answers=complete_answers(),
    )
    service = ProfileService(factory)

    first = await service.finalize(user_id=user_id)
    second = await service.finalize(user_id=user_id)

    assert first == second
    assert first.training_days == ["MONDAY", "SATURDAY"]
    async with factory() as session:
        profile_count = await session.scalar(
            select(func.count()).select_from(AthleteProfile)
        )
        onboarding = await session.scalar(
            select(OnboardingSession).where(OnboardingSession.user_id == user_id)
        )
    assert profile_count == 1
    assert onboarding is not None
    assert onboarding.status is OnboardingStatus.COMPLETED


@pytest.mark.asyncio
async def test_invalid_staging_rolls_back_all_normalized_writes(
    database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = database
    invalid = complete_answers()
    invalid.pop("age")
    user_id = await stage_user(factory, telegram_id=102, answers=invalid)

    with pytest.raises(IncompleteProfileError):
        await ProfileService(factory).finalize(
            user_id=user_id,
        )

    async with factory() as session:
        profile_count = await session.scalar(
            select(func.count()).select_from(AthleteProfile)
        )
        onboarding = await session.scalar(
            select(OnboardingSession).where(OnboardingSession.user_id == user_id)
        )
    assert profile_count == 0
    assert onboarding is not None
    assert onboarding.status is OnboardingStatus.ACTIVE


@pytest.mark.asyncio
async def test_profile_queries_are_user_isolated(
    database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = database
    owner_id = await stage_user(
        factory,
        telegram_id=201,
        answers=complete_answers(),
    )
    other_id = await stage_user(
        factory,
        telegram_id=202,
        answers={"consent": True},
    )
    service = ProfileService(factory)
    await service.finalize(user_id=owner_id)

    owner_profile = await service.get(user_id=owner_id)
    other_profile = await service.get(user_id=other_id)

    assert owner_profile is not None
    assert other_profile is None


@pytest.mark.asyncio
async def test_pending_baseline_selection_updates_only_preference_and_lifecycle(
    database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = database
    user_id = await stage_user(
        factory,
        telegram_id=250,
        answers=complete_answers(),
    )
    service = ProfileService(factory)
    await service.finalize(user_id=user_id)

    selected = await service.select_pending_baseline_source(
        user_id=user_id,
        source=BaselineSource.MANUAL,
    )

    async with factory() as session:
        user = await session.get(User, user_id)
        preference = await session.scalar(
            select(BaselinePreference).where(
                BaselinePreference.user_id == user_id,
            )
        )
        baseline_count = await session.scalar(
            select(func.count())
            .select_from(AthleteBaseline)
            .where(AthleteBaseline.user_id == user_id)
        )
    assert selected.baseline_source is BaselineSource.MANUAL
    assert user is not None
    assert user.status is UserStatus.BASELINE_PENDING
    assert preference is not None
    assert preference.selected_source is BaselineSource.MANUAL
    assert preference.status is BaselinePreferenceStatus.NOT_IMPLEMENTED
    assert baseline_count == 0


@pytest.mark.asyncio
async def test_stale_baseline_choice_cannot_replace_a_ready_source(
    database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = database
    user_id = await stage_user(
        factory,
        telegram_id=251,
        answers=complete_answers(),
    )
    service = ProfileService(factory)
    await service.finalize(user_id=user_id)
    async with factory.begin() as session:
        await UserRepository(session).update_status(
            user_id=user_id,
            status=UserStatus.BASELINE_READY,
        )

    with pytest.raises(BaselineSelectionUnavailableError):
        await service.select_pending_baseline_source(
            user_id=user_id,
            source=BaselineSource.CALIBRATION,
        )

    persisted = await service.get(user_id=user_id)
    assert persisted is not None
    assert persisted.baseline_source is BaselineSource.SKIP_FOR_NOW


@pytest.mark.asyncio
async def test_profile_round_trip_preserves_access_notes_and_descriptions(
    database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = database
    answers = complete_answers()
    answers.update(
        {
            "primary_sport": "TRIATHLON",
            "goal_type": "OLYMPIC_TRIATHLON",
            "training_days": ["TUESDAY"],
            "weekday_duration": "OVER_90",
            "weekend_duration": "VARIABLE",
            "equipment": ["OTHER"],
            "equipment_other_description": "Kettlebell and rowing erg",
            "pool_access": {
                "type": "REGULAR",
                "days": ["MONDAY", "SATURDAY"],
            },
            "bike_access": {"type": "IRREGULAR", "days": []},
            "health_areas": ["OTHER"],
            "health_areas_other_description": "Elbow and wrist",
            "health_timing": "CURRENT",
            "health_description": "Limited overhead reach",
        }
    )
    user_id = await stage_user(factory, telegram_id=301, answers=answers)

    await ProfileService(factory).finalize(user_id=user_id)
    persisted = await ProfileService(factory).get(user_id=user_id)

    assert persisted is not None
    assert persisted.weekday_duration == "OVER_90"
    assert persisted.weekend_duration == "VARIABLE"
    access_by_type = {item.equipment_type: item for item in persisted.equipment_access}
    assert access_by_type[EquipmentType.OTHER].notes == ("Kettlebell and rowing erg")
    assert access_by_type[EquipmentType.SWIMMING_POOL].access_days == [
        "MONDAY",
        "SATURDAY",
    ]
    assert access_by_type[EquipmentType.ROAD_BIKE].access_type == "IRREGULAR"
    assert persisted.health_constraint_details[0].body_area is BodyArea.OTHER
    assert persisted.health_constraint_details[0].description == (
        "Elbow and wrist; Limited overhead reach"
    )

    async with factory() as session:
        equipment = (
            await session.scalars(
                select(EquipmentAccess).where(
                    EquipmentAccess.user_id == user_id,
                )
            )
        ).all()
        constraint = await session.scalar(
            select(HealthConstraint).where(
                HealthConstraint.user_id == user_id,
            )
        )
        availability = await session.scalar(
            select(AvailabilityRule).where(
                AvailabilityRule.user_id == user_id,
            )
        )
    assert {item.equipment_type for item in equipment} == {
        EquipmentType.OTHER,
        EquipmentType.SWIMMING_POOL,
        EquipmentType.ROAD_BIKE,
    }
    assert (
        next(
            item for item in equipment if item.equipment_type is EquipmentType.OTHER
        ).notes
        == "Kettlebell and rowing erg"
    )
    assert constraint is not None
    assert constraint.normalized_description == (
        "Elbow and wrist; Limited overhead reach"
    )
    assert availability is not None
    assert availability.available_minutes == 90
    assert availability.is_variable is True

    query_data = await AccountQueryService(factory).profile(
        TelegramIdentity(telegram_user_id=301),
    )
    assert query_data is not None
    rendered = messages.persisted_profile(query_data)
    assert "Weekday availability: Over 90 min" in rendered
    assert "Weekend availability: Variable" in rendered
    assert "notes: Kettlebell and rowing erg" in rendered
    assert "Swimming Pool" in rendered
    assert "Monday, Saturday" in rendered
    assert "Road Bike" in rendered
    assert "Irregular" in rendered
    assert "Elbow and wrist; Limited overhead reach" in rendered


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("connection_status", "expected_connected", "expected_can_disconnect"),
    [
        (ConnectionStatus.CONNECTED, True, True),
        (ConnectionStatus.REFRESH_FAILED, False, True),
        (ConnectionStatus.INSUFFICIENT_SCOPE, False, True),
        (ConnectionStatus.DISCONNECTED, False, False),
    ],
)
async def test_only_healthy_strava_connection_is_available_to_bot_queries(
    database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    connection_status: ConnectionStatus,
    expected_connected: bool,
    expected_can_disconnect: bool,
) -> None:
    _, factory = database
    user_id = await stage_user(
        factory,
        telegram_id=401,
        answers={"consent": True},
    )
    async with factory.begin() as session:
        await StravaRepository(session).upsert_connection(
            user_id=user_id,
            strava_athlete_id=987654,
            accepted_scopes=["activity:read"],
            encrypted_access_token="encrypted-access",
            encrypted_refresh_token="encrypted-refresh",
            access_token_expires_at=datetime(2027, 1, 1, tzinfo=UTC),
            connection_status=connection_status,
        )

    status = await AccountQueryService(factory).strava(
        TelegramIdentity(telegram_user_id=401),
    )

    assert status is not None
    assert status["connected"] is expected_connected
    assert status["can_disconnect"] is expected_can_disconnect
    assert status["connection_status"] is connection_status
