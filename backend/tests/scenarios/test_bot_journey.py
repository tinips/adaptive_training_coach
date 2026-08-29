"""End-to-end Telegram scenario for the retained onboarding journey."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date

import pytest
import pytest_asyncio
from catalog_seed import seed_training_catalog
from langchain_core.messages import HumanMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.bot import messages
from app.bot.rendering import TelegramResponse
from app.bot.service import CoachBotApplicationService
from app.config import Settings
from app.db.base import Base
from app.db.models import OnboardingSession, TrainingGoal
from app.domain.enums import AthleteGender, OnboardingStatus, OnboardingStep, UserStatus
from app.repositories.athlete_capabilities import AthleteCapabilityRepository
from app.repositories.onboarding import OnboardingRepository
from app.repositories.profiles import ProfileRepository
from app.repositories.users import UserRepository
from app.schemas.common import TelegramIdentity
from app.services.accounts import AccountQueryService, AccountService
from app.services.onboarding import OnboardingApplicationError, OnboardingService
from app.services.profiles import ProfileService


@pytest_asyncio.fixture
async def journey() -> AsyncIterator[
    tuple[
        CoachBotApplicationService,
        async_sessionmaker[AsyncSession],
    ]
]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with factory.begin() as session:
        await seed_training_catalog(session)
    onboarding = OnboardingService(
        session_factory=factory,
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
            apple_health=None,
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


def _reply_buttons(response: TelegramResponse) -> list[list[str]]:
    if response.user_keyboard is None:
        return []
    return [[button.text for button in row] for row in response.user_keyboard.keyboard]


@pytest.mark.asyncio
async def test_journey_collects_profile_goal_and_required_context_before_completion(
    journey: tuple[
        CoachBotApplicationService,
        async_sessionmaker[AsyncSession],
    ],
) -> None:
    bot, factory = journey
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
    sport_choice = await bot.handle_callback(athlete, "ob:v1:goal:sport:TRIATHLON")
    template_choice = await bot.handle_callback(
        athlete, "ob:v1:goal:template:TRIATHLON_HALF_DISTANCE"
    )
    date_skipped = await bot.handle_callback(athlete, "ob:v1:goal:nodate")
    availability = await bot.handle_callback(
        athlete, "ob:v1:support:STRENGTH_MAINTENANCE"
    )
    equipment = await bot.handle_text(
        athlete,
        "Tuesday and Thursday evenings, plus a longer Saturday run.",
    )
    equipment_callback = next(
        callback for label, callback in _buttons(equipment) if "Running shoes" in label
    )
    await bot.handle_callback(athlete, equipment_callback)
    limitations = await bot.handle_callback(athlete, "ob:v1:equipment:done")
    past_injuries = await bot.handle_callback(athlete, "ob:v1:health:none")
    history = await bot.handle_callback(athlete, "ob:v1:health:none")
    completed = await bot.handle_callback(athlete, "ob:v1:history:skip")
    displayed_profile = await bot.profile(athlete)
    profile_button = await _agent_input(bot, "Profile")

    assert welcome.text == messages.WELCOME
    assert consent.text == messages.SETUP_INTRODUCTION
    assert setup.text == messages.SETUP_INTRODUCTION
    assert intake.text == messages.GOAL_INTAKE
    assert ("Triathlon", "ob:v1:goal:sport:TRIATHLON") in _buttons(intake)
    assert ("Cancel", "ob:v1:cancel") in _buttons(intake)
    assert sport_choice.text == messages.GOAL_TEMPLATE_PROMPT
    assert (
        "Half-distance triathlon",
        "ob:v1:goal:template:TRIATHLON_HALF_DISTANCE",
    ) in _buttons(sport_choice)
    assert ("Back", "ob:v1:goal:back") in _buttons(sport_choice)
    assert template_choice.text == messages.GOAL_EVENT_DATE_PROMPT
    assert ("No date yet", "ob:v1:goal:nodate") in _buttons(template_choice)
    assert date_skipped.text == messages.GOAL_SUPPORT_PROMPT
    assert ("Maintain strength", "ob:v1:support:STRENGTH_MAINTENANCE") in _buttons(
        date_skipped
    )
    assert ("No supporting goal", "ob:v1:support:none") in _buttons(date_skipped)
    assert birth_year.text == messages.PROFILE_BIRTH_YEAR_INTAKE
    assert gender.text == messages.PROFILE_GENDER_INTAKE
    assert ("Female", "ob:v1:profile:gender:FEMALE") in _buttons(gender)
    assert weight.text == messages.PROFILE_WEIGHT_INTAKE
    assert height.text == messages.PROFILE_HEIGHT_INTAKE
    assert availability.text == messages.AVAILABILITY_INTAKE
    assert "select every resource" in equipment.text.casefold()
    assert messages.HEALTH_LIMITATIONS_INTAKE in limitations.text
    assert past_injuries.text == messages.PAST_INJURIES_INTAKE
    assert history.text == messages.TRAINING_HISTORY_IMPORT
    assert completed.text == messages.TRAINING_HISTORY_SKIP_SUGGESTION
    assert "Birth year: 1990" in displayed_profile.text
    assert "Category: Female" in displayed_profile.text
    assert "Running shoes" in displayed_profile.text
    assert "<pre>" in displayed_profile.text
    assert "<b>Training goal</b>" in displayed_profile.text
    assert "Main goal: Half-distance triathlon" in displayed_profile.text
    assert "Target outcome: Half-distance triathlon" in displayed_profile.text
    assert "Secondary priority: Maintain strength" in displayed_profile.text
    assert "Event date: Not set" in displayed_profile.text
    assert "Status: Confirmed" in displayed_profile.text
    assert "Running shoes" in profile_button.text
    assert _reply_buttons(profile_button) == [
        ["Profile", "Change profile"],
        ["Add workout", "Plan next week"],
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
        assert state.current_step is OnboardingStep.TRAINING_HISTORY_IMPORT
        goal = await ProfileRepository(session).get_training_goal(user_id=user.id)
        assert goal is not None
        assert goal.main_goal == "Half-distance triathlon"
        assert goal.target_outcome == "Half-distance triathlon"
        assert goal.secondary_priority == "Maintain strength"
        assert goal.goal_template_id is not None
        assert goal.supporting_goal_template_id is not None
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
        assert profile.health_limitations_text == (
            "Current limitations: NONE_REPORTED\nPast injuries: NONE_REPORTED"
        )
        selected_equipment = await AthleteCapabilityRepository(session).available(
            athlete_id=user.id
        )
        assert {item.code for item in selected_equipment} == {"running_shoes"}

    back = await bot.handle_callback(athlete, "nav:v1:welcome")
    assert back.text == messages.WELCOME

    settings = await _agent_input(bot, "Change profile")
    assert settings.text == messages.PROFILE_SETTINGS_MENU

    goal_menu = await bot.handle_callback(athlete, "ps:v1:section:goal")
    assert goal_menu.text == messages.PROFILE_GOAL_MENU
    # Main goal and secondary priority are edited through the same
    # deterministic catalog menu as onboarding (ps:v1:goal:main /
    # ps:v1:goal:secondary), not as free text; see the dedicated tests below.
    assert {label for label, _ in _buttons(goal_menu)} == {
        "Main goal",
        "Target outcome",
        "Event date",
        "Secondary priority",
        "Back",
    }

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
    assert "Running shoes" in equipment_current.text
    await bot.handle_callback(athlete, "ps:v1:back")

    health_current = await bot.handle_callback(athlete, "ps:v1:section:health")
    assert "Current limitations: NONE_REPORTED" in health_current.text
    assert "Past injuries: NONE_REPORTED" in health_current.text
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
        # Unchanged: there is no free-text goal edit any more.
        assert goal.secondary_priority == "Maintain strength"
        assert goal.original_description == original_description


@pytest.mark.asyncio
async def test_a_single_sport_athlete_can_skip_the_supporting_goal(
    journey: tuple[
        CoachBotApplicationService,
        async_sessionmaker[AsyncSession],
    ],
) -> None:
    """Spec section 1.1: a single-sport athlete (here, running) is first-class."""

    bot, factory = journey
    athlete = _athlete()

    await bot.start(athlete)
    await bot.handle_callback(athlete, "nav:v1:consent")
    await bot.handle_callback(athlete, "ob:v1:consent")
    await bot.handle_callback(athlete, "ob:v1:profile")
    await bot.handle_text(athlete, "1988")
    await bot.handle_callback(athlete, "ob:v1:profile:gender:MALE")
    await bot.handle_text(athlete, "78")
    await bot.handle_text(athlete, "180")
    template_choice = await bot.handle_callback(athlete, "ob:v1:goal:sport:RUNNING")
    date_prompt = await bot.handle_callback(athlete, "ob:v1:goal:template:MARATHON")
    support_choice = await bot.handle_callback(athlete, "ob:v1:goal:nodate")
    skipped = await bot.handle_callback(athlete, "ob:v1:support:none")

    assert ("Marathon", "ob:v1:goal:template:MARATHON") in _buttons(template_choice)
    assert date_prompt.text == messages.GOAL_EVENT_DATE_PROMPT
    assert support_choice.text == messages.GOAL_SUPPORT_PROMPT
    assert skipped.text == messages.AVAILABILITY_INTAKE

    async with factory() as session:
        goal = await session.scalar(select(TrainingGoal))
        assert goal is not None
        assert goal.main_goal == "Marathon"
        assert goal.target_outcome == "Marathon"
        assert goal.secondary_priority is None
        assert goal.goal_template_id is not None
        assert goal.supporting_goal_template_id is None


async def _onboard_to_completed(
    bot: CoachBotApplicationService, athlete: TelegramIdentity
) -> None:
    """Walk a fresh athlete to ONBOARDING_COMPLETED with MARATHON, no support."""

    await bot.start(athlete)
    await bot.handle_callback(athlete, "nav:v1:consent")
    await bot.handle_callback(athlete, "ob:v1:consent")
    await bot.handle_callback(athlete, "ob:v1:profile")
    await bot.handle_text(athlete, "1990")
    await bot.handle_callback(athlete, "ob:v1:profile:gender:FEMALE")
    await bot.handle_text(athlete, "62.5")
    await bot.handle_text(athlete, "168")
    await bot.handle_callback(athlete, "ob:v1:goal:sport:RUNNING")
    await bot.handle_callback(athlete, "ob:v1:goal:template:MARATHON")
    await bot.handle_callback(athlete, "ob:v1:goal:nodate")
    await bot.handle_callback(athlete, "ob:v1:support:none")
    equipment = await bot.handle_text(
        athlete, "Weekday evenings and a longer Saturday run."
    )
    equipment_callback = next(
        callback for label, callback in _buttons(equipment) if "Running shoes" in label
    )
    await bot.handle_callback(athlete, equipment_callback)
    await bot.handle_callback(athlete, "ob:v1:equipment:done")
    await bot.handle_callback(athlete, "ob:v1:health:none")
    await bot.handle_callback(athlete, "ob:v1:health:none")
    await bot.handle_callback(athlete, "ob:v1:history:skip")


async def _onboard_to_goal_chosen(
    journey: tuple[
        CoachBotApplicationService,
        async_sessionmaker[AsyncSession],
    ],
) -> tuple[
    CoachBotApplicationService, TelegramIdentity, async_sessionmaker[AsyncSession]
]:
    """Walk a fresh athlete from consent through to the sport choice.

    The brief's own version of this helper takes a `database` fixture that
    does not exist in this file; this file's fixture is `journey`, which
    already bundles the bot service with the session factory, so this walks
    the same steps against that instead.
    """

    bot, factory = journey
    athlete = _athlete()
    await bot.start(athlete)
    await bot.handle_callback(athlete, "nav:v1:consent")
    await bot.handle_callback(athlete, "ob:v1:consent")
    await bot.handle_callback(athlete, "ob:v1:profile")
    await bot.handle_text(athlete, "1990")
    await bot.handle_callback(athlete, "ob:v1:profile:gender:FEMALE")
    await bot.handle_text(athlete, "62.5")
    await bot.handle_text(athlete, "168")
    await bot.handle_callback(athlete, "ob:v1:goal:sport:RUNNING")
    return bot, athlete, factory


@pytest.mark.asyncio
async def test_a_race_date_can_be_entered_and_skipped(
    journey: tuple[
        CoachBotApplicationService,
        async_sessionmaker[AsyncSession],
    ],
) -> None:
    """Without this the planner has no weeks-to-race and no phase."""

    bot, athlete, factory = await _onboard_to_goal_chosen(journey)

    await bot.handle_callback(athlete, "ob:v1:goal:template:MARATHON")
    await bot.handle_text(athlete, "2027-07-11")

    async with factory() as session:
        goal = await session.scalar(select(TrainingGoal))
    assert goal is not None
    assert goal.event_date == date(2027, 7, 11)


@pytest.mark.asyncio
async def test_an_unparseable_race_date_is_rejected_without_advancing(
    journey: tuple[
        CoachBotApplicationService,
        async_sessionmaker[AsyncSession],
    ],
) -> None:
    bot, athlete, factory = await _onboard_to_goal_chosen(journey)
    await bot.handle_callback(athlete, "ob:v1:goal:template:MARATHON")

    await bot.handle_text(athlete, "sometime next summer")

    async with factory() as session:
        onboarding = await session.scalar(select(OnboardingSession))
    assert onboarding is not None
    assert onboarding.current_step is OnboardingStep.GOAL_EVENT_DATE


@pytest.mark.asyncio
async def test_a_past_race_date_is_rejected_without_advancing(
    journey: tuple[
        CoachBotApplicationService,
        async_sessionmaker[AsyncSession],
    ],
) -> None:
    """A race that already happened is not a goal, same as unparseable text."""

    bot, athlete, factory = await _onboard_to_goal_chosen(journey)
    await bot.handle_callback(athlete, "ob:v1:goal:template:MARATHON")

    await bot.handle_text(athlete, "2020-01-01")

    async with factory() as session:
        onboarding = await session.scalar(select(OnboardingSession))
    assert onboarding is not None
    assert onboarding.current_step is OnboardingStep.GOAL_EVENT_DATE


@pytest.mark.asyncio
async def test_a_race_date_in_ddmmyyyy_format_is_parsed_correctly(
    journey: tuple[
        CoachBotApplicationService,
        async_sessionmaker[AsyncSession],
    ],
) -> None:
    """11/07/2027 must become 11 July 2027, not day/month swapped."""

    bot, athlete, factory = await _onboard_to_goal_chosen(journey)

    await bot.handle_callback(athlete, "ob:v1:goal:template:MARATHON")
    await bot.handle_text(athlete, "11/07/2027")

    async with factory() as session:
        goal = await session.scalar(select(TrainingGoal))
        onboarding = await session.scalar(select(OnboardingSession))
    assert goal is not None
    assert goal.event_date == date(2027, 7, 11)
    assert onboarding is not None
    assert onboarding.current_step is OnboardingStep.GOAL_CONFIRMED


@pytest.mark.asyncio
async def test_profile_settings_goal_main_walks_sport_then_template_then_offers_support(
    journey: tuple[
        CoachBotApplicationService,
        async_sessionmaker[AsyncSession],
    ],
) -> None:
    """Editing the main goal after onboarding uses the same deterministic menu."""

    bot, factory = journey
    athlete = _athlete()
    await _onboard_to_completed(bot, athlete)

    await bot.handle_callback(athlete, "ps:v1:section:goal")
    main = await bot.handle_callback(athlete, "ps:v1:goal:main")
    assert main.text == messages.PROFILE_GOAL_MAIN_SPORT
    assert ("Triathlon", "ps:v1:goal:sport:TRIATHLON") in _buttons(main)

    sport_choice = await bot.handle_callback(athlete, "ps:v1:goal:sport:TRIATHLON")
    assert sport_choice.text == messages.PROFILE_GOAL_MAIN_TEMPLATE
    assert (
        "Half-distance triathlon",
        "ps:v1:goal:template:TRIATHLON_HALF_DISTANCE",
    ) in _buttons(sport_choice)

    template_choice = await bot.handle_callback(
        athlete, "ps:v1:goal:template:TRIATHLON_HALF_DISTANCE"
    )
    assert "Saved: Main goal." in template_choice.text
    assert messages.PROFILE_GOAL_SECONDARY in template_choice.text
    assert ("Maintain muscle", "ps:v1:goal:support:MUSCLE_RETENTION") in _buttons(
        template_choice
    )

    support_choice = await bot.handle_callback(
        athlete, "ps:v1:goal:support:MUSCLE_RETENTION"
    )
    # Both the primary and supporting goal changed, and triathlon's training
    # contexts differ entirely from running's, so equipment & access reopens
    # instead of landing straight back on the goal menu.
    assert "Saved: Secondary priority." in support_choice.text
    equipment_buttons = _buttons(support_choice)
    assert any(
        callback.startswith("ps:v1:equipment:") for _, callback in equipment_buttons
    )

    done = await bot.handle_callback(athlete, "ps:v1:equipment:done")
    assert "Saved: Equipment & access." in done.text
    assert messages.PROFILE_SETTINGS_MENU in done.text

    async with factory() as session:
        goal = await session.scalar(select(TrainingGoal))
        assert goal is not None
        assert goal.main_goal == "Half-distance triathlon"
        assert goal.target_outcome == "Half-distance triathlon"
        assert goal.secondary_priority == "Maintain muscle"


@pytest.mark.asyncio
async def test_profile_settings_secondary_priority_is_a_direct_entry_point(
    journey: tuple[
        CoachBotApplicationService,
        async_sessionmaker[AsyncSession],
    ],
) -> None:
    """Editing just the support does not require re-choosing the primary goal."""

    bot, factory = journey
    athlete = _athlete()
    await _onboard_to_completed(bot, athlete)

    await bot.handle_callback(athlete, "ps:v1:section:goal")
    secondary = await bot.handle_callback(athlete, "ps:v1:goal:secondary")
    assert secondary.text == messages.PROFILE_GOAL_SECONDARY
    assert ("No supporting goal", "ps:v1:goal:support:none") in _buttons(secondary)

    added = await bot.handle_callback(
        athlete, "ps:v1:goal:support:STRENGTH_MAINTENANCE"
    )
    # The supporting goal changed, so equipment & access reopens even though
    # this entry point never touched the primary goal.
    assert "Saved: Secondary priority." in added.text
    added_buttons = _buttons(added)
    assert any(callback.startswith("ps:v1:equipment:") for _, callback in added_buttons)
    await bot.handle_callback(athlete, "ps:v1:equipment:done")

    async with factory() as session:
        goal = await session.scalar(select(TrainingGoal))
        assert goal is not None
        # Unchanged: this entry point never touches the primary goal.
        assert goal.main_goal == "Marathon"
        assert goal.secondary_priority == "Maintain strength"

    await bot.handle_callback(athlete, "ps:v1:section:goal")
    await bot.handle_callback(athlete, "ps:v1:goal:secondary")
    removed = await bot.handle_callback(athlete, "ps:v1:goal:support:none")
    # Removing it is a change too: equipment & access reopens again.
    assert "Saved: Secondary priority." in removed.text
    removed_buttons = _buttons(removed)
    assert any(
        callback.startswith("ps:v1:equipment:") for _, callback in removed_buttons
    )
    await bot.handle_callback(athlete, "ps:v1:equipment:done")

    async with factory() as session:
        goal = await session.scalar(select(TrainingGoal))
        assert goal is not None
        assert goal.secondary_priority is None
        assert goal.supporting_goal_template_id is None


@pytest.mark.asyncio
async def test_profile_settings_goal_edit_skips_equipment_review_when_nothing_changed(
    journey: tuple[
        CoachBotApplicationService,
        async_sessionmaker[AsyncSession],
    ],
) -> None:
    """Re-confirming the same goal shows no save notice and never reopens equipment."""

    bot, factory = journey
    athlete = _athlete()
    await _onboard_to_completed(bot, athlete)

    await bot.handle_callback(athlete, "ps:v1:section:goal")
    await bot.handle_callback(athlete, "ps:v1:goal:main")
    await bot.handle_callback(athlete, "ps:v1:goal:sport:RUNNING")
    template_choice = await bot.handle_callback(athlete, "ps:v1:goal:template:MARATHON")
    # Re-picking the identical template is not a change: no save notice.
    assert template_choice.text == messages.PROFILE_GOAL_SECONDARY

    support_choice = await bot.handle_callback(athlete, "ps:v1:goal:support:none")
    # The supporting goal was already unset, so nothing changed at all:
    # straight back to the goal menu, no equipment review, no save notice.
    assert support_choice.text == messages.PROFILE_GOAL_MENU

    async with factory() as session:
        goal = await session.scalar(select(TrainingGoal))
        assert goal is not None
        assert goal.main_goal == "Marathon"
        assert goal.secondary_priority is None

    # Same story through the direct secondary-priority entry point.
    await bot.handle_callback(athlete, "ps:v1:section:goal")
    await bot.handle_callback(athlete, "ps:v1:goal:secondary")
    unchanged = await bot.handle_callback(athlete, "ps:v1:goal:support:none")
    assert unchanged.text == messages.PROFILE_GOAL_MENU


@pytest.mark.asyncio
async def test_profile_settings_goal_main_back_navigation_discards_the_pending_sport(
    journey: tuple[
        CoachBotApplicationService,
        async_sessionmaker[AsyncSession],
    ],
) -> None:
    bot, factory = journey
    athlete = _athlete()
    await _onboard_to_completed(bot, athlete)

    await bot.handle_callback(athlete, "ps:v1:section:goal")
    await bot.handle_callback(athlete, "ps:v1:goal:main")
    await bot.handle_callback(athlete, "ps:v1:goal:sport:CYCLING")

    # Step back from the template screen to the sport screen: still in the
    # goal-main mini-flow, the pending sport is discarded.
    reopened = await bot.handle_callback(athlete, "ps:v1:goal:main:back")
    assert reopened.text == messages.PROFILE_GOAL_MAIN_SPORT

    # The full escape hatch: back out to the goal menu entirely.
    cancelled = await bot.handle_callback(athlete, "ps:v1:goal:back")
    assert cancelled.text == messages.PROFILE_GOAL_MENU

    async with factory() as session:
        goal = await session.scalar(select(TrainingGoal))
        assert goal is not None
        # Nothing was ever confirmed, so the original goal survives untouched.
        assert goal.main_goal == "Marathon"


@pytest.mark.asyncio
async def test_profile_settings_event_date_in_ddmmyyyy_format_is_parsed_correctly(
    journey: tuple[
        CoachBotApplicationService,
        async_sessionmaker[AsyncSession],
    ],
) -> None:
    """The profile-settings edit path parses dates the same way onboarding does."""

    bot, factory = journey
    athlete = _athlete()
    await _onboard_to_completed(bot, athlete)

    await bot.handle_callback(athlete, "ps:v1:section:goal")
    await bot.handle_callback(athlete, "ps:v1:goal:date")
    saved = await _agent_input(bot, "11/07/2027")

    assert "Saved: Goal." in saved.text

    async with factory() as session:
        goal = await session.scalar(select(TrainingGoal))
    assert goal is not None
    assert goal.event_date == date(2027, 7, 11)


@pytest.mark.asyncio
async def test_profile_settings_event_date_rejects_a_past_date(
    journey: tuple[
        CoachBotApplicationService,
        async_sessionmaker[AsyncSession],
    ],
) -> None:
    """A race that already happened is rejected here the same as in onboarding."""

    bot, factory = journey
    athlete = _athlete()
    await _onboard_to_completed(bot, athlete)

    await bot.handle_callback(athlete, "ps:v1:section:goal")
    await bot.handle_callback(athlete, "ps:v1:goal:date")

    with pytest.raises(OnboardingApplicationError, match="invalid_event_date"):
        await _agent_input(bot, "2020-01-01")

    async with factory() as session:
        goal = await session.scalar(select(TrainingGoal))
    assert goal is not None
    assert goal.event_date is None


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
async def test_recreated_account_reaches_the_deterministic_goal_menu(
    journey: tuple[
        CoachBotApplicationService,
        async_sessionmaker[AsyncSession],
    ],
) -> None:
    bot, _ = journey

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
        "ob:v1:goal:sport:TRIATHLON",
        event_type="callback",
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
    assert goal.text == messages.GOAL_TEMPLATE_PROMPT


def test_the_bot_service_takes_no_agent_workspace() -> None:
    """After this task nothing in the bot may call a model."""

    import inspect

    from app.bot.service import CoachBotApplicationService

    assert (
        "agent_workspace"
        not in inspect.signature(CoachBotApplicationService.__init__).parameters
    )
