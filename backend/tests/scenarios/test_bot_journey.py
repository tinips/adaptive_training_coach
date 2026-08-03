"""End-to-end Telegram scenario for the retained onboarding journey."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.bot import messages
from app.bot.rendering import TelegramResponse
from app.bot.service import CoachBotApplicationService
from app.config import Settings
from app.db.base import Base
from app.domain.enums import AthleteGender, OnboardingStatus, OnboardingStep, UserStatus
from app.integrations.llm.models import (
    GoalExtractionAction,
    GoalExtractionOutput,
    GoalExtractionPatch,
)
from app.repositories.onboarding import OnboardingRepository
from app.repositories.profiles import ProfileRepository
from app.repositories.users import UserRepository
from app.schemas.common import TelegramIdentity
from app.schemas.onboarding_goal import GoalExtractionWorkflowResult
from app.services.accounts import AccountQueryService, AccountService
from app.services.onboarding import OnboardingService
from app.services.profiles import ProfileService


@dataclass
class QueueGoalExtractor:
    results: list[GoalExtractionWorkflowResult]

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
        return self.results.pop(0)


class UnusedStrava:
    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"Strava must not be used during onboarding: {name}")


def _extracted(
    *,
    main_goal: str | None,
    target_outcome: str | None,
    secondary_priority: str | None = None,
) -> GoalExtractionWorkflowResult:
    return GoalExtractionWorkflowResult(
        outcome="extracted",
        goal_patch=GoalExtractionPatch(
            main_goal=main_goal,
            event_date=None,
            target_outcome=target_outcome,
            secondary_priority=secondary_priority,
            missing_fields=[],
            ambiguous_fields=[],
            message_status="COMPLETE",
        ),
    )


@pytest_asyncio.fixture
async def journey() -> AsyncIterator[
    tuple[CoachBotApplicationService, async_sessionmaker[AsyncSession]]
]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    extractor = QueueGoalExtractor(
        [
            _extracted(
                main_goal="Complete a marathon",
                target_outcome="Finish safely",
            ),
            _extracted(
                main_goal=None,
                target_outcome=None,
                secondary_priority="Maintain strength",
            ),
        ]
    )
    onboarding = OnboardingService(
        session_factory=factory,
        goal_extractor=extractor,
        settings=Settings(
            environment="test",
            database_url="sqlite+aiosqlite:///:memory:",
            llm_mode="mock",
        ),
    )
    yield (
        CoachBotApplicationService(
            onboarding=onboarding,
            profiles=ProfileService(factory),
            account_queries=AccountQueryService(factory),
            accounts=AccountService(factory),
            strava=UnusedStrava(),  # type: ignore[arg-type]
            apple_health=None,
            workout_feedback=None,
            strava_enabled=False,
            workout_feedback_enabled=False,
        ),
        factory,
    )
    await engine.dispose()


def _athlete() -> TelegramIdentity:
    return TelegramIdentity(
        telegram_user_id=5101,
        telegram_username="journey_runner",
        first_name="Journey Runner",
        language_code="en",
    )


def _buttons(response: TelegramResponse) -> list[tuple[str, str]]:
    keyboard = response.keyboard
    if keyboard is None:
        return []
    return [
        (button.text, button.callback_data or "")
        for row in keyboard.inline_keyboard
        for button in row
    ]


@pytest.mark.asyncio
async def test_journey_collects_mandatory_profile_after_goal_confirmation(
    journey: tuple[CoachBotApplicationService, async_sessionmaker[AsyncSession]],
) -> None:
    bot, factory = journey
    athlete = _athlete()

    welcome = await bot.start(athlete)
    consent = await bot.handle_callback(athlete, "nav:v1:consent")
    setup = await bot.handle_callback(athlete, "ob:v1:consent")
    intake = await bot.handle_callback(athlete, "ob:v1:profile")
    confirmation = await bot.handle_text(
        athlete,
        "I want to complete a marathon and finish safely.",
    )
    addition = await bot.handle_callback(athlete, "ob:v1:goal:add")
    updated = await bot.handle_text(athlete, "I also want to maintain strength.")
    saved = await bot.handle_callback(athlete, "ob:v1:goal:confirm")
    gender = await bot.handle_text(athlete, "1990")
    weight = await bot.handle_callback(
        athlete,
        "ob:v1:profile:gender:FEMALE",
    )
    height = await bot.handle_text(athlete, "62.5")
    completed = await bot.handle_text(athlete, "168")
    displayed_profile = await bot.profile(athlete)

    assert welcome.text == messages.WELCOME
    assert consent.text == messages.CONSENT
    assert setup.text == messages.SETUP_INTRODUCTION
    assert intake.text == messages.GOAL_INTAKE
    assert ("Cancel", "ob:v1:cancel") in _buttons(intake)
    assert confirmation.text.startswith("Here\u2019s what I understood:")
    assert addition.text == messages.GOAL_ADDITION
    assert "Maintain strength" in updated.text
    assert saved.text == messages.PROFILE_BIRTH_YEAR_INTAKE
    assert gender.text == messages.PROFILE_GENDER_INTAKE
    assert ("Female", "ob:v1:profile:gender:FEMALE") in _buttons(gender)
    assert weight.text == messages.PROFILE_WEIGHT_INTAKE
    assert height.text == messages.PROFILE_HEIGHT_INTAKE
    assert completed.text == messages.ONBOARDING_COMPLETED
    assert "Birth year: 1990" in displayed_profile.text
    assert "Category: Female" in displayed_profile.text

    async with factory() as session:
        user = await UserRepository(session).get_by_telegram_id(
            athlete.telegram_user_id
        )
        assert user is not None
        assert user.status is UserStatus.ONBOARDING_COMPLETED
        state = await OnboardingRepository(session).require_for_user(user_id=user.id)
        assert state.status is OnboardingStatus.COMPLETED
        assert state.current_step is OnboardingStep.PROFILE_HEIGHT_INTAKE
        goal = await ProfileRepository(session).get_training_goal(user_id=user.id)
        assert goal is not None
        assert goal.main_goal == "Complete a marathon"
        assert goal.target_outcome == "Finish safely"
        assert goal.secondary_priority == "Maintain strength"
        profile = await ProfileRepository(session).get_athlete_profile(user_id=user.id)
        assert profile is not None
        assert profile.birth_year == 1990
        assert profile.gender is AthleteGender.FEMALE
        assert profile.weight_kg == 62.5
        assert profile.height_cm == 168.0

    back = await bot.handle_callback(athlete, "nav:v1:welcome")
    assert back.text == messages.WELCOME
