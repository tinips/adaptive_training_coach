"""Focused persistence tests for the conversational onboarding goal step."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import Settings
from app.db.base import Base
from app.domain.enums import OnboardingStep, TrainingGoalStatus
from app.integrations.llm.models import GoalExtractionOutput
from app.repositories.onboarding import OnboardingRepository
from app.repositories.profiles import ProfileRepository
from app.schemas.common import TelegramIdentity
from app.schemas.onboarding_goal import GoalExtractionWorkflowResult
from app.services.onboarding import OnboardingService


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
    calls: list[tuple[str, GoalExtractionOutput | None]] = field(default_factory=list)

    async def extract(
        self,
        *,
        user_id: UUID,
        user_text: str,
        existing_draft: GoalExtractionOutput | None,
    ) -> GoalExtractionWorkflowResult:
        del user_id
        self.calls.append((user_text, existing_draft))
        return self.results.pop(0)


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
        goal_draft=GoalExtractionOutput.model_validate(
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
    assert extractor.calls == [(raw, None)]
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
    assert extractor.calls[1][1] is not None
    assert extractor.calls[1][1].main_goal == "Complete a marathon"
    assert extractor.calls[2][1] is not None
    assert extractor.calls[2][1].target_outcome == "Finish safely"


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
async def test_confirmation_persists_canonical_goal_and_stops_at_checkpoint(
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

    assert draft.kind == "goal_confirmation"
    assert confirmed.kind == "goal_confirmed"
    assert confirmed.current_step is OnboardingStep.GOAL_CONFIRMED
    assert "goal_draft" not in confirmed.answers
    assert confirmed.answers["raw_goal_text"] == raw
    async with factory() as session:
        goal = await ProfileRepository(session).get_training_goal(
            user_id=confirmed.user_id
        )
        assert goal is not None
        assert goal.main_goal == "Improve 5 km performance"
        assert goal.event_date is not None
        assert goal.event_date.isoformat() == "2026-10-04"
        assert goal.target_outcome == "Run under 24 minutes"
        assert goal.secondary_priority is None
        assert goal.original_description == raw
        assert goal.status is TrainingGoalStatus.CONFIRMED


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
