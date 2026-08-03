"""Deterministic mandatory athlete-profile onboarding tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import Settings
from app.db.base import Base
from app.domain.enums import AthleteGender, OnboardingStatus, OnboardingStep, UserStatus
from app.integrations.llm.models import GoalExtractionAction, GoalExtractionOutput
from app.repositories.onboarding import OnboardingRepository
from app.repositories.profiles import ProfileRepository
from app.repositories.users import UserRepository
from app.schemas.common import TelegramIdentity
from app.schemas.onboarding_goal import GoalExtractionWorkflowResult
from app.services.onboarding import OnboardingApplicationError, OnboardingService


@dataclass
class NeverGoalExtractor:
    calls: int = 0

    async def extract(
        self,
        *,
        user_id: UUID,
        action: GoalExtractionAction,
        user_text: str,
        existing_draft: GoalExtractionOutput | None,
        current_date: str,
    ) -> GoalExtractionWorkflowResult:
        del user_id, action, user_text, existing_draft, current_date
        self.calls += 1
        raise AssertionError("Mandatory profile intake must not invoke the LLM")


@pytest_asyncio.fixture
async def profile_database() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
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
async def test_profile_inputs_validate_deterministically_and_complete_atomically(
    profile_database: async_sessionmaker[AsyncSession],
) -> None:
    identity = _identity()
    extractor = NeverGoalExtractor()
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
        goal_extractor=extractor,
        settings=Settings(llm_mode="mock"),
    )

    invalid_year = await service.handle_text(identity, "2009")
    invalid_year_text = await service.handle_text(identity, "nineteen ninety")
    assert invalid_year.kind == "profile_validation_error"
    assert invalid_year.error_code == "invalid_birth_year"
    assert invalid_year_text.current_step is OnboardingStep.PROFILE_BIRTH_YEAR_INTAKE

    gender = await service.handle_text(identity, "1990")
    assert gender.current_step is OnboardingStep.PROFILE_GENDER_INTAKE
    with pytest.raises(OnboardingApplicationError, match="invalid_action"):
        await service.choose_gender(identity, "NOT_A_CATEGORY")

    weight = await service.choose_gender(identity, "OTHER_UNSPECIFIED")
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

    completed = await service.handle_text(identity, "178")
    assert completed.kind == "onboarding_completed"
    assert completed.user_status is UserStatus.ONBOARDING_COMPLETED
    assert completed.onboarding_status is OnboardingStatus.COMPLETED
    assert extractor.calls == 0

    async with profile_database() as session:
        profile = await ProfileRepository(session).get_athlete_profile(user_id=user_id)
        persisted_user = await UserRepository(session).require_by_id(user_id)
        onboarding = await OnboardingRepository(session).require_for_user(
            user_id=user_id
        )
        assert profile is not None
        assert profile.birth_year == 1990
        assert profile.gender is AthleteGender.OTHER_UNSPECIFIED
        assert profile.weight_kg == 72.5
        assert profile.height_cm == 178.0
        assert persisted_user.status is UserStatus.ONBOARDING_COMPLETED
        assert onboarding.status is OnboardingStatus.COMPLETED
