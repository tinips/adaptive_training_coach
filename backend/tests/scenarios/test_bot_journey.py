"""End-to-end Telegram scenario for the retained onboarding journey."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import UUID

import pytest
import pytest_asyncio
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableLambda
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.bot import messages
from app.bot.rendering import TelegramResponse
from app.bot.service import CoachBotApplicationService
from app.config import Settings
from app.db.base import Base
from app.domain.enums import AthleteGender, OnboardingStatus, OnboardingStep, UserStatus
from app.integrations.llm.mock import DeterministicFakeOnboardingModel
from app.integrations.llm.models import (
    GoalExtractionAction,
    GoalExtractionOutput,
    GoalExtractionPatch,
)
from app.repositories.equipment import EquipmentRepository
from app.repositories.onboarding import OnboardingRepository
from app.repositories.profiles import ProfileRepository
from app.repositories.users import UserRepository
from app.schemas.common import TelegramIdentity
from app.schemas.onboarding_goal import GoalExtractionWorkflowResult
from app.services.accounts import AccountQueryService, AccountService
from app.services.onboarding import OnboardingService
from app.services.profiles import ProfileService
from app.workflows.telegram_orchestrator.workspace import TelegramAgentWorkspace
from tests.equipment_seed import seed_equipment_catalog


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


class TrackingGlobalAgentModel(DeterministicFakeOnboardingModel):
    """Fail the scenario if active onboarding reaches the global model."""

    def __init__(self) -> None:
        super().__init__()
        self.invocations = 0

    def bind_tools(self, tools):  # type: ignore[no-untyped-def]
        runnable = super().bind_tools(tools)

        async def respond(messages, config):  # type: ignore[no-untyped-def]
            self.invocations += 1
            return await runnable.ainvoke(messages, config=config)

        return RunnableLambda(respond)


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
    tuple[
        CoachBotApplicationService,
        async_sessionmaker[AsyncSession],
        TrackingGlobalAgentModel,
    ]
]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with factory.begin() as session:
        await seed_equipment_catalog(session)
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
    global_model = TrackingGlobalAgentModel()
    workspace = TelegramAgentWorkspace(model=global_model)
    yield (
        CoachBotApplicationService(
            onboarding=onboarding,
            profiles=ProfileService(factory),
            account_queries=AccountQueryService(factory),
            accounts=AccountService(factory),
            apple_health=None,
            agent_workspace=workspace,
        ),
        factory,
        global_model,
    )
    await workspace.aclose()
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


def _reply_buttons(response: TelegramResponse) -> list[list[str]]:
    if response.user_keyboard is None:
        return []
    return [[button.text for button in row] for row in response.user_keyboard.keyboard]


@pytest.mark.asyncio
async def test_journey_collects_profile_goal_and_required_context_before_completion(
    journey: tuple[
        CoachBotApplicationService,
        async_sessionmaker[AsyncSession],
        TrackingGlobalAgentModel,
    ],
) -> None:
    bot, factory, global_model = journey
    athlete = _athlete()

    welcome = await bot.start(athlete)
    consent = await bot.handle_callback(athlete, "nav:v1:consent")
    setup = await bot.handle_callback(athlete, "ob:v1:consent")
    birth_year = await bot.handle_callback(athlete, "ob:v1:profile")
    gender = await bot.handle_text(athlete, "1990")
    weight = await bot.handle_callback(
        athlete,
        "ob:v1:profile:gender:FEMALE",
    )
    height = await bot.handle_text(athlete, "62.5")
    intake = await bot.handle_text(athlete, "168")
    confirmation = await bot.handle_text(
        athlete,
        "I want to complete a marathon and finish safely.",
    )
    updated = await bot.handle_text(athlete, "I also want to maintain strength.")
    availability = await bot.handle_callback(athlete, "ob:v1:goal:confirm")
    equipment = await bot.handle_text(
        athlete,
        "Tuesday and Thursday evenings, plus a longer Saturday run.",
    )
    equipment_callback = next(
        callback
        for label, callback in _buttons(equipment)
        if "Trail running shoes" in label
    )
    await bot.handle_callback(athlete, equipment_callback)
    limitations = await bot.handle_callback(athlete, "ob:v1:equipment:done")
    completed = await bot.handle_callback(athlete, "ob:v1:health:none")
    displayed_profile = await bot.profile(athlete)
    profile_button = await _agent_input(bot, "Profile")

    assert welcome.text == messages.WELCOME
    assert consent.text == messages.SETUP_INTRODUCTION
    assert setup.text == messages.SETUP_INTRODUCTION
    assert intake.text == messages.GOAL_INTAKE
    assert ("Cancel", "ob:v1:cancel") in _buttons(intake)
    assert confirmation.text.startswith("Here\u2019s what I understood:")
    assert "Maintain strength" in updated.text
    assert birth_year.text == messages.PROFILE_BIRTH_YEAR_INTAKE
    assert gender.text == messages.PROFILE_GENDER_INTAKE
    assert ("Female", "ob:v1:profile:gender:FEMALE") in _buttons(gender)
    assert weight.text == messages.PROFILE_WEIGHT_INTAKE
    assert height.text == messages.PROFILE_HEIGHT_INTAKE
    assert availability.text == messages.AVAILABILITY_INTAKE
    assert "essential means" in equipment.text.casefold()
    assert messages.HEALTH_LIMITATIONS_INTAKE in limitations.text
    assert completed.text == messages.ONBOARDING_COMPLETED
    assert "Birth year: 1990" in displayed_profile.text
    assert "Category: Female" in displayed_profile.text
    assert "Trail running shoes" in displayed_profile.text
    assert "<pre>" in displayed_profile.text
    assert "<b>Training goal</b>" in displayed_profile.text
    assert "Main goal: Complete a marathon" in displayed_profile.text
    assert "Target outcome: Finish safely" in displayed_profile.text
    assert "Secondary priority: Maintain strength" in displayed_profile.text
    assert "Event date: Not set" in displayed_profile.text
    assert "Status: Confirmed" in displayed_profile.text
    assert "Original description" not in displayed_profile.text
    assert "Trail running shoes" in profile_button.text
    assert _reply_buttons(profile_button) == [
        ["Profile", "Change profile"],
        ["Delete"],
    ]

    async with factory() as session:
        user = await UserRepository(session).get_by_telegram_id(
            athlete.telegram_user_id
        )
        assert user is not None
        assert user.status is UserStatus.ONBOARDING_COMPLETED
        state = await OnboardingRepository(session).require_for_user(user_id=user.id)
        assert state.status is OnboardingStatus.COMPLETED
        assert state.current_step is OnboardingStep.HEALTH_LIMITATIONS_INTAKE
        goal = await ProfileRepository(session).get_training_goal(user_id=user.id)
        assert goal is not None
        assert goal.main_goal == "Complete a marathon"
        assert goal.target_outcome == "Finish safely"
        assert goal.secondary_priority == "Maintain strength"
        original_description = goal.original_description
        profile = await ProfileRepository(session).get_athlete_profile(user_id=user.id)
        assert profile is not None
        assert profile.birth_year == 1990
        assert profile.gender is AthleteGender.FEMALE
        assert profile.weight_kg == 62.5
        assert profile.height_cm == 168.0
        assert profile.availability_text == (
            "Tuesday and Thursday evenings, plus a longer Saturday run."
        )
        assert profile.health_limitations_text == "NONE_REPORTED"
        selected_equipment = await EquipmentRepository(session).selected_catalog(
            athlete_id=user.id
        )
        assert {item.equipment for item in selected_equipment} == {
            "trail_running_shoes"
        }

    back = await bot.handle_callback(athlete, "nav:v1:welcome")
    assert back.text == messages.WELCOME

    settings = await _agent_input(bot, "Change profile")
    assert settings.text == messages.PROFILE_SETTINGS_MENU

    goal_menu = await bot.handle_callback(athlete, "ps:v1:section:goal")
    assert goal_menu.text == messages.PROFILE_GOAL_MENU
    assert {label for label, _ in _buttons(goal_menu)} >= {
        "Main goal",
        "Target outcome",
        "Event date",
        "Secondary priority",
    }
    goal = await bot.handle_callback(athlete, "ps:v1:goal:main")
    assert "Complete a marathon" in goal.text
    await _agent_input(bot, "Complete a marathon")

    await bot.handle_callback(athlete, "ps:v1:section:goal")
    secondary = await bot.handle_callback(athlete, "ps:v1:goal:secondary")
    assert "Maintain strength" in secondary.text
    goal_saved = await _agent_input(bot, "Maintain mobility")
    assert "Saved: Goal." in goal_saved.text
    await bot.handle_callback(athlete, "ps:v1:back")

    availability_current = await bot.handle_callback(
        athlete, "ps:v1:section:availability"
    )
    assert "Tuesday and Thursday evenings, plus a longer Saturday run." in (
        availability_current.text
    )
    availability_closed = await bot.handle_callback(athlete, "ps:v1:done")
    assert availability_closed.text == messages.PROFILE_SETTINGS_CLOSED

    equipment_current = await bot.handle_callback(athlete, "ps:v1:section:equipment")
    assert "Have" in equipment_current.text
    assert "Trail running shoes" in equipment_current.text
    await bot.handle_callback(athlete, "ps:v1:back")

    health_current = await bot.handle_callback(athlete, "ps:v1:section:health")
    assert "None reported" in health_current.text
    await bot.handle_callback(athlete, "ps:v1:done")

    await bot.handle_callback(athlete, "ps:v1:section:personal")
    birth_year_current = await bot.handle_callback(athlete, "ps:v1:personal:birth_year")
    assert "1990" in birth_year_current.text
    await bot.handle_callback(athlete, "ps:v1:back")
    await bot.handle_callback(athlete, "ps:v1:section:personal")
    category_current = await bot.handle_callback(athlete, "ps:v1:personal:gender")
    assert "Female" in category_current.text
    await bot.handle_callback(athlete, "ps:v1:back")
    await bot.handle_callback(athlete, "ps:v1:section:personal")
    weight_current = await bot.handle_callback(athlete, "ps:v1:personal:weight")
    assert "62.5 kg" in weight_current.text
    await bot.handle_callback(athlete, "ps:v1:back")
    await bot.handle_callback(athlete, "ps:v1:section:personal")
    height_current = await bot.handle_callback(athlete, "ps:v1:personal:height")
    assert "168.0 cm" in height_current.text
    correction = await _agent_input(bot, "170")
    assert "height" in correction.text.casefold()
    assert global_model.invocations == 0

    async with factory() as session:
        user = await UserRepository(session).get_by_telegram_id(
            athlete.telegram_user_id
        )
        assert user is not None
        profile = await ProfileRepository(session).get_athlete_profile(user_id=user.id)
        assert profile is not None
        assert profile.height_cm == 170.0
        goal = await ProfileRepository(session).get_training_goal(user_id=user.id)
        assert goal is not None
        assert goal.secondary_priority == "Maintain mobility"
        assert goal.original_description == original_description


async def _agent_input(
    bot: CoachBotApplicationService,
    content: str,
    *,
    event_type: str = "text",
) -> TelegramResponse:
    return await bot.handle_agent_input(
        _athlete(),
        HumanMessage(
            content=content,
            additional_kwargs={"telegram_event_type": event_type},
        ),
    )


@pytest.mark.asyncio
async def test_recreated_account_routes_goal_text_to_goal_workflow(
    journey: tuple[
        CoachBotApplicationService,
        async_sessionmaker[AsyncSession],
        TrackingGlobalAgentModel,
    ],
) -> None:
    bot, _, global_model = journey

    await _agent_input(bot, "Start")
    deletion_prompt = await _agent_input(bot, "Delete")
    deleted = await _agent_input(
        bot,
        "acct:v1:delete:confirm",
        event_type="callback",
    )
    restarted = await _agent_input(bot, "Start")
    resumed = await _agent_input(bot, "Resume")
    await _agent_input(bot, "nav:v1:consent", event_type="callback")
    await _agent_input(bot, "ob:v1:consent", event_type="callback")
    birth_year = await _agent_input(bot, "ob:v1:profile", event_type="callback")
    await _agent_input(bot, "1990")
    await _agent_input(
        bot,
        "ob:v1:profile:gender:FEMALE",
        event_type="callback",
    )
    await _agent_input(bot, "62.5")
    intake = await _agent_input(bot, "168")
    goal = await _agent_input(
        bot,
        "I want to complete an Ironman 70.3 next July",
    )

    assert deletion_prompt.text == messages.DELETE_CONFIRM
    assert deleted.text == messages.DELETED
    assert _reply_buttons(deleted) == [["Start"]]
    assert deleted.edit_existing is False
    assert restarted.text == messages.WELCOME
    assert _reply_buttons(restarted) == [["Resume"], ["Delete"]]
    assert _reply_buttons(resumed) == [["Resume"], ["Delete"]]
    assert birth_year.text == messages.PROFILE_BIRTH_YEAR_INTAKE
    assert intake.text == messages.GOAL_INTAKE
    assert goal.text.startswith("Here\u2019s what I understood:")
    assert global_model.invocations == 0
