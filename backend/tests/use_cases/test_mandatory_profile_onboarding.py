"""Deterministic mandatory athlete-profile onboarding tests."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from catalog_seed import seed_training_catalog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import Settings
from app.db.base import Base
from app.db.models import AppleHealthImportJob, AthleteCapability, Capability
from app.domain.enums import (
    AppleHealthImportStatus,
    AthleteCapabilityStatus,
    AthleteGender,
    OnboardingStatus,
    OnboardingStep,
    TrainingFileFormat,
    TrainingImportContext,
    UserStatus,
)
from app.repositories.athlete_baselines import AthleteBaselineRepository
from app.repositories.onboarding import OnboardingRepository
from app.repositories.profiles import ProfileRepository
from app.repositories.users import UserRepository
from app.schemas.common import TelegramIdentity
from app.services.onboarding import OnboardingApplicationError, OnboardingService
from app.services.onboarding.service import _parse_goal_metric
from app.training_catalog_seed import catalog_id


def test_triathlon_finish_time_uses_hours_and_minutes_only() -> None:
    assert _parse_goal_metric("triathlon_finish_time", "5:30") == 19_800

    with pytest.raises(ValueError):
        _parse_goal_metric("triathlon_finish_time", "5:30:00")


@pytest_asyncio.fixture
async def profile_database() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with factory.begin() as session:
        await seed_training_catalog(session)
    yield factory
    await engine.dispose()


def _identity() -> TelegramIdentity:
    return TelegramIdentity(
        telegram_user_id=6301,
        telegram_username="profile_athlete",
        first_name="Profile Athlete",
        language_code="en",
    )


@pytest.mark.asyncio
async def test_cycling_goal_collects_structured_targets(
    profile_database: async_sessionmaker[AsyncSession],
) -> None:
    identity = _identity()
    async with profile_database.begin() as session:
        user, _ = await UserRepository(session).get_or_create(
            telegram_user_id=identity.telegram_user_id,
            telegram_username=identity.telegram_username,
            first_name=identity.first_name,
        )
        user.status = UserStatus.ONBOARDING_IN_PROGRESS
        await OnboardingRepository(session).get_or_create(
            user_id=user.id,
            current_step=OnboardingStep.GOAL_INTAKE,
        )

    service = OnboardingService(
        session_factory=profile_database,
        settings=Settings(llm_mode="mock"),
    )

    result = await service.choose_goal_sport(identity, "CYCLING")

    assert result.current_step is OnboardingStep.GOAL_METRIC_INTAKE
    assert result.answers["goal_metric_fields"] == [
        "cycling_distance",
        "elevation",
        "cycling_average_speed",
    ]
    elevation = await service.submit_goal_metric(identity, "100")
    assert elevation.current_step is OnboardingStep.GOAL_METRIC_INTAKE
    speed = await service.submit_goal_metric(identity, "850")
    assert speed.current_step is OnboardingStep.GOAL_METRIC_INTAKE
    completed = await service.submit_goal_metric(identity, "28.5")
    assert completed.current_step is OnboardingStep.GOAL_EVENT_DATE
    async with profile_database() as session:
        goal = await ProfileRepository(session).get_training_goal(user_id=user.id)
    assert goal is not None
    assert goal.target_distance_km == 100.0
    assert goal.target_elevation_m == 850.0
    assert goal.target_average_speed_kph == 28.5


@pytest.mark.asyncio
async def test_supporting_goal_options_exclude_the_current_main_goal(
    profile_database: async_sessionmaker[AsyncSession],
) -> None:
    identity = _identity()
    async with profile_database.begin() as session:
        user, _ = await UserRepository(session).get_or_create(
            telegram_user_id=identity.telegram_user_id,
            telegram_username=identity.telegram_username,
            first_name=identity.first_name,
        )
        await ProfileRepository(session).upsert_training_goal(
            user_id=user.id,
            main_goal="Maintain strength",
            event_date=None,
            secondary_priority=None,
            goal_template_id=catalog_id("goal", "ROAD_CYCLING_EVENT"),
        )

    service = OnboardingService(
        session_factory=profile_database,
        settings=Settings(llm_mode="mock"),
    )

    options = await service.supporting_goal_options(identity)

    assert ("STRENGTH_MAINTENANCE", "Maintain strength") not in options


@pytest.mark.asyncio
async def test_profile_inputs_validate_deterministically_and_then_begin_goal_intake(
    profile_database: async_sessionmaker[AsyncSession],
) -> None:
    identity = _identity()
    async with profile_database.begin() as session:
        user, _ = await UserRepository(session).get_or_create(
            telegram_user_id=identity.telegram_user_id,
            telegram_username=identity.telegram_username,
            first_name=identity.first_name,
        )
        user.status = UserStatus.ONBOARDING_IN_PROGRESS
        await OnboardingRepository(session).get_or_create(
            user_id=user.id,
            current_step=OnboardingStep.PROFILE_BIRTH_YEAR_INTAKE,
        )
        user_id = user.id

    service = OnboardingService(
        session_factory=profile_database,
        settings=Settings(llm_mode="mock"),
    )

    invalid_year = await service.handle_text(identity, "2009")
    invalid_year_text = await service.handle_text(identity, "nineteen ninety")
    assert invalid_year.kind == "profile_validation_error"
    assert invalid_year.error_code == "invalid_birth_year"
    assert invalid_year_text.current_step is OnboardingStep.PROFILE_BIRTH_YEAR_INTAKE

    gender = await service.handle_text(identity, "1990")
    assert gender.current_step is OnboardingStep.PROFILE_GENDER_INTAKE
    corrected = await service.update_onboarding_data(
        user_id=user_id,
        payload={"birth_year": 2004},
    )
    assert corrected.updated_fields == {"birth_year": 2004}
    with pytest.raises(OnboardingApplicationError, match="invalid_action"):
        await service.choose_gender(identity, "NOT_A_CATEGORY")

    with pytest.raises(OnboardingApplicationError, match="invalid_action"):
        await service.choose_gender(identity, "OTHER_UNSPECIFIED")

    weight = await service.choose_gender(identity, "FEMALE")
    assert weight.current_step is OnboardingStep.PROFILE_WEIGHT_INTAKE
    for bad_weight in ("weight", "nan", "39.9", "200.1"):
        invalid_weight = await service.handle_text(identity, bad_weight)
        assert invalid_weight.kind == "profile_validation_error"
        assert invalid_weight.current_step is OnboardingStep.PROFILE_WEIGHT_INTAKE

    height = await service.handle_text(identity, "72.5")
    assert height.current_step is OnboardingStep.PROFILE_HEIGHT_INTAKE
    for bad_height in ("height", "170.5", "119", "231"):
        invalid_height = await service.handle_text(identity, bad_height)
        assert invalid_height.kind == "profile_validation_error"
        assert invalid_height.current_step is OnboardingStep.PROFILE_HEIGHT_INTAKE

    timezone = await service.handle_text(identity, "178")
    assert timezone.kind == "profile_timezone_intake"
    assert timezone.current_step is OnboardingStep.PROFILE_TIMEZONE_INTAKE
    goal_intake = await service.handle_text(identity, "Europe/Madrid")
    assert goal_intake.kind == "goal_intake"
    assert goal_intake.current_step is OnboardingStep.GOAL_INTAKE
    assert goal_intake.user_status is UserStatus.ONBOARDING_IN_PROGRESS
    assert goal_intake.onboarding_status is OnboardingStatus.ACTIVE

    async with profile_database() as session:
        profile = await ProfileRepository(session).get_athlete_profile(user_id=user_id)
        persisted_user = await UserRepository(session).require_by_id(user_id)
        onboarding = await OnboardingRepository(session).require_for_user(
            user_id=user_id
        )
        assert profile is not None
        assert profile.birth_year == 2004
        assert profile.gender is AthleteGender.FEMALE
        assert profile.weight_kg == 72.5
        assert profile.height_cm == 178.0
        assert persisted_user.timezone == "Europe/Madrid"
        assert persisted_user.status is UserStatus.ONBOARDING_IN_PROGRESS
        assert onboarding.status is OnboardingStatus.ACTIVE
        assert onboarding.current_step is OnboardingStep.GOAL_INTAKE


@pytest.mark.asyncio
async def test_development_steps_seed_only_the_requesting_users_onboarding_state(
    profile_database: async_sessionmaker[AsyncSession],
) -> None:
    identity = _identity()
    other_identity = TelegramIdentity(
        telegram_user_id=6302,
        telegram_username="other_athlete",
        first_name="Other Athlete",
        language_code="en",
    )
    service = OnboardingService(
        session_factory=profile_database,
        settings=Settings(environment="development", llm_mode="mock"),
    )

    availability = await service.seed_development_step(identity, "availability")
    equipment = await service.seed_development_step(identity, "equipment")
    limitations = await service.seed_development_step(identity, "limitations")
    history = await service.seed_development_step(identity, "history")
    skipped = await service.skip_training_history(identity)
    completed = await service.seed_development_step(identity, "completed")
    await service.seed_development_step(other_identity, "availability")

    assert availability.current_step is OnboardingStep.AVAILABILITY_INTAKE
    assert equipment.current_step is OnboardingStep.EQUIPMENT_RECOMMENDATION
    assert limitations.current_step is OnboardingStep.HEALTH_LIMITATIONS_INTAKE
    assert history.current_step is OnboardingStep.TRAINING_HISTORY_IMPORT
    assert history.onboarding_status is OnboardingStatus.ACTIVE
    assert skipped.onboarding_status is OnboardingStatus.COMPLETED
    assert skipped.user_status is UserStatus.ONBOARDING_COMPLETED
    assert skipped.training_history_skipped is True
    assert completed.onboarding_status is OnboardingStatus.COMPLETED
    assert completed.user_status is UserStatus.ONBOARDING_COMPLETED
    assert completed.training_history_skipped is False

    async with profile_database() as session:
        users = UserRepository(session)
        first_user = await users.get_by_telegram_id(identity.telegram_user_id)
        other_user = await users.get_by_telegram_id(other_identity.telegram_user_id)
        assert first_user is not None
        assert other_user is not None
        profiles = ProfileRepository(session)
        first_context = await profiles.get_athlete_profile_context(
            user_id=first_user.id
        )
        other_context = await profiles.get_athlete_profile_context(
            user_id=other_user.id
        )
        first_goal = await profiles.get_training_goal(user_id=first_user.id)
        other_goal = await profiles.get_training_goal(user_id=other_user.id)

        assert first_context is not None
        assert first_context.weekly_availability_jsonb is not None
        assert first_context.weekly_availability_jsonb["schema_version"] == 2
        assert first_context.health_limitations_text == "NONE_REPORTED"
        assert first_goal is not None
        assert other_context is not None
        assert other_context.weekly_availability_jsonb is None
        assert other_goal is not None

    reset = await service.reset_development_onboarding(identity)
    assert reset.current_step is OnboardingStep.CONSENT
    assert reset.onboarding_status is OnboardingStatus.ACTIVE
    assert reset.user_status is UserStatus.ONBOARDING_IN_PROGRESS


@pytest.mark.asyncio
async def test_development_goal_equipment_reset_preserves_profile_and_history(
    profile_database: async_sessionmaker[AsyncSession],
) -> None:
    identity = _identity()
    service = OnboardingService(
        session_factory=profile_database,
        settings=Settings(environment="development", llm_mode="mock"),
    )
    seeded = await service.seed_development_step(identity, "history")
    async with profile_database.begin() as session:
        capability_id = await session.scalar(select(Capability.id).limit(1))
        assert capability_id is not None
        session.add(
            AthleteCapability(
                athlete_id=seeded.user_id,
                capability_id=capability_id,
                status=AthleteCapabilityStatus.AVAILABLE,
            )
        )

    reset = await service.reset_development_goal_and_equipment(identity)

    assert reset.current_step is OnboardingStep.GOAL_INTAKE
    assert reset.onboarding_status is OnboardingStatus.ACTIVE
    assert reset.user_status is UserStatus.ONBOARDING_IN_PROGRESS
    async with profile_database() as session:
        profiles = ProfileRepository(session)
        assert await profiles.get_training_goal(user_id=seeded.user_id) is None
        profile = await profiles.get_athlete_profile(user_id=seeded.user_id)
        assert profile is not None
        remaining_capabilities = await session.scalar(
            select(AthleteCapability).where(
                AthleteCapability.athlete_id == seeded.user_id
            )
        )
        assert remaining_capabilities is None


@pytest.mark.asyncio
async def test_history_skip_is_rejected_while_an_import_is_active(
    profile_database: async_sessionmaker[AsyncSession],
) -> None:
    identity = _identity()
    service = OnboardingService(
        session_factory=profile_database,
        settings=Settings(environment="development", llm_mode="mock"),
    )
    history = await service.seed_development_step(identity, "history")
    async with profile_database.begin() as session:
        session.add(
            AppleHealthImportJob(
                user_id=history.user_id,
                context=TrainingImportContext.ONBOARDING_HISTORY,
                onboarding_session_id=None,
                telegram_file_id="active",
                telegram_file_unique_id="active-unique",
                display_filename="history.zip",
                file_format=TrainingFileFormat.APPLE_HEALTH_ZIP,
                status=AppleHealthImportStatus.PROCESSING,
            )
        )

    with pytest.raises(OnboardingApplicationError, match="import_already_active"):
        await service.skip_training_history(identity)


@pytest.mark.asyncio
async def test_availability_uses_the_structured_extraction_service() -> None:
    """Availability is LLM-extracted before its structured draft is reviewed."""

    import inspect

    from app.services.onboarding.service import OnboardingService

    assert "model" in inspect.signature(OnboardingService.__init__).parameters


@pytest.mark.asyncio
async def test_context_text_outside_length_bounds_is_rejected_without_a_model_call(
    profile_database: async_sessionmaker[AsyncSession],
) -> None:
    """Availability/health answers are gated by length alone, not by a model."""

    identity = TelegramIdentity(
        telegram_user_id=6303,
        telegram_username="length_athlete",
        first_name="Length Athlete",
        language_code="en",
    )
    service = OnboardingService(
        session_factory=profile_database,
        settings=Settings(environment="development", llm_mode="mock"),
    )
    await service.seed_development_step(identity, "availability")

    with pytest.raises(OnboardingApplicationError, match="invalid_action"):
        await service.handle_text(identity, "  ok  ")
    with pytest.raises(OnboardingApplicationError, match="invalid_action"):
        await service.handle_text(identity, "x" * 2001)


@pytest.mark.asyncio
async def test_web_app_baseline_is_goal_adaptive_and_persisted(
    profile_database: async_sessionmaker[AsyncSession],
) -> None:
    identity = _identity()
    async with profile_database.begin() as session:
        user, _ = await UserRepository(session).get_or_create(
            telegram_user_id=identity.telegram_user_id,
            telegram_username=identity.telegram_username,
            first_name=identity.first_name,
        )
        user.status = UserStatus.ONBOARDING_IN_PROGRESS
        await OnboardingRepository(session).get_or_create(
            user_id=user.id,
            current_step=OnboardingStep.HEALTH_LIMITATIONS_INTAKE,
        )
        profiles = ProfileRepository(session)
        await profiles.upsert_mandatory_athlete_profile(
            user_id=user.id,
            birth_year=1990,
            gender=AthleteGender.FEMALE,
            weight_kg=70,
            height_cm=175,
        )
        await profiles.upsert_training_goal(
            user_id=user.id,
            main_goal="10K race",
            event_date=None,
            secondary_priority=None,
            goal_template_id=catalog_id("goal", "RUNNING_10K"),
        )

    service = OnboardingService(
        session_factory=profile_database,
        settings=Settings(llm_mode="mock"),
    )

    started = await service.choose_health_limitations(identity, "none")

    assert started.current_step is OnboardingStep.BASELINE_INTAKE
    assert started.answers["baseline_fields"] == [
        "running.typical_weekly_sessions",
        "running.typical_weekly_duration_minutes",
        "running.longest_recent_run_minutes",
        "running.recent_race_result",
    ]

    completed = await service.submit_baseline_form(
        identity,
        {
            "running.typical_weekly_sessions": "3",
            "running.typical_weekly_duration_minutes": "150",
            "running.longest_recent_run_minutes": "55",
            "running.recent_race_result": "",
        },
    )

    assert completed.onboarding_status is OnboardingStatus.COMPLETED
    async with profile_database() as session:
        saved = await AthleteBaselineRepository(session).get(athlete_id=user.id)
    assert saved is not None
    assert saved.form_version == 2
    assert saved.baseline_jsonb == {
        "running": {
            "typical_weekly_sessions": 3,
            "typical_weekly_duration_minutes": 150,
            "longest_recent_run_minutes": 55,
        }
    }
