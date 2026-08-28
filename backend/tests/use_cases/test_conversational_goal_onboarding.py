"""Focused persistence tests for the conversational onboarding goal step."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import date
from uuid import UUID

import pytest
import pytest_asyncio
from catalog_seed import seed_training_catalog
from pydantic import JsonValue
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import Settings
from app.db.base import Base
from app.db.models import Capability, GoalTemplate, TrainingContext
from app.domain.enums import (
    OnboardingStatus,
    OnboardingStep,
    ProfileSettingsStep,
    TrainingGoalStatus,
    UserStatus,
)
from app.integrations.llm.models import (
    GoalExtractionAction,
    GoalExtractionOutput,
    GoalExtractionPatch,
    GoalTemplateSummary,
)
from app.repositories.onboarding import OnboardingRepository
from app.repositories.profiles import ProfileRepository
from app.repositories.users import UserRepository
from app.schemas.common import TelegramIdentity
from app.schemas.onboarding_goal import (
    GoalExtractionWorkflowResult,
    OnboardingModificationWorkflowResult,
    OnboardingUpdateHandler,
)
from app.services.onboarding import OnboardingApplicationError, OnboardingService


@pytest_asyncio.fixture
async def goal_database() -> AsyncIterator[
    tuple[AsyncEngine, async_sessionmaker[AsyncSession]]
]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with factory.begin() as session:
        await seed_training_catalog(session)
    yield engine, factory
    await engine.dispose()


@dataclass
class QueueGoalExtractor:
    results: list[GoalExtractionWorkflowResult]
    calls: list[tuple[GoalExtractionAction, str, GoalExtractionOutput | None]] = field(
        default_factory=list
    )
    current_dates: list[str] = field(default_factory=list)
    modification_updates: list[dict[str, JsonValue]] = field(default_factory=list)
    modification_calls: list[tuple[UUID, str]] = field(default_factory=list)

    async def extract(
        self,
        *,
        user_id: UUID,
        action: GoalExtractionAction,
        user_text: str,
        existing_draft: GoalExtractionOutput | None,
        goal_catalog: tuple[GoalTemplateSummary, ...],
        current_date: str,
    ) -> GoalExtractionWorkflowResult:
        del user_id, goal_catalog
        self.calls.append((action, user_text, existing_draft))
        self.current_dates.append(current_date)
        return self.results.pop(0)

    async def modify_onboarding_data(
        self,
        *,
        user_id: UUID,
        user_text: str,
        onboarding_updater: OnboardingUpdateHandler,
    ) -> OnboardingModificationWorkflowResult:
        self.modification_calls.append((user_id, user_text))
        update = self.modification_updates.pop(0)
        saved = await onboarding_updater(user_id=user_id, payload=update)
        return OnboardingModificationWorkflowResult(
            outcome="onboarding_modified",
            confirmation=f"Updated: {', '.join(saved.updated_fields)}.",
            updated_fields=tuple(saved.updated_fields),
        )


def identity(telegram_id: int = 6201) -> TelegramIdentity:
    return TelegramIdentity(
        telegram_user_id=telegram_id,
        telegram_username="goal_runner",
        first_name="Goal Runner",
        language_code="en",
    )


def settings() -> Settings:
    return Settings(
        environment="test",
        database_url="sqlite+aiosqlite:///:memory:",
        llm_mode="mock",
    )


def extracted(
    *,
    main_goal: str | None,
    target_outcome: str | None,
    event_date: str | None = None,
    secondary_priority: str | None = None,
    missing_fields: list[str] | None = None,
    ambiguous_fields: list[str] | None = None,
    message_status: str = "COMPLETE",
) -> GoalExtractionWorkflowResult:
    primary_template: dict[str, object] | None = None
    if main_goal is not None:
        folded = main_goal.casefold()
        known = next(
            (
                code
                for phrase, code in (
                    ("70.3", "TRIATHLON_HALF_DISTANCE"),
                    ("half ironman", "TRIATHLON_HALF_DISTANCE"),
                    ("marathon", "MARATHON"),
                    ("trail", "TRAIL_RACE"),
                    ("running", "GENERAL_RUNNING"),
                    ("run", "GENERAL_RUNNING"),
                    ("5 km", "RUNNING_5K"),
                    ("cycling", "ROAD_CYCLING_EVENT"),
                )
                if phrase in folded
            ),
            None,
        )
        # There is no catalog expansion any more: the fake extractor only ever
        # proposes a code already in the canonical catalog.
        if known is None:
            raise ValueError(
                f"no canonical goal template code known for main_goal={main_goal!r}"
            )
        primary_template = {
            "decision": "USE_EXISTING",
            "code": known,
            "display_name": None,
            "description": None,
        }
    supporting_template: dict[str, object] | None = None
    if secondary_priority is not None:
        code = (
            "MUSCLE_RETENTION"
            if "muscle" in secondary_priority.casefold()
            else "STRENGTH_MAINTENANCE"
        )
        supporting_template = {
            "decision": "USE_EXISTING",
            "code": code,
            "display_name": None,
            "description": None,
        }
    return GoalExtractionWorkflowResult(
        outcome="extracted",
        goal_patch=GoalExtractionPatch.model_validate(
            {
                "main_goal": main_goal,
                "event_date": event_date,
                "target_outcome": target_outcome,
                "secondary_priority": secondary_priority,
                "primary_template": primary_template,
                "supporting_template": supporting_template,
                "missing_fields": missing_fields or [],
                "ambiguous_fields": ambiguous_fields or [],
                "message_status": message_status,
            }
        ),
    )


def service(
    factory: async_sessionmaker[AsyncSession],
    extractor: QueueGoalExtractor,
) -> OnboardingService:
    return OnboardingService(
        session_factory=factory,
        goal_extractor=extractor,
        settings=settings(),
    )


async def start_goal(onboarding: OnboardingService, athlete: TelegramIdentity) -> None:
    await onboarding.start(athlete)
    introduction = await onboarding.confirm_consent(athlete)
    assert introduction.kind == "setup_introduction"
    result = await onboarding.start_profile(athlete)
    assert result.kind == "profile_birth_year_intake"
    gender = await onboarding.handle_text(athlete, "1990")
    assert gender.kind == "profile_gender_intake"
    weight = await onboarding.choose_gender(athlete, "FEMALE")
    assert weight.kind == "profile_weight_intake"
    height = await onboarding.handle_text(athlete, "70")
    assert height.kind == "profile_height_intake"
    result = await onboarding.handle_text(athlete, "170")
    assert result.kind == "goal_intake"
    assert result.current_step is OnboardingStep.GOAL_INTAKE


@pytest.mark.asyncio
async def test_raw_goal_is_retained_and_complete_draft_waits_for_confirmation(
    goal_database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = goal_database
    extractor = QueueGoalExtractor(
        [
            extracted(
                main_goal="Complete a first Ironman 70.3",
                event_date="2027-07-18",
                target_outcome="Finish safely",
                secondary_priority="Maintain muscle mass",
            )
        ]
    )
    onboarding = service(factory, extractor)
    athlete = identity()
    await start_goal(onboarding, athlete)
    raw = (
        "I want to complete my first Ironman 70.3 on July 18, 2027, "
        "finish safely and maintain muscle."
    )

    result = await onboarding.handle_text(athlete, raw)

    assert result.kind == "goal_confirmation"
    assert result.answers["raw_goal_text"] == raw
    assert result.answers["goal_messages"] == [raw]
    draft = result.answers["goal_draft"]
    assert draft["main_goal"] == "Complete a first Ironman 70.3"
    assert draft["event_date"] == "2027-07-18"
    assert draft["target_outcome"] == "Finish safely"
    assert draft["secondary_priority"] == "Maintain muscle mass"
    assert draft["primary_template"]["code"] == "TRIATHLON_HALF_DISTANCE"
    assert draft["supporting_template"]["code"] == "MUSCLE_RETENTION"
    assert extractor.calls == [("CREATE_GOAL", raw, None)]
    assert extractor.current_dates == [date.today().isoformat()]
    async with factory() as session:
        assert (
            await ProfileRepository(session).get_training_goal(user_id=result.user_id)
            is None
        )


@pytest.mark.asyncio
async def test_vague_and_off_topic_answers_do_not_create_false_goal_data(
    goal_database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = goal_database
    extractor = QueueGoalExtractor(
        [
            extracted(
                main_goal="Train to run",
                target_outcome=None,
                missing_fields=["main_goal", "target_outcome"],
                message_status="NEEDS_CLARIFICATION",
            ),
            extracted(
                main_goal="Buy running shoes",
                target_outcome="Choose shoes",
                message_status="OFF_TOPIC",
            ),
        ]
    )
    onboarding = service(factory, extractor)
    athlete = identity(6202)
    await start_goal(onboarding, athlete)

    vague = await onboarding.handle_text(athlete, "I want to train to run.")
    unrelated = await onboarding.handle_text(athlete, "What shoes should I buy?")

    assert vague.kind == "goal_clarification"
    assert vague.answers["_goal_clarification_field"] == "main_goal"
    assert unrelated.kind == "goal_off_topic"
    assert unrelated.answers["goal_draft"] == vague.answers["goal_draft"]
    assert unrelated.answers["goal_messages"] == ["I want to train to run."]
    assert "Buy running shoes" not in str(unrelated.answers)
    async with factory() as session:
        assert (
            await ProfileRepository(session).get_training_goal(
                user_id=unrelated.user_id
            )
            is None
        )


@pytest.mark.asyncio
async def test_multiple_turns_merge_and_explicitly_unknown_date_is_complete(
    goal_database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = goal_database
    extractor = QueueGoalExtractor(
        [
            extracted(
                main_goal="Complete a marathon",
                target_outcome=None,
                missing_fields=["target_outcome", "event_date"],
                message_status="NEEDS_CLARIFICATION",
            ),
            extracted(
                main_goal=None,
                target_outcome="Finish safely",
                missing_fields=["event_date"],
                message_status="NEEDS_CLARIFICATION",
            ),
        ]
    )
    onboarding = service(factory, extractor)
    athlete = identity(6203)
    await start_goal(onboarding, athlete)

    first = await onboarding.handle_text(athlete, "I want to prepare for a marathon.")
    second = await onboarding.handle_text(athlete, "I want to finish it safely.")
    final = await onboarding.choose_goal_clarification(athlete, "NOT_YET")

    assert first.kind == "goal_clarification"
    assert second.kind == "goal_clarification"
    assert final.kind == "goal_confirmation"
    draft = final.answers["goal_draft"]
    assert draft["main_goal"] == "Complete a marathon"
    assert draft["event_date"] is None
    assert draft["target_outcome"] == "Finish safely"
    assert draft["primary_template"]["code"] == "MARATHON"
    assert extractor.calls[1][0] == "UPDATE_EXISTING_GOAL"
    assert extractor.calls[1][2] is not None
    assert extractor.calls[1][2].main_goal == "Complete a marathon"


@pytest.mark.asyncio
async def test_not_yet_button_completes_missing_date_without_an_llm_call(
    goal_database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = goal_database
    extractor = QueueGoalExtractor(
        [
            extracted(
                main_goal="Complete a first marathon",
                target_outcome="Finish safely",
                missing_fields=["event_date"],
                message_status="NEEDS_CLARIFICATION",
            )
        ]
    )
    onboarding = service(factory, extractor)
    athlete = identity(6204)
    await start_goal(onboarding, athlete)
    clarification = await onboarding.handle_text(
        athlete,
        "I want to complete my first marathon and finish safely.",
    )

    confirmation = await onboarding.choose_goal_clarification(athlete, "NOT_YET")

    assert clarification.answers["_goal_clarification_field"] == "event_date"
    assert confirmation.kind == "goal_confirmation"
    assert confirmation.answers["goal_draft"]["event_date"] is None
    assert len(extractor.calls) == 1


@pytest.mark.asyncio
async def test_secondary_priority_is_optional_and_confirmation_text_updates_draft(
    goal_database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = goal_database
    extractor = QueueGoalExtractor(
        [
            extracted(
                main_goal="Run 10 kilometres without stopping",
                target_outcome="Complete the distance without stopping",
            ),
            extracted(
                main_goal=None,
                target_outcome=None,
                secondary_priority="Continue practising calisthenics",
            ),
        ]
    )
    onboarding = service(factory, extractor)
    athlete = identity(6205)
    await start_goal(onboarding, athlete)
    first = await onboarding.handle_text(
        athlete,
        "I want to run 10 kilometres without stopping.",
    )

    updated = await onboarding.handle_text(
        athlete,
        "I also want to continue practising calisthenics.",
    )

    assert first.kind == "goal_confirmation"
    assert first.answers["goal_draft"]["secondary_priority"] is None
    assert updated.kind == "goal_confirmation"
    assert updated.answers["goal_draft"]["main_goal"] == (
        "Run 10 kilometres without stopping"
    )
    assert updated.answers["goal_draft"]["secondary_priority"] == (
        "Continue practising calisthenics"
    )


@pytest.mark.asyncio
async def test_iso_date_clarification_does_not_invoke_the_llm(
    goal_database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = goal_database
    extractor = QueueGoalExtractor(
        [
            extracted(
                main_goal="Complete my first Ironman 70.3",
                target_outcome="Finish safely",
                missing_fields=["event_date"],
                message_status="NEEDS_CLARIFICATION",
            ),
        ]
    )
    onboarding = service(factory, extractor)
    athlete = identity(6208)
    await start_goal(onboarding, athlete)
    await onboarding.handle_text(
        athlete,
        "I want to complete my first Ironman 70.3 and finish safely.",
    )
    invalid = await onboarding.handle_text(athlete, "11 July 2027")

    updated = await onboarding.handle_text(
        athlete,
        "2027-07-11",
    )

    assert invalid.kind == "goal_clarification"
    assert invalid.error_code == "invalid_event_date"
    assert updated.kind == "goal_confirmation"
    draft = updated.answers["goal_draft"]
    assert draft["main_goal"] == "Complete my first Ironman 70.3"
    assert draft["event_date"] == "2027-07-11"
    assert draft["target_outcome"] == "Finish safely"
    assert draft["primary_template"]["code"] == "TRIATHLON_HALF_DISTANCE"
    assert len(extractor.calls) == 1


@pytest.mark.asyncio
async def test_explicit_main_goal_correction_overrides_only_that_field(
    goal_database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = goal_database
    extractor = QueueGoalExtractor(
        [
            extracted(
                main_goal="Complete a marathon",
                event_date="2027-09-19",
                target_outcome="Finish safely",
                secondary_priority="Maintain strength",
            ),
            extracted(
                main_goal="Complete a half marathon",
                target_outcome=None,
            ),
        ]
    )
    onboarding = service(factory, extractor)
    athlete = identity(6209)
    await start_goal(onboarding, athlete)
    await onboarding.handle_text(
        athlete,
        "I want to complete a marathon on 19 September 2027, finish safely, "
        "and maintain strength.",
    )
    corrected = await onboarding.handle_text(
        athlete,
        "Correction: my main goal is to complete a half marathon.",
    )

    assert corrected.kind == "goal_confirmation"
    draft = corrected.answers["goal_draft"]
    assert draft["main_goal"] == "Complete a half marathon"
    assert draft["event_date"] == "2027-09-19"
    assert draft["target_outcome"] == "Finish safely"
    assert draft["secondary_priority"] == "Maintain strength"
    assert draft["supporting_template"]["code"] == "STRENGTH_MAINTENANCE"


@pytest.mark.asyncio
async def test_confirmation_persists_goal_then_requires_context_before_completion(
    goal_database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = goal_database
    extractor = QueueGoalExtractor(
        [
            extracted(
                main_goal="Improve 5 km performance",
                event_date="2026-10-04",
                target_outcome="Run under 24 minutes",
            )
        ]
    )
    onboarding = service(factory, extractor)
    athlete = identity(6206)
    await start_goal(onboarding, athlete)
    raw = "I want to run a sub-24-minute 5 km on October 4, 2026."
    draft = await onboarding.handle_text(athlete, raw)

    confirmed = await onboarding.confirm_goal(athlete)
    equipment = await onboarding.handle_text(
        athlete,
        "Tuesday and Thursday after work, plus a longer Saturday session.",
    )
    limitations = await onboarding.choose_equipment(athlete, "done")
    history = await onboarding.choose_health_limitations(athlete, "none")
    completed = await onboarding.skip_training_history(athlete)

    assert draft.kind == "goal_confirmation"
    assert confirmed.kind == "availability_intake"
    assert confirmed.current_step is OnboardingStep.AVAILABILITY_INTAKE
    assert equipment.kind == "equipment_intake"
    assert equipment.current_step is OnboardingStep.EQUIPMENT_INTAKE
    assert limitations.kind == "health_limitations_intake"
    assert history.kind == "training_history_import"
    assert completed.kind == "onboarding_completed"
    assert completed.current_step is OnboardingStep.TRAINING_HISTORY_IMPORT
    assert completed.onboarding_status is OnboardingStatus.COMPLETED
    assert completed.user_status is UserStatus.ONBOARDING_COMPLETED
    assert "goal_draft" not in completed.answers
    assert completed.answers["raw_goal_text"] == raw
    async with factory() as session:
        goal = await ProfileRepository(session).get_training_goal(
            user_id=completed.user_id
        )
        assert goal is not None
        assert goal.main_goal == "Improve 5 km performance"
        assert goal.event_date is not None
        assert goal.event_date.isoformat() == "2026-10-04"
        assert goal.target_outcome == "Run under 24 minutes"
        assert goal.secondary_priority is None
        assert goal.original_description == raw
        assert goal.status is TrainingGoalStatus.CONFIRMED
        original_updated_at = goal.updated_at
        profile_context = await ProfileRepository(session).get_athlete_profile_context(
            user_id=completed.user_id,
        )
        assert profile_context is not None
        assert profile_context.availability_text == (
            "Tuesday and Thursday after work, plus a longer Saturday session."
        )
        assert profile_context.health_limitations_text == "NONE_REPORTED"

    with pytest.raises(OnboardingApplicationError) as exc_info:
        await onboarding.update_onboarding_data(
            user_id=completed.user_id,
            payload={"main_goal": "Finish an Ironman 70.3"},
        )
    assert exc_info.value.code == "invalid_onboarding_update"
    updated = await onboarding.update_onboarding_data(
        user_id=completed.user_id,
        payload={"target_outcome": "Finish in a decent time"},
    )

    assert updated.updated_fields == {"target_outcome": "Finish in a decent time"}
    async with factory() as session:
        goal = await ProfileRepository(session).get_training_goal(
            user_id=completed.user_id
        )
        assert goal is not None
        assert goal.main_goal == "Improve 5 km performance"
        assert goal.target_outcome == "Finish in a decent time"
        assert goal.original_description == raw
        assert goal.event_date is not None
        assert goal.event_date.isoformat() == "2026-10-04"
        assert goal.updated_at > original_updated_at
        profile_context = await ProfileRepository(session).get_athlete_profile_context(
            user_id=completed.user_id,
        )
        assert profile_context is not None
        onboarding_state = await OnboardingRepository(session).require_for_user(
            user_id=completed.user_id,
        )
        assert onboarding_state.status is OnboardingStatus.COMPLETED


@pytest.mark.asyncio
async def test_historical_unclassified_goal_is_classified_before_equipment_review(
    goal_database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = goal_database
    extractor = QueueGoalExtractor(
        [
            GoalExtractionWorkflowResult(
                outcome="extracted",
                goal_patch=GoalExtractionPatch.model_validate(
                    {
                        "main_goal": None,
                        "event_date": None,
                        "target_outcome": None,
                        "secondary_priority": None,
                        "primary_template": {
                            "decision": "USE_EXISTING",
                            "code": "TRIATHLON_HALF_DISTANCE",
                            "display_name": None,
                            "description": None,
                        },
                        "supporting_template": {
                            "decision": "NONE",
                            "code": None,
                            "display_name": None,
                            "description": None,
                        },
                        "missing_fields": [],
                        "ambiguous_fields": [],
                        "message_status": "COMPLETE",
                    }
                ),
            )
        ]
    )
    onboarding = service(factory, extractor)
    athlete = identity(6221)
    await start_goal(onboarding, athlete)
    original = "I want to finish a local middle-distance triathlon."
    async with factory.begin() as session:
        user = await UserRepository(session).get_by_telegram_id(
            athlete.telegram_user_id
        )
        assert user is not None
        await ProfileRepository(session).upsert_conversational_training_goal(
            user_id=user.id,
            main_goal="Finish a middle-distance triathlon",
            event_date=None,
            target_outcome="Finish comfortably",
            secondary_priority=None,
            original_description=original,
        )
        await OnboardingRepository(session).save_progress(
            user_id=user.id,
            current_step=OnboardingStep.AVAILABILITY_INTAKE,
            answers={
                "raw_goal_text": original,
                "goal_messages": [original],
            },
        )

    confirmation = await onboarding.handle_text(athlete, "Weekend mornings.")

    assert confirmation.kind == "goal_confirmation"
    assert confirmation.answers["goal_messages"] == [original]
    assert extractor.calls[0][0] == "UPDATE_EXISTING_GOAL"
    assert extractor.calls[0][1].startswith("Classify my existing main goal")
    assert extractor.calls[0][2] is not None
    assert extractor.calls[0][2].main_goal == "Finish a middle-distance triathlon"

    review = await onboarding.confirm_goal(athlete)

    assert review.kind == "equipment_intake"
    assert review.capability_review is not None
    assert review.current_step is OnboardingStep.EQUIPMENT_INTAKE
    async with factory() as session:
        user = await UserRepository(session).get_by_telegram_id(
            athlete.telegram_user_id
        )
        assert user is not None
        goal = await ProfileRepository(session).get_training_goal(user_id=user.id)
        profile = await ProfileRepository(session).get_athlete_profile(user_id=user.id)
        assert goal is not None
        assert goal.goal_template_id is not None
        assert goal.original_description == original
        assert profile is not None
        assert profile.availability_text == "Weekend mornings."


@pytest.mark.asyncio
async def test_completed_athlete_requires_explicit_profile_settings_flow(
    goal_database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = goal_database
    extractor = QueueGoalExtractor(
        [
            extracted(
                main_goal="Complete a marathon",
                target_outcome="Finish safely",
            )
        ],
        modification_updates=[
            {
                "main_goal": "Finish an Ironman 70.3",
                "target_outcome": "Finish in a decent time",
                "age": 35,
                "weight_kg": 75.25,
            }
        ],
    )
    onboarding = service(factory, extractor)
    athlete = identity(6210)
    await start_goal(onboarding, athlete)
    await onboarding.handle_text(
        athlete,
        "I want to complete a marathon and finish safely.",
    )
    await onboarding.confirm_goal(athlete)
    await onboarding.handle_text(athlete, "Tuesday, Thursday, and Sunday mornings.")
    await onboarding.choose_equipment(athlete, "done")
    await onboarding.choose_health_limitations(athlete, "none")
    await onboarding.skip_training_history(athlete)

    request = "change my goal to finish my ironman 70.3 in a decent time"
    with pytest.raises(OnboardingApplicationError, match="profile_settings_required"):
        await onboarding.handle_text(athlete, request)


@pytest.mark.asyncio
async def test_other_equipment_and_described_limitations_retain_literal_text(
    goal_database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = goal_database
    extractor = QueueGoalExtractor(
        [
            extracted(
                main_goal="Finish a trail half marathon",
                target_outcome="Finish comfortably on hilly terrain",
            )
        ]
    )
    onboarding = service(factory, extractor)
    athlete = identity(6211)
    await start_goal(onboarding, athlete)
    await onboarding.handle_text(
        athlete,
        "I want to finish a hilly trail half marathon comfortably.",
    )

    confirmed = await onboarding.confirm_goal(athlete)
    availability_text = "  Tuesday evenings; Saturday 2 hours; Sunday recovery.  "
    equipment = await onboarding.handle_text(athlete, availability_text)
    health = await onboarding.choose_equipment(athlete, "done")
    health_text = "  Recovering from a previous ankle sprain; avoid steep descents.  "
    history = await onboarding.handle_text(athlete, health_text)
    completed = await onboarding.skip_training_history(athlete)

    assert confirmed.kind == "availability_intake"
    assert equipment.kind == "equipment_intake"
    assert health.kind == "health_limitations_intake"
    assert history.kind == "training_history_import"
    assert completed.kind == "onboarding_completed"

    async with factory() as session:
        profile_context = await ProfileRepository(session).get_athlete_profile_context(
            user_id=completed.user_id,
        )
        assert profile_context is not None
        assert profile_context.availability_text == availability_text
        assert profile_context.health_limitations_text == health_text


@pytest.mark.asyncio
async def test_equipment_review_is_deterministic_and_resumes_without_provider(
    goal_database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = goal_database
    extractor = QueueGoalExtractor(
        [
            extracted(
                main_goal="Complete a marathon",
                target_outcome="Finish safely",
            )
        ]
    )
    onboarding = service(factory, extractor)
    athlete = identity(6212)
    await start_goal(onboarding, athlete)
    await onboarding.handle_text(
        athlete,
        "I want to complete a marathon and finish safely.",
    )
    await onboarding.confirm_goal(athlete)

    availability_text = "Monday and Wednesday for 45 minutes, Sunday long run."
    failed = await onboarding.handle_text(athlete, availability_text)
    resumed = await onboarding.start(athlete)
    assert failed.kind == "equipment_intake"
    assert failed.error_code is None
    assert failed.current_step is OnboardingStep.EQUIPMENT_INTAKE
    assert resumed.kind == "equipment_intake"
    assert resumed.current_step is OnboardingStep.EQUIPMENT_INTAKE
    assert resumed.capability_review is not None

    async with factory() as session:
        profile_context = await ProfileRepository(session).get_athlete_profile_context(
            user_id=resumed.user_id,
        )
        assert profile_context is not None
        assert profile_context.availability_text == availability_text


@pytest.mark.asyncio
async def test_context_accepts_literal_nonempty_text_without_detail_requirements(
    goal_database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = goal_database
    extractor = QueueGoalExtractor(
        [
            extracted(
                main_goal="Build cycling endurance",
                target_outcome="Ride for two hours comfortably",
            )
        ]
    )
    vague_text = (
        "I usually have full availability for around one hour on weekdays and "
        "more time at weekends."
    )
    onboarding = service(factory, extractor)
    athlete = identity(6214)
    await start_goal(onboarding, athlete)
    await onboarding.handle_text(
        athlete,
        "I want to build cycling endurance and ride for two hours comfortably.",
    )
    confirmed = await onboarding.confirm_goal(athlete)

    accepted = await onboarding.handle_text(athlete, vague_text)

    assert confirmed.kind == "availability_intake"
    assert accepted.kind == "equipment_intake"
    async with factory() as session:
        profile_context = await ProfileRepository(session).get_athlete_profile_context(
            user_id=accepted.user_id,
        )
        assert profile_context is not None
        assert profile_context.availability_text == vague_text


@pytest.mark.asyncio
async def test_stale_context_callbacks_cannot_skip_the_required_free_text_step(
    goal_database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = goal_database
    extractor = QueueGoalExtractor(
        [
            extracted(
                main_goal="Complete a marathon",
                target_outcome="Finish safely",
            )
        ]
    )
    onboarding = service(factory, extractor)
    athlete = identity(6215)
    await start_goal(onboarding, athlete)
    await onboarding.handle_text(
        athlete,
        "I want to complete a marathon and finish safely.",
    )
    confirmed = await onboarding.confirm_goal(athlete)

    with pytest.raises(OnboardingApplicationError, match="stale_action"):
        await onboarding.choose_equipment(athlete, "done")
    with pytest.raises(OnboardingApplicationError, match="stale_action"):
        await onboarding.choose_health_limitations(athlete, "none")

    assert confirmed.current_step is OnboardingStep.AVAILABILITY_INTAKE


@pytest.mark.asyncio
async def test_completed_chat_edit_requires_explicit_profile_settings_flow(
    goal_database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = goal_database
    extractor = QueueGoalExtractor(
        [
            extracted(
                main_goal="Build a consistent running habit",
                target_outcome="Run three times a week",
            )
        ],
    )
    onboarding = service(factory, extractor)
    athlete = identity(6213)
    await start_goal(onboarding, athlete)
    await onboarding.handle_text(
        athlete,
        "I want to build a consistent running habit with three runs each week.",
    )
    await onboarding.confirm_goal(athlete)
    await onboarding.handle_text(athlete, "Tuesday, Thursday, and Saturday.")
    await onboarding.choose_equipment(athlete, "done")
    await onboarding.choose_health_limitations(athlete, "none")
    await onboarding.skip_training_history(athlete)

    with pytest.raises(OnboardingApplicationError, match="profile_settings_required"):
        await onboarding.handle_text(
            athlete,
            "Update my availability and training limitations.",
        )


def _unresolved_primary_result(
    *, code: str = "CUSTOM_ENDURANCE_GOAL"
) -> GoalExtractionWorkflowResult:
    """A classification whose code is not in the canonical catalog.

    There is no catalog expansion any more: whatever decision the extractor
    attaches, a code that does not resolve to an existing, active template is
    a not-found error rather than a candidate for creation.
    """

    return GoalExtractionWorkflowResult(
        outcome="extracted",
        goal_patch=GoalExtractionPatch.model_validate(
            {
                "main_goal": "Prepare for an indoor rowing event",
                "event_date": None,
                "target_outcome": "Finish comfortably",
                "secondary_priority": None,
                "primary_template": {
                    "decision": "CREATE",
                    "code": code,
                    "display_name": "Custom endurance goal",
                    "description": ("General preparation for a custom endurance goal."),
                },
                "supporting_template": {
                    "decision": "NONE",
                    "code": None,
                    "display_name": None,
                    "description": None,
                },
                "missing_fields": [],
                "ambiguous_fields": [],
                "message_status": "COMPLETE",
            }
        ),
    )


@pytest.mark.asyncio
async def test_confirm_goal_rejects_a_classification_not_in_the_catalog(
    goal_database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    """Without expansion, an unresolved template code is a hard error."""

    _, factory = goal_database
    extractor = QueueGoalExtractor([_unresolved_primary_result()])
    onboarding = service(factory, extractor)
    athlete = identity(6270)
    await start_goal(onboarding, athlete)
    drafted = await onboarding.handle_text(
        athlete,
        "I want to prepare for an indoor rowing event and finish comfortably.",
    )

    with pytest.raises(OnboardingApplicationError) as exc_info:
        await onboarding.confirm_goal(athlete)

    assert exc_info.value.code == "goal_template_not_found"
    async with factory() as session:
        assert (
            await ProfileRepository(session).get_training_goal(user_id=drafted.user_id)
            is None
        )


@pytest.mark.asyncio
async def test_profile_goal_classification_confirm_rejects_an_unresolved_template(
    goal_database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    """The post-onboarding goal-edit flow has no expansion fallback either."""

    _, factory = goal_database
    extractor = QueueGoalExtractor(
        [
            extracted(
                main_goal="Complete a marathon",
                target_outcome="Finish safely",
            ),
            _unresolved_primary_result(),
        ],
    )
    onboarding = service(factory, extractor)
    athlete = identity(6271)
    await start_goal(onboarding, athlete)
    await onboarding.handle_text(
        athlete,
        "I want to complete a marathon and finish safely.",
    )
    await onboarding.confirm_goal(athlete)
    await onboarding.handle_text(athlete, "Tuesday, Thursday, and Saturday.")
    await onboarding.choose_equipment(athlete, "done")
    await onboarding.choose_health_limitations(athlete, "none")
    await onboarding.skip_training_history(athlete)

    await onboarding.open_profile_settings(athlete)
    await onboarding.choose_profile_settings(athlete, "section:goal")
    await onboarding.choose_profile_settings(athlete, "goal:main")
    classification = await onboarding.submit_profile_settings_text(
        athlete, "Prepare for an indoor rowing event"
    )

    assert classification is not None
    assert classification.step is ProfileSettingsStep.GOAL_CLASSIFICATION_CONFIRM

    with pytest.raises(OnboardingApplicationError) as exc_info:
        await onboarding.choose_profile_settings(athlete, "goal:classification:confirm")

    assert exc_info.value.code == "goal_template_not_found"
    async with factory() as session:
        user = await UserRepository(session).get_by_telegram_id(
            athlete.telegram_user_id
        )
        assert user is not None
        goal = await ProfileRepository(session).get_training_goal(user_id=user.id)
        assert goal is not None
        assert goal.main_goal == "Complete a marathon"


@pytest.mark.asyncio
async def test_existing_complete_goal_reuses_everything_without_expansion(
    goal_database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = goal_database
    extractor = QueueGoalExtractor(
        [
            extracted(
                main_goal="Improve my marathon finish",
                target_outcome="Finish under four hours",
            )
        ]
    )
    onboarding = service(factory, extractor)
    athlete = identity(6240)
    await start_goal(onboarding, athlete)
    async with factory() as session:
        before_templates = await session.scalar(select(func.count(GoalTemplate.id)))
        before_contexts = await session.scalar(select(func.count(TrainingContext.id)))
        before_capabilities = await session.scalar(select(func.count(Capability.id)))

    await onboarding.handle_text(
        athlete,
        "I want to improve my marathon finish and run under four hours.",
    )
    confirmed = await onboarding.confirm_goal(athlete)
    equipment = await onboarding.handle_text(athlete, "Weekday mornings.")
    limitations = await onboarding.choose_equipment(athlete, "done")
    await onboarding.choose_health_limitations(athlete, "none")
    completed = await onboarding.skip_training_history(athlete)

    assert confirmed.kind == "availability_intake"
    assert equipment.capability_review is not None
    assert limitations.kind == "health_limitations_intake"
    assert completed.kind == "onboarding_completed"
    async with factory() as session:
        goal = await ProfileRepository(session).get_training_goal(
            user_id=confirmed.user_id
        )
        marathon = await session.scalar(
            select(GoalTemplate).where(GoalTemplate.code == "MARATHON")
        )
        assert goal is not None
        assert marathon is not None
        assert goal.goal_template_id == marathon.id
        assert (
            await session.scalar(select(func.count(GoalTemplate.id)))
            == before_templates
        )
        assert (
            await session.scalar(select(func.count(TrainingContext.id)))
            == before_contexts
        )
        assert (
            await session.scalar(select(func.count(Capability.id)))
            == before_capabilities
        )
