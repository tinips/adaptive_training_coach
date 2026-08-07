"""Focused persistence tests for the conversational onboarding goal step."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import date
from uuid import UUID

import pytest
import pytest_asyncio
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
from app.db.models import AvailabilityRule, EquipmentAccess, HealthConstraint
from app.domain.enums import (
    OnboardingStatus,
    OnboardingStep,
    TrainingGoalStatus,
    UserStatus,
)
from app.integrations.llm.models import (
    GoalExtractionAction,
    GoalExtractionOutput,
    GoalExtractionPatch,
)
from app.repositories.onboarding import OnboardingRepository
from app.repositories.profiles import ProfileRepository
from app.schemas.common import TelegramIdentity
from app.schemas.onboarding_context import (
    EquipmentRecommendationGoalContext,
    EquipmentRecommendationWorkflowResult,
    FreeTextValidationWorkflowResult,
)
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
        current_date: str,
    ) -> GoalExtractionWorkflowResult:
        del user_id
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


@dataclass
class QueueContextWorkflow:
    """Deterministic LangGraph boundary substitute for service-flow tests."""

    validations: list[FreeTextValidationWorkflowResult] = field(default_factory=list)
    recommendations: list[EquipmentRecommendationWorkflowResult] = field(
        default_factory=list,
    )
    validation_calls: list[tuple[OnboardingStep, str]] = field(default_factory=list)
    recommendation_calls: list[EquipmentRecommendationGoalContext] = field(
        default_factory=list,
    )

    async def validate_free_text(
        self,
        *,
        step: OnboardingStep,
        user_text: str,
        goal_context: EquipmentRecommendationGoalContext | None = None,
    ) -> FreeTextValidationWorkflowResult:
        del goal_context
        self.validation_calls.append((step, user_text))
        if self.validations:
            return self.validations.pop(0)
        return FreeTextValidationWorkflowResult(outcome="accepted")

    async def validate_text(
        self,
        *,
        step: OnboardingStep,
        user_text: str,
        goal_context: EquipmentRecommendationGoalContext | None = None,
    ) -> FreeTextValidationWorkflowResult:
        return await self.validate_free_text(
            step=step,
            user_text=user_text,
            goal_context=goal_context,
        )

    async def recommend_equipment(
        self,
        *,
        main_goal: str | None,
        target_outcome: str | None,
        event_date: date | None,
        secondary_priority: str | None,
    ) -> EquipmentRecommendationWorkflowResult:
        self.recommendation_calls.append(
            EquipmentRecommendationGoalContext(
                main_goal=main_goal,
                target_outcome=target_outcome,
                event_date=event_date,
                secondary_priority=secondary_priority,
            )
        )
        if self.recommendations:
            return self.recommendations.pop(0)
        return EquipmentRecommendationWorkflowResult(
            outcome="recommended",
            recommendation="Running shoes, suitable clothing, and a watch or timer.",
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
    return GoalExtractionWorkflowResult(
        outcome="extracted",
        goal_patch=GoalExtractionPatch.model_validate(
            {
                "main_goal": main_goal,
                "event_date": event_date,
                "target_outcome": target_outcome,
                "secondary_priority": secondary_priority,
                "missing_fields": missing_fields or [],
                "ambiguous_fields": ambiguous_fields or [],
                "message_status": message_status,
            }
        ),
    )


def service(
    factory: async_sessionmaker[AsyncSession],
    extractor: QueueGoalExtractor,
    context_workflow: QueueContextWorkflow | None = None,
) -> OnboardingService:
    return OnboardingService(
        session_factory=factory,
        goal_extractor=extractor,
        settings=settings(),
        context_workflow=context_workflow or QueueContextWorkflow(),
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
    assert result.answers["goal_draft"] == {
        "main_goal": "Complete a first Ironman 70.3",
        "event_date": "2027-07-18",
        "target_outcome": "Finish safely",
        "secondary_priority": "Maintain muscle mass",
        "missing_fields": [],
        "ambiguous_fields": [],
        "message_status": "COMPLETE",
    }
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
            extracted(
                main_goal=None,
                target_outcome=None,
                message_status="COMPLETE",
            ),
        ]
    )
    onboarding = service(factory, extractor)
    athlete = identity(6203)
    await start_goal(onboarding, athlete)

    first = await onboarding.handle_text(athlete, "I want to prepare for a marathon.")
    second = await onboarding.handle_text(athlete, "I want to finish it safely.")
    final = await onboarding.handle_text(athlete, "I do not have a race date yet.")

    assert first.kind == "goal_clarification"
    assert second.kind == "goal_clarification"
    assert final.kind == "goal_confirmation"
    assert final.answers["goal_draft"] == {
        "main_goal": "Complete a marathon",
        "event_date": None,
        "target_outcome": "Finish safely",
        "secondary_priority": None,
        "missing_fields": [],
        "ambiguous_fields": [],
        "message_status": "COMPLETE",
    }
    assert extractor.calls[1][0] == "UPDATE_EXISTING_GOAL"
    assert extractor.calls[1][2] is not None
    assert extractor.calls[1][2].main_goal == "Complete a marathon"
    assert extractor.calls[2][0] == "UPDATE_EXISTING_GOAL"
    assert extractor.calls[2][2] is not None
    assert extractor.calls[2][2].target_outcome == "Finish safely"


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
async def test_secondary_priority_is_optional_and_addition_updates_existing_draft(
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

    addition = await onboarding.add_to_goal(athlete)
    updated = await onboarding.handle_text(
        athlete,
        "I also want to continue practising calisthenics.",
    )

    assert first.kind == "goal_confirmation"
    assert first.answers["goal_draft"]["secondary_priority"] is None
    assert addition.kind == "goal_addition"
    assert updated.kind == "goal_confirmation"
    assert updated.answers["goal_draft"]["main_goal"] == (
        "Run 10 kilometres without stopping"
    )
    assert updated.answers["goal_draft"]["secondary_priority"] == (
        "Continue practising calisthenics"
    )


@pytest.mark.asyncio
async def test_date_and_secondary_priority_patch_preserves_existing_goal_fields(
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
            extracted(
                main_goal=None,
                event_date="2027-07-11",
                target_outcome=None,
                secondary_priority="Maintain muscle",
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

    updated = await onboarding.handle_text(
        athlete,
        "The event is on 11 July 2027 and I also want to maintain muscle.",
    )

    assert updated.kind == "goal_confirmation"
    assert updated.answers["goal_draft"] == {
        "main_goal": "Complete my first Ironman 70.3",
        "event_date": "2027-07-11",
        "target_outcome": "Finish safely",
        "secondary_priority": "Maintain muscle",
        "missing_fields": [],
        "ambiguous_fields": [],
        "message_status": "COMPLETE",
    }
    action, latest_message, current_draft = extractor.calls[1]
    assert action == "UPDATE_EXISTING_GOAL"
    assert latest_message.startswith("The event is on 11 July 2027")
    assert current_draft is not None
    assert current_draft.main_goal == "Complete my first Ironman 70.3"
    assert current_draft.target_outcome == "Finish safely"


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
    await onboarding.add_to_goal(athlete)

    corrected = await onboarding.handle_text(
        athlete,
        "Correction: my main goal is to complete a half marathon.",
    )

    assert corrected.kind == "goal_confirmation"
    assert corrected.answers["goal_draft"] == {
        "main_goal": "Complete a half marathon",
        "event_date": "2027-09-19",
        "target_outcome": "Finish safely",
        "secondary_priority": "Maintain strength",
        "missing_fields": [],
        "ambiguous_fields": [],
        "message_status": "COMPLETE",
    }


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
    context = QueueContextWorkflow(
        recommendations=[
            EquipmentRecommendationWorkflowResult(
                outcome="recommended",
                recommendation=(
                    "Road-running shoes, weather-appropriate clothing, and a timer."
                ),
            ),
            EquipmentRecommendationWorkflowResult(
                outcome="recommended",
                recommendation="Triathlon-specific essentials for the revised goal.",
            ),
        ]
    )
    onboarding = service(factory, extractor, context)
    athlete = identity(6206)
    await start_goal(onboarding, athlete)
    raw = "I want to run a sub-24-minute 5 km on October 4, 2026."
    draft = await onboarding.handle_text(athlete, raw)

    confirmed = await onboarding.confirm_goal(athlete)
    equipment = await onboarding.handle_text(
        athlete,
        "Tuesday and Thursday after work, plus a longer Saturday session.",
    )
    limitations = await onboarding.choose_equipment(athlete, "all")
    completed = await onboarding.choose_health_limitations(athlete, "none")

    assert draft.kind == "goal_confirmation"
    assert confirmed.kind == "availability_intake"
    assert confirmed.current_step is OnboardingStep.AVAILABILITY_INTAKE
    assert equipment.kind == "equipment_intake"
    assert equipment.current_step is OnboardingStep.EQUIPMENT_INTAKE
    assert limitations.kind == "health_limitations_intake"
    assert completed.kind == "onboarding_completed"
    assert completed.current_step is OnboardingStep.HEALTH_LIMITATIONS_INTAKE
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
        assert profile_context.equipment_recommendation_text == (
            "Road-running shoes, weather-appropriate clothing, and a timer."
        )
        assert profile_context.equipment_text == "ALL_RECOMMENDED"
        assert profile_context.health_limitations_text == "NONE_REPORTED"
        for table in (AvailabilityRule, EquipmentAccess, HealthConstraint):
            count = await session.scalar(
                select(func.count())
                .select_from(table)
                .where(table.user_id == completed.user_id),
            )
            assert count == 0

    updated = await onboarding.update_onboarding_data(
        user_id=completed.user_id,
        payload={
            "main_goal": "Finish an Ironman 70.3",
            "target_outcome": "Finish in a decent time",
        },
    )

    assert updated.updated_fields == {
        "main_goal": "Finish an Ironman 70.3",
        "target_outcome": "Finish in a decent time",
    }
    async with factory() as session:
        goal = await ProfileRepository(session).get_training_goal(
            user_id=completed.user_id
        )
        assert goal is not None
        assert goal.main_goal == "Finish an Ironman 70.3"
        assert goal.target_outcome == "Finish in a decent time"
        assert goal.original_description == raw
        assert goal.event_date is not None
        assert goal.event_date.isoformat() == "2026-10-04"
        assert goal.updated_at > original_updated_at
        profile_context = await ProfileRepository(session).get_athlete_profile_context(
            user_id=completed.user_id,
        )
        assert profile_context is not None
        assert profile_context.equipment_recommendation_text == (
            "Triathlon-specific essentials for the revised goal."
        )
        assert profile_context.equipment_text is None
        onboarding_state = await OnboardingRepository(session).require_for_user(
            user_id=completed.user_id,
        )
        assert onboarding_state.status is OnboardingStatus.ACTIVE
        assert onboarding_state.current_step is OnboardingStep.EQUIPMENT_INTAKE


@pytest.mark.asyncio
async def test_completed_athlete_goal_modification_uses_owned_service_update(
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
    context = QueueContextWorkflow(
        recommendations=[
            EquipmentRecommendationWorkflowResult(
                outcome="recommended",
                recommendation="Shoes and a timer for marathon preparation.",
            ),
            EquipmentRecommendationWorkflowResult(
                outcome="recommended",
                recommendation="Swim, bike, run essentials for Ironman preparation.",
            ),
        ]
    )
    onboarding = service(factory, extractor, context)
    athlete = identity(6210)
    await start_goal(onboarding, athlete)
    await onboarding.handle_text(
        athlete,
        "I want to complete a marathon and finish safely.",
    )
    await onboarding.confirm_goal(athlete)
    await onboarding.handle_text(athlete, "Tuesday, Thursday, and Sunday mornings.")
    await onboarding.choose_equipment(athlete, "all")
    completed = await onboarding.choose_health_limitations(athlete, "none")

    request = "change my goal to finish my ironman 70.3 in a decent time"
    result = await onboarding.handle_text(athlete, request)

    assert result.kind == "equipment_intake"
    assert result.current_step is OnboardingStep.EQUIPMENT_INTAKE
    assert result.onboarding_status is OnboardingStatus.ACTIVE
    assert result.user_status is UserStatus.ONBOARDING_IN_PROGRESS
    assert extractor.modification_calls == [(completed.user_id, request)]
    async with factory() as session:
        goal = await ProfileRepository(session).get_training_goal(
            user_id=completed.user_id
        )
        assert goal is not None
        assert goal.main_goal == "Finish an Ironman 70.3"
        assert goal.target_outcome == "Finish in a decent time"
        assert goal.original_description == (
            "I want to complete a marathon and finish safely."
        )
        profile = await ProfileRepository(session).get_athlete_profile(
            user_id=completed.user_id
        )
        assert profile is not None
        assert profile.age == 35
        assert profile.weight_kg == 75.25
        assert profile.equipment_recommendation_text == (
            "Swim, bike, run essentials for Ironman preparation."
        )
        assert profile.equipment_text is None


@pytest.mark.asyncio
async def test_start_again_clears_only_temporary_goal_state(
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
    athlete = identity(6207)
    await start_goal(onboarding, athlete)
    draft = await onboarding.handle_text(
        athlete,
        "I want to complete a marathon safely.",
    )
    async with factory.begin() as session:
        repository = OnboardingRepository(session)
        persisted = await repository.require_for_user(user_id=draft.user_id)
        answers = dict(persisted.answers)
        answers["unrelated_profile_marker"] = "keep"
        await repository.save_progress(
            user_id=draft.user_id,
            current_step=OnboardingStep.GOAL_INTAKE,
            answers=answers,
        )

    restarted = await onboarding.restart_goal(athlete)

    assert restarted.kind == "goal_intake"
    assert restarted.answers["consent"] is True
    assert restarted.answers["unrelated_profile_marker"] == "keep"
    assert "goal_draft" not in restarted.answers
    assert "raw_goal_text" not in restarted.answers
    assert "goal_messages" not in restarted.answers
    async with factory() as session:
        assert (
            await ProfileRepository(session).get_training_goal(
                user_id=restarted.user_id
            )
            is None
        )


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
    context = QueueContextWorkflow(
        recommendations=[
            EquipmentRecommendationWorkflowResult(
                outcome="recommended",
                recommendation="Trail shoes, hydration, and weather layers.",
            )
        ]
    )
    onboarding = service(factory, extractor, context)
    athlete = identity(6211)
    await start_goal(onboarding, athlete)
    await onboarding.handle_text(
        athlete,
        "I want to finish a hilly trail half marathon comfortably.",
    )

    confirmed = await onboarding.confirm_goal(athlete)
    availability_text = "  Tuesday evenings; Saturday 2 hours; Sunday recovery.  "
    equipment = await onboarding.handle_text(athlete, availability_text)
    validation_count_before_callbacks = len(context.validation_calls)
    recommendation_count_before_callbacks = len(context.recommendation_calls)
    equipment_details = await onboarding.choose_equipment(athlete, "other")
    equipment_text = "  I have trail shoes and a bottle, but no poles.\n"
    health = await onboarding.handle_text(athlete, equipment_text)
    describe = await onboarding.choose_health_limitations(athlete, "describe")
    health_text = "  Recovering from a previous ankle sprain; avoid steep descents.  "
    completed = await onboarding.handle_text(athlete, health_text)

    assert confirmed.kind == "availability_intake"
    assert equipment.kind == "equipment_intake"
    assert equipment_details.kind == "equipment_details_intake"
    assert health.kind == "health_limitations_intake"
    assert describe.kind == "health_limitations_intake"
    assert completed.kind == "onboarding_completed"
    # Deterministic callbacks did not call either LangGraph method.
    assert validation_count_before_callbacks == 1
    assert recommendation_count_before_callbacks == 1
    assert len(context.validation_calls) == 3
    assert len(context.recommendation_calls) == 1

    async with factory() as session:
        profile_context = await ProfileRepository(session).get_athlete_profile_context(
            user_id=completed.user_id,
        )
        assert profile_context is not None
        assert profile_context.availability_text == availability_text
        assert profile_context.equipment_recommendation_text == (
            "Trail shoes, hydration, and weather layers."
        )
        assert profile_context.equipment_text == equipment_text
        assert profile_context.health_limitations_text == health_text


@pytest.mark.asyncio
async def test_recommendation_failure_keeps_availability_and_can_resume_and_retry(
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
    context = QueueContextWorkflow(
        recommendations=[
            EquipmentRecommendationWorkflowResult(
                outcome="provider_error",
                error_code="temporary_provider_failure",
            ),
            EquipmentRecommendationWorkflowResult(
                outcome="recommended",
                recommendation="Shoes, comfortable kit, and hydration for long runs.",
            ),
        ]
    )
    onboarding = service(factory, extractor, context)
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
    retried = await onboarding.handle_text(athlete, "Please retry the suggestion.")

    assert failed.kind == "equipment_recommendation"
    assert failed.error_code == "temporary_provider_failure"
    assert failed.current_step is OnboardingStep.EQUIPMENT_RECOMMENDATION
    assert resumed.kind == "equipment_recommendation"
    assert resumed.current_step is OnboardingStep.EQUIPMENT_RECOMMENDATION
    assert retried.kind == "equipment_intake"
    assert retried.current_step is OnboardingStep.EQUIPMENT_INTAKE
    assert len(context.validation_calls) == 1
    assert len(context.recommendation_calls) == 2

    async with factory() as session:
        profile_context = await ProfileRepository(session).get_athlete_profile_context(
            user_id=retried.user_id,
        )
        assert profile_context is not None
        assert profile_context.availability_text == availability_text
        assert profile_context.equipment_recommendation_text == (
            "Shoes, comfortable kit, and hydration for long runs."
        )


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
    context = QueueContextWorkflow()
    onboarding = service(factory, extractor, context)
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
    context = QueueContextWorkflow()
    onboarding = service(factory, extractor, context)
    athlete = identity(6215)
    await start_goal(onboarding, athlete)
    await onboarding.handle_text(
        athlete,
        "I want to complete a marathon and finish safely.",
    )
    confirmed = await onboarding.confirm_goal(athlete)

    with pytest.raises(OnboardingApplicationError, match="stale_action"):
        await onboarding.choose_equipment(athlete, "all")
    with pytest.raises(OnboardingApplicationError, match="stale_action"):
        await onboarding.choose_health_limitations(athlete, "none")

    assert confirmed.current_step is OnboardingStep.AVAILABILITY_INTAKE
    assert context.validation_calls == []
    assert context.recommendation_calls == []


@pytest.mark.asyncio
async def test_completed_chat_edit_updates_only_raw_context_fields(
    goal_database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = goal_database
    availability_text = "Monday before work and Saturday afternoon."
    equipment_text = "I only have a treadmill and resistance bands."
    health_text = "Avoid impact while my calf settles."
    extractor = QueueGoalExtractor(
        [
            extracted(
                main_goal="Build a consistent running habit",
                target_outcome="Run three times a week",
            )
        ],
        modification_updates=[
            {
                "availability_text": availability_text,
                "equipment_text": equipment_text,
                "health_limitations_text": health_text,
            }
        ],
    )
    context = QueueContextWorkflow()
    onboarding = service(factory, extractor, context)
    athlete = identity(6213)
    await start_goal(onboarding, athlete)
    await onboarding.handle_text(
        athlete,
        "I want to build a consistent running habit with three runs each week.",
    )
    await onboarding.confirm_goal(athlete)
    await onboarding.handle_text(athlete, "Tuesday, Thursday, and Saturday.")
    await onboarding.choose_equipment(athlete, "all")
    completed = await onboarding.choose_health_limitations(athlete, "none")

    result = await onboarding.handle_text(
        athlete,
        "Update my availability, equipment, and training limitations.",
    )

    assert result.kind == "onboarding_modification"
    assert result.onboarding_status is OnboardingStatus.COMPLETED
    assert result.current_step is OnboardingStep.HEALTH_LIMITATIONS_INTAKE
    assert len(context.recommendation_calls) == 1
    async with factory() as session:
        profile_context = await ProfileRepository(session).get_athlete_profile_context(
            user_id=completed.user_id,
        )
        assert profile_context is not None
        assert profile_context.availability_text == availability_text
        assert profile_context.equipment_text == equipment_text
        assert profile_context.health_limitations_text == health_text
