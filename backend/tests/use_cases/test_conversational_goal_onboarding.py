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
from app.db.models import (
    Capability,
    GoalTemplate,
    GoalTemplateContext,
    TrainingContext,
)
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
    GoalTemplateSummary,
)
from app.repositories.onboarding import OnboardingRepository
from app.repositories.profiles import ProfileRepository
from app.repositories.users import UserRepository
from app.schemas.catalog_expansion import (
    CapabilitySummary,
    CatalogExpansionWorkflowResult,
    ContextCapabilityOutput,
    GoalContextMappingOutput,
    GoalContextProposal,
    GoalTemplateDraft,
    TrainingContextSummary,
)
from app.schemas.common import TelegramIdentity
from app.schemas.onboarding_context import FreeTextValidationWorkflowResult
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


@dataclass
class QueueContextWorkflow:
    """Deterministic LangGraph boundary substitute for service-flow tests."""

    validations: list[FreeTextValidationWorkflowResult] = field(default_factory=list)
    validation_calls: list[tuple[OnboardingStep, str]] = field(default_factory=list)

    async def validate_free_text(
        self,
        *,
        step: OnboardingStep,
        user_text: str,
    ) -> FreeTextValidationWorkflowResult:
        self.validation_calls.append((step, user_text))
        if self.validations:
            return self.validations.pop(0)
        return FreeTextValidationWorkflowResult(outcome="accepted")

    async def validate_text(
        self,
        *,
        step: OnboardingStep,
        user_text: str,
    ) -> FreeTextValidationWorkflowResult:
        return await self.validate_free_text(
            step=step,
            user_text=user_text,
        )


@dataclass
class QueueCatalogExpansionWorkflow:
    mappings: list[CatalogExpansionWorkflowResult]
    capabilities: list[CatalogExpansionWorkflowResult]
    map_calls: int = 0
    capability_calls: int = 0

    async def map_goal_contexts(
        self,
        *,
        user_id: UUID,
        templates: tuple[GoalTemplateDraft, ...],
        active_goals: tuple[GoalTemplateSummary, ...],
        active_contexts: tuple[TrainingContextSummary, ...],
    ) -> CatalogExpansionWorkflowResult:
        del user_id, templates, active_goals, active_contexts
        self.map_calls += 1
        return self.mappings.pop(0)

    async def define_context_capabilities(
        self,
        *,
        user_id: UUID,
        goals: tuple[GoalTemplateDraft, ...],
        new_contexts: tuple[GoalContextProposal, ...],
        active_contexts: tuple[TrainingContextSummary, ...],
        active_capabilities: tuple[CapabilitySummary, ...],
    ) -> CatalogExpansionWorkflowResult:
        del user_id, goals, new_contexts, active_contexts, active_capabilities
        self.capability_calls += 1
        return self.capabilities.pop(0)


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
                )
                if phrase in folded
            ),
            None,
        )
        primary_template = (
            {
                "decision": "USE_EXISTING",
                "code": known,
                "display_name": None,
                "description": None,
            }
            if known is not None
            else {
                "decision": "CREATE",
                "code": "CUSTOM_ENDURANCE_GOAL",
                "display_name": "Custom endurance goal",
                "description": "General preparation for a custom endurance goal.",
            }
        )
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
    context_workflow: QueueContextWorkflow | None = None,
    catalog_expansion_workflow: QueueCatalogExpansionWorkflow | None = None,
) -> OnboardingService:
    return OnboardingService(
        session_factory=factory,
        goal_extractor=extractor,
        settings=settings(),
        context_workflow=context_workflow or QueueContextWorkflow(),
        catalog_expansion_workflow=catalog_expansion_workflow,
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
    context = QueueContextWorkflow()
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
async def test_dynamic_expansion_failure_is_atomic_and_retry_publishes_everything(
    goal_database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = goal_database
    mapping = GoalContextMappingOutput.model_validate(
        {
            "templates": [
                {
                    "template_code": "CUSTOM_ENDURANCE_GOAL",
                    "contexts": [
                        {
                            "decision": "CREATE",
                            "code": "rowing_indoor",
                            "display_name": "Indoor rowing",
                            "description": (
                                "General indoor rowing preparation and conditioning."
                            ),
                            "discipline": "OTHER",
                            "role": "TARGET",
                            "priority": 10,
                        }
                    ],
                },
                {
                    "template_code": "POSTURE_MAINTENANCE",
                    "contexts": [
                        {
                            "decision": "CREATE",
                            "code": "rowing_indoor",
                            "display_name": "Indoor rowing",
                            "description": (
                                "General indoor rowing preparation and conditioning."
                            ),
                            "discipline": "OTHER",
                            "role": "SUPPORTING",
                            "priority": 20,
                        }
                    ],
                },
            ]
        }
    )
    capability_definition = {
        "capabilities": [
            {
                "decision": "CREATE",
                "code": "rowing_machine",
                "display_name": "Rowing machine",
                "description": "Equipment used for indoor rowing sessions.",
                "kind": "EQUIPMENT",
            }
        ],
        "contexts": [
            {
                "target_context_code": "rowing_indoor",
                "options": [
                    {
                        "code": "rowing_machine_execution",
                        "display_name": "Indoor rowing machine",
                        "execution_context_code": "rowing_indoor",
                        "role": "PREFERRED",
                        "priority": 10,
                        "limitations": [],
                        "requirements": [
                            {
                                "capability_code": "rowing_machine",
                                "importance": "REQUIRED",
                            }
                        ],
                    }
                ],
            }
        ],
    }
    expansion = QueueCatalogExpansionWorkflow(
        mappings=[
            CatalogExpansionWorkflowResult(
                outcome="succeeded", context_mapping=mapping
            ),
            CatalogExpansionWorkflowResult(
                outcome="succeeded", context_mapping=mapping
            ),
        ],
        capabilities=[
            CatalogExpansionWorkflowResult(
                outcome="provider_error", error_code="provider_failure"
            ),
            CatalogExpansionWorkflowResult.model_validate(
                {
                    "outcome": "succeeded",
                    "capability_definition": capability_definition,
                }
            ),
        ],
    )
    onboarding = service(
        factory,
        QueueGoalExtractor(
            [
                GoalExtractionWorkflowResult(
                    outcome="extracted",
                    goal_patch=GoalExtractionPatch.model_validate(
                        {
                            "main_goal": "Prepare for an indoor rowing event",
                            "event_date": None,
                            "target_outcome": "Finish the event comfortably",
                            "secondary_priority": "Maintain good posture",
                            "primary_template": {
                                "decision": "CREATE",
                                "code": "CUSTOM_ENDURANCE_GOAL",
                                "display_name": "Custom endurance goal",
                                "description": (
                                    "General preparation for a custom endurance goal."
                                ),
                            },
                            "supporting_template": {
                                "decision": "CREATE",
                                "code": "POSTURE_MAINTENANCE",
                                "display_name": "Posture maintenance",
                                "description": (
                                    "Maintain posture during endurance preparation."
                                ),
                            },
                            "missing_fields": [],
                            "ambiguous_fields": [],
                            "message_status": "COMPLETE",
                        }
                    ),
                )
            ]
        ),
        catalog_expansion_workflow=expansion,
    )
    athlete = identity(6220)
    await start_goal(onboarding, athlete)
    await onboarding.handle_text(
        athlete,
        "I want to prepare for an indoor rowing event, finish comfortably, "
        "and maintain good posture.",
    )

    failed = await onboarding.confirm_goal(athlete)

    assert failed.kind == "goal_confirmation"
    assert failed.error_code == "provider_failure"
    assert "_catalog_expansion_in_flight" not in failed.answers
    async with factory() as session:
        assert (
            await session.scalar(
                select(GoalTemplate).where(GoalTemplate.code == "CUSTOM_ENDURANCE_GOAL")
            )
            is None
        )
        assert (
            await session.scalar(
                select(GoalTemplate).where(GoalTemplate.code == "POSTURE_MAINTENANCE")
            )
            is None
        )
        assert (
            await session.scalar(
                select(TrainingContext).where(TrainingContext.code == "rowing_indoor")
            )
            is None
        )
        assert (
            await session.scalar(
                select(Capability).where(Capability.code == "rowing_machine")
            )
            is None
        )
        assert (
            await ProfileRepository(session).get_training_goal(user_id=failed.user_id)
            is None
        )

    retried = await onboarding.confirm_goal(athlete)

    assert retried.kind == "availability_intake"
    assert expansion.map_calls == 2
    assert expansion.capability_calls == 2
    review = await onboarding.handle_text(athlete, "Weekday mornings.")
    assert review.capability_review is not None
    rowing_machine = next(
        item.id
        for item in review.capability_review.options
        if item.code == "rowing_machine"
    )
    await onboarding.choose_equipment(athlete, str(rowing_machine))
    await onboarding.choose_equipment(athlete, "done")
    history = await onboarding.choose_health_limitations(athlete, "none")
    completed = await onboarding.skip_training_history(athlete)
    assert history.kind == "training_history_import"
    assert completed.kind == "onboarding_completed"
    async with factory() as session:
        goal = await ProfileRepository(session).get_training_goal(
            user_id=retried.user_id
        )
        template = await session.scalar(
            select(GoalTemplate).where(GoalTemplate.code == "CUSTOM_ENDURANCE_GOAL")
        )
        supporting_template = await session.scalar(
            select(GoalTemplate).where(GoalTemplate.code == "POSTURE_MAINTENANCE")
        )
        context = await session.scalar(
            select(TrainingContext).where(TrainingContext.code == "rowing_indoor")
        )
        capability = await session.scalar(
            select(Capability).where(Capability.code == "rowing_machine")
        )
        assert goal is not None
        assert template is not None
        assert supporting_template is not None
        assert context is not None
        assert capability is not None
        assert goal.goal_template_id == template.id
        assert goal.supporting_goal_template_id == supporting_template.id


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
    context = QueueContextWorkflow()
    onboarding = service(factory, extractor, context)
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
    context = QueueContextWorkflow()
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
    health = await onboarding.choose_equipment(athlete, "done")
    health_text = "  Recovering from a previous ankle sprain; avoid steep descents.  "
    history = await onboarding.handle_text(athlete, health_text)
    completed = await onboarding.skip_training_history(athlete)

    assert confirmed.kind == "availability_intake"
    assert equipment.kind == "equipment_intake"
    assert health.kind == "health_limitations_intake"
    assert history.kind == "training_history_import"
    assert completed.kind == "onboarding_completed"
    # Deterministic callbacks did not call either LangGraph method.
    assert validation_count_before_callbacks == 1
    assert len(context.validation_calls) == 2

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
    context = QueueContextWorkflow()
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
    assert failed.kind == "equipment_intake"
    assert failed.error_code is None
    assert failed.current_step is OnboardingStep.EQUIPMENT_INTAKE
    assert resumed.kind == "equipment_intake"
    assert resumed.current_step is OnboardingStep.EQUIPMENT_INTAKE
    assert resumed.capability_review is not None
    assert len(context.validation_calls) == 1

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
        await onboarding.choose_equipment(athlete, "done")
    with pytest.raises(OnboardingApplicationError, match="stale_action"):
        await onboarding.choose_health_limitations(athlete, "none")

    assert confirmed.current_step is OnboardingStep.AVAILABILITY_INTAKE
    assert context.validation_calls == []


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
    await onboarding.choose_equipment(athlete, "done")
    await onboarding.choose_health_limitations(athlete, "none")
    await onboarding.skip_training_history(athlete)

    with pytest.raises(OnboardingApplicationError, match="profile_settings_required"):
        await onboarding.handle_text(
            athlete,
            "Update my availability and training limitations.",
        )


def _new_goal_result(
    *,
    template_code: str,
    main_goal: str = "Prepare for a HYROX-style event",
    target_outcome: str = "Finish the event comfortably",
) -> GoalExtractionWorkflowResult:
    return GoalExtractionWorkflowResult(
        outcome="extracted",
        goal_patch=GoalExtractionPatch.model_validate(
            {
                "main_goal": main_goal,
                "event_date": None,
                "target_outcome": target_outcome,
                "secondary_priority": None,
                "primary_template": {
                    "decision": "CREATE",
                    "code": template_code,
                    "display_name": template_code.replace("_", " ").title(),
                    "description": (
                        f"General preparation for {template_code.casefold()}."
                    ),
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


def _existing_goal_result(
    *,
    template_code: str,
    main_goal: str = "Prepare for a HYROX-style event",
    target_outcome: str = "Finish the event comfortably",
) -> GoalExtractionWorkflowResult:
    return GoalExtractionWorkflowResult(
        outcome="extracted",
        goal_patch=GoalExtractionPatch.model_validate(
            {
                "main_goal": main_goal,
                "event_date": None,
                "target_outcome": target_outcome,
                "secondary_priority": None,
                "primary_template": {
                    "decision": "USE_EXISTING",
                    "code": template_code,
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


def _rowing_mapping(
    *,
    template_code: str,
    include_running: bool = False,
    rowing_decision: str = "USE_EXISTING",
) -> CatalogExpansionWorkflowResult:
    contexts: list[dict[str, object]] = []
    if include_running:
        contexts.append(
            {
                "decision": "USE_EXISTING",
                "code": "running_road",
                "display_name": None,
                "description": None,
                "discipline": "RUNNING",
                "role": "SUPPORTING",
                "priority": 20,
            }
        )
    rowing_context: dict[str, object] = {
        "decision": rowing_decision,
        "code": "rowing_general",
        "display_name": None,
        "description": None,
        "discipline": "OTHER",
        "role": "TARGET",
        "priority": 10,
    }
    if rowing_decision == "CREATE":
        rowing_context["display_name"] = "General rowing"
        rowing_context["description"] = "General rowing practice on water or indoors."
    contexts.append(rowing_context)
    return CatalogExpansionWorkflowResult(
        outcome="succeeded",
        context_mapping=GoalContextMappingOutput.model_validate(
            {"templates": [{"template_code": template_code, "contexts": contexts}]}
        ),
    )


def _rowing_capability_result(
    *,
    target_codes: tuple[str, ...] = ("running_road", "rowing_general"),
    option_code: str = "indoor_rowing",
    capability_code: str = "rowing_machine",
) -> CatalogExpansionWorkflowResult:
    capabilities: list[dict[str, object]] = [
        {
            "decision": "CREATE",
            "code": capability_code,
            "display_name": "Rowing machine",
            "description": "An indoor rowing machine.",
            "kind": "EQUIPMENT",
        }
    ]
    if "running_road" in target_codes:
        capabilities.append(
            {
                "decision": "USE_EXISTING",
                "code": "running_shoes",
                "display_name": None,
                "description": None,
                "kind": "EQUIPMENT",
            }
        )
    return CatalogExpansionWorkflowResult(
        outcome="succeeded",
        capability_definition=ContextCapabilityOutput.model_validate(
            {
                "capabilities": capabilities,
                "contexts": [
                    {
                        "target_context_code": target_code,
                        "options": [
                            {
                                "code": (
                                    "outdoor_road"
                                    if target_code == "running_road"
                                    else option_code
                                ),
                                "display_name": (
                                    "Outdoor road running"
                                    if target_code == "running_road"
                                    else "Indoor rowing"
                                ),
                                "execution_context_code": target_code,
                                "role": "PREFERRED",
                                "priority": 10,
                                "limitations": [],
                                "requirements": [
                                    {
                                        "capability_code": (
                                            "running_shoes"
                                            if target_code == "running_road"
                                            else capability_code
                                        ),
                                        "importance": "REQUIRED",
                                    }
                                ],
                            }
                        ],
                    }
                    for target_code in target_codes
                ],
            }
        ),
    )


@pytest.mark.asyncio
async def test_existing_complete_goal_reuses_everything_without_expansion(
    goal_database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = goal_database
    expansion = QueueCatalogExpansionWorkflow(mappings=[], capabilities=[])
    extractor = QueueGoalExtractor(
        [
            extracted(
                main_goal="Improve my marathon finish",
                target_outcome="Finish under four hours",
            )
        ]
    )
    onboarding = service(factory, extractor, catalog_expansion_workflow=expansion)
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

    assert expansion.map_calls == 0
    assert expansion.capability_calls == 0
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


@pytest.mark.asyncio
async def test_new_goal_reuses_existing_context_and_creates_only_missing(
    goal_database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = goal_database
    expansion = QueueCatalogExpansionWorkflow(
        mappings=[
            _rowing_mapping(
                template_code="CUSTOM_HYROX_GOAL",
                include_running=True,
                rowing_decision="CREATE",
            ),
        ],
        capabilities=[_rowing_capability_result()],
    )
    onboarding = service(
        factory,
        QueueGoalExtractor([_new_goal_result(template_code="CUSTOM_HYROX_GOAL")]),
        catalog_expansion_workflow=expansion,
    )
    athlete = identity(6250)
    await start_goal(onboarding, athlete)
    await onboarding.handle_text(athlete, "I want to prepare for a HYROX-style event.")

    confirmed = await onboarding.confirm_goal(athlete)
    equipment = await onboarding.handle_text(athlete, "Weekday mornings.")

    assert expansion.map_calls == 1
    assert expansion.capability_calls == 1
    assert confirmed.kind == "availability_intake"
    assert equipment.capability_review is not None
    option_codes = {item.code for item in equipment.capability_review.options}
    assert {"rowing_machine", "running_shoes"}.issubset(option_codes)
    async with factory() as session:
        goal = await ProfileRepository(session).get_training_goal(
            user_id=confirmed.user_id
        )
        template = await session.scalar(
            select(GoalTemplate).where(GoalTemplate.code == "CUSTOM_HYROX_GOAL")
        )
        rowing = await session.scalar(
            select(TrainingContext).where(TrainingContext.code == "rowing_general")
        )
        running = await session.scalar(
            select(TrainingContext).where(TrainingContext.code == "running_road")
        )
        machine = await session.scalar(
            select(Capability).where(Capability.code == "rowing_machine")
        )
        assert goal is not None
        assert template is not None
        assert goal.goal_template_id == template.id
        assert rowing is not None
        assert running is not None
        assert machine is not None
        # running_road is a canonical context and must be reused, not recreated.
        assert (
            await session.scalar(
                select(func.count(TrainingContext.id)).where(
                    TrainingContext.code == "running_road"
                )
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count(TrainingContext.id)).where(
                    TrainingContext.code == "rowing_general"
                )
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count(Capability.id)).where(
                    Capability.code == "rowing_machine"
                )
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count(GoalTemplateContext.training_context_id)).where(
                    GoalTemplateContext.goal_template_id == template.id
                )
            )
            == 2
        )


@pytest.mark.asyncio
async def test_new_goal_is_idempotent_across_athletes(
    goal_database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = goal_database
    expansion = QueueCatalogExpansionWorkflow(
        mappings=[
            _rowing_mapping(
                template_code="CUSTOM_HYROX_GOAL",
                include_running=True,
                rowing_decision="CREATE",
            ),
        ],
        capabilities=[_rowing_capability_result()],
    )
    first_onboarding = service(
        factory,
        QueueGoalExtractor([_new_goal_result(template_code="CUSTOM_HYROX_GOAL")]),
        catalog_expansion_workflow=expansion,
    )
    first = identity(6251)
    await start_goal(first_onboarding, first)
    await first_onboarding.handle_text(
        first,
        "I want to prepare for a HYROX-style event.",
    )
    first_confirmed = await first_onboarding.confirm_goal(first)
    assert first_confirmed.kind == "availability_intake"
    assert expansion.map_calls == 1
    assert expansion.capability_calls == 1

    second_onboarding = service(
        factory,
        QueueGoalExtractor([_existing_goal_result(template_code="CUSTOM_HYROX_GOAL")]),
        catalog_expansion_workflow=expansion,
    )
    second = identity(6252)
    await start_goal(second_onboarding, second)
    await second_onboarding.handle_text(
        second,
        "I want to prepare for a HYROX-style event.",
    )
    second_confirmed = await second_onboarding.confirm_goal(second)

    assert second_confirmed.kind == "availability_intake"
    assert expansion.map_calls == 1
    assert expansion.capability_calls == 1
    async with factory() as session:
        assert (
            await session.scalar(
                select(func.count(GoalTemplate.id)).where(
                    GoalTemplate.code == "CUSTOM_HYROX_GOAL"
                )
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count(TrainingContext.id)).where(
                    TrainingContext.code == "rowing_general"
                )
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count(Capability.id)).where(
                    Capability.code == "rowing_machine"
                )
            )
            == 1
        )
        second_goal = await ProfileRepository(session).get_training_goal(
            user_id=second_confirmed.user_id
        )
        template = await session.scalar(
            select(GoalTemplate).where(GoalTemplate.code == "CUSTOM_HYROX_GOAL")
        )
        assert second_goal is not None
        assert template is not None
        assert second_goal.goal_template_id == template.id
