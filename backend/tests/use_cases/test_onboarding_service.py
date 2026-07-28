"""Persistence-aware onboarding journey and invariant tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
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
from app.domain.enums import (
    LLMUsageStatus,
    OnboardingStep,
)
from app.repositories.llm_usage import LLMUsageRepository
from app.repositories.onboarding import OnboardingRepository
from app.schemas.common import TelegramIdentity
from app.schemas.onboarding import OnboardingParseResult, OnboardingTextWorkflowResult
from app.services.onboarding import OnboardingApplicationError, OnboardingService
from app.workflows.onboarding_text.graph import create_onboarding_text_parser


@pytest_asyncio.fixture
async def onboarding_database() -> AsyncIterator[
    tuple[AsyncEngine, async_sessionmaker[AsyncSession]]
]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    yield engine, factory
    await engine.dispose()


def identity(telegram_id: int = 4001) -> TelegramIdentity:
    return TelegramIdentity(
        telegram_user_id=telegram_id,
        telegram_username=f"athlete_{telegram_id}",
        first_name="Athlete",
        language_code="ca",
    )


def settings(**updates: Any) -> Settings:
    values: dict[str, Any] = {
        "environment": "test",
        "database_url": "sqlite+aiosqlite:///:memory:",
        "llm_mode": "mock",
    }
    values.update(updates)
    return Settings(**values)


@dataclass
class CountingParser:
    result: OnboardingTextWorkflowResult
    calls: int = 0

    async def parse(
        self,
        *,
        user_id: UUID,
        step: OnboardingStep,
        user_text: str,
        confirmed_context: dict[str, object],
    ) -> OnboardingTextWorkflowResult:
        del user_id, step, user_text, confirmed_context
        self.calls += 1
        return self.result


@dataclass
class GatedParser:
    started: asyncio.Event
    release: asyncio.Event
    calls: int = 0

    async def parse(
        self,
        *,
        user_id: UUID,
        step: OnboardingStep,
        user_text: str,
        confirmed_context: dict[str, object],
    ) -> OnboardingTextWorkflowResult:
        del user_id, step, user_text, confirmed_context
        self.calls += 1
        self.started.set()
        await self.release.wait()
        return OnboardingTextWorkflowResult(
            outcome="confirmation_required",
            parse_result=OnboardingParseResult(
                normalized_value="RUNNING",
                display_value="Running",
                confidence=0.99,
            ),
        )


def unused_parser() -> CountingParser:
    return CountingParser(
        OnboardingTextWorkflowResult(
            outcome="provider_error",
            error_code="must_not_be_called",
        )
    )


@pytest.mark.asyncio
async def test_complete_deterministic_onboarding_and_resume_after_restart(
    onboarding_database: tuple[
        AsyncEngine,
        async_sessionmaker[AsyncSession],
    ],
) -> None:
    _, factory = onboarding_database
    parser = unused_parser()
    service = OnboardingService(
        session_factory=factory,
        text_parser=parser,
        settings=settings(),
    )
    athlete = identity()

    result = await service.start(athlete)
    assert result.created
    await service.choose(athlete, "CONTINUE")
    await service.choose(athlete, "RUNNING")
    await service.choose(athlete, "TEN_K")
    await service.choose(athlete, "NO")
    await service.choose(athlete, "FINISH_SAFELY")
    await service.handle_text(athlete, "35")
    await service.skip(athlete)
    await service.skip(athlete)
    await service.toggle(athlete, "MONDAY")
    await service.toggle(athlete, "SATURDAY")
    await service.continue_multiselect(athlete)
    await service.choose(athlete, 60)
    await service.choose(athlete, 120)
    await service.toggle(athlete, "RUNNING_SHOES")
    await service.continue_multiselect(athlete)
    await service.toggle(athlete, "NONE")
    await service.continue_multiselect(athlete)
    await service.choose(athlete, "CONCISE_PRACTICAL")
    await service.choose(athlete, "MEDIUM")
    result = await service.choose(athlete, "SKIP_FOR_NOW")

    assert result.kind == "summary"
    assert result.current_step is OnboardingStep.SUMMARY
    assert result.answers["training_days"] == ["MONDAY", "SATURDAY"]
    assert "pool_access" not in result.answers
    assert "bike_access" not in result.answers
    assert parser.calls == 0

    restarted_process_service = OnboardingService(
        session_factory=factory,
        text_parser=parser,
        settings=settings(),
    )
    resumed = await restarted_process_service.start(athlete)
    assert resumed.current_step is OnboardingStep.SUMMARY
    assert resumed.answers == result.answers


@pytest.mark.asyncio
async def test_free_text_uses_graph_and_requires_confirmation_before_answer(
    onboarding_database: tuple[
        AsyncEngine,
        async_sessionmaker[AsyncSession],
    ],
) -> None:
    _, factory = onboarding_database
    runtime_settings = settings()
    parser = create_onboarding_text_parser(runtime_settings)
    service = OnboardingService(
        session_factory=factory,
        text_parser=parser,
        settings=runtime_settings,
    )
    athlete = identity(4002)
    await service.start(athlete)
    await service.choose(athlete, "CONTINUE")
    awaiting = await service.begin_free_text(athlete)

    assert awaiting.kind == "awaiting_text"
    interpreted = await service.handle_text(athlete, "córrer")

    assert interpreted.kind == "interpretation"
    assert "primary_sport" not in interpreted.answers
    assert interpreted.parse_result is not None
    assert interpreted.parse_result.normalized_value == "RUNNING"

    confirmed = await service.confirm_parsed(athlete)
    assert confirmed.answers["primary_sport"] == "RUNNING"
    assert confirmed.current_step is OnboardingStep.GOAL_TYPE


@pytest.mark.asyncio
async def test_forged_other_callback_cannot_enable_llm_on_consent(
    onboarding_database: tuple[
        AsyncEngine,
        async_sessionmaker[AsyncSession],
    ],
) -> None:
    _, factory = onboarding_database
    parser = unused_parser()
    service = OnboardingService(
        session_factory=factory,
        text_parser=parser,
        settings=settings(),
    )
    athlete = identity(4011)
    await service.start(athlete)

    with pytest.raises(OnboardingApplicationError) as failure:
        await service.begin_free_text(athlete)

    assert failure.value.code == "invalid_action"
    assert parser.calls == 0


@pytest.mark.asyncio
async def test_rejected_interpretation_can_retry_without_staging_value(
    onboarding_database: tuple[
        AsyncEngine,
        async_sessionmaker[AsyncSession],
    ],
) -> None:
    _, factory = onboarding_database
    runtime_settings = settings()
    service = OnboardingService(
        session_factory=factory,
        text_parser=create_onboarding_text_parser(runtime_settings),
        settings=runtime_settings,
    )
    athlete = identity(4003)
    await service.start(athlete)
    await service.choose(athlete, "CONTINUE")
    await service.begin_free_text(athlete)
    await service.handle_text(athlete, "un esport diferent")

    retry = await service.retry_parsed(athlete)

    assert retry.kind == "awaiting_text"
    assert "primary_sport" not in retry.answers
    second = await service.handle_text(athlete, "running")
    assert second.kind == "interpretation"


@pytest.mark.asyncio
async def test_low_confidence_does_not_persist_pending_value(
    onboarding_database: tuple[
        AsyncEngine,
        async_sessionmaker[AsyncSession],
    ],
) -> None:
    _, factory = onboarding_database
    runtime_settings = settings()
    service = OnboardingService(
        session_factory=factory,
        text_parser=create_onboarding_text_parser(runtime_settings),
        settings=runtime_settings,
    )
    athlete = identity(4004)
    started = await service.start(athlete)
    await service.choose(athlete, "CONTINUE")
    await service.begin_free_text(athlete)

    result = await service.handle_text(
        athlete,
        "mock:low_confidence:running",
    )

    assert result.kind == "clarification"
    assert "primary_sport" not in result.answers
    async with factory() as session:
        onboarding = await OnboardingRepository(session).require_for_user(
            user_id=started.user_id
        )
    assert onboarding.pending_parsed_value is None


@pytest.mark.asyncio
async def test_live_rolling_hour_limit_prevents_provider_invocation(
    onboarding_database: tuple[
        AsyncEngine,
        async_sessionmaker[AsyncSession],
    ],
) -> None:
    _, factory = onboarding_database
    live_settings = settings(
        llm_mode="live",
        llm_other_requests_per_hour=1,
    )
    parser = unused_parser()
    service = OnboardingService(
        session_factory=factory,
        text_parser=parser,
        settings=live_settings,
    )
    athlete = identity(4005)
    started = await service.start(athlete)
    await service.choose(athlete, "CONTINUE")
    await service.begin_free_text(athlete)
    async with factory.begin() as session:
        await LLMUsageRepository(session).record(
            user_id=started.user_id,
            onboarding_step=OnboardingStep.PRIMARY_SPORT,
            provider_mode="live",
            model="economical-model",
            status=LLMUsageStatus.SUCCEEDED,
        )

    result = await service.handle_text(athlete, "running")

    assert result.kind == "rate_limited"
    assert parser.calls == 0
    assert "primary_sport" not in result.answers


@pytest.mark.asyncio
async def test_live_limit_does_not_count_prior_mock_usage(
    onboarding_database: tuple[
        AsyncEngine,
        async_sessionmaker[AsyncSession],
    ],
) -> None:
    _, factory = onboarding_database
    live_settings = settings(
        llm_mode="live",
        llm_other_requests_per_hour=1,
    )
    parser = unused_parser()
    service = OnboardingService(
        session_factory=factory,
        text_parser=parser,
        settings=live_settings,
    )
    athlete = identity(4010)
    started = await service.start(athlete)
    await service.choose(athlete, "CONTINUE")
    await service.begin_free_text(athlete)
    async with factory.begin() as session:
        await LLMUsageRepository(session).record(
            user_id=started.user_id,
            onboarding_step=OnboardingStep.PRIMARY_SPORT,
            provider_mode="mock",
            model="deterministic-fake",
            status=LLMUsageStatus.SUCCEEDED,
        )

    result = await service.handle_text(athlete, "running")

    assert result.kind == "provider_error"
    assert parser.calls == 1


@pytest.mark.asyncio
async def test_pending_parse_is_strictly_user_isolated(
    onboarding_database: tuple[
        AsyncEngine,
        async_sessionmaker[AsyncSession],
    ],
) -> None:
    _, factory = onboarding_database
    runtime_settings = settings()
    service = OnboardingService(
        session_factory=factory,
        text_parser=create_onboarding_text_parser(runtime_settings),
        settings=runtime_settings,
    )
    owner = identity(4006)
    other = identity(4007)
    owner_result = await service.start(owner)
    other_result = await service.start(other)
    await service.choose(owner, "CONTINUE")
    await service.begin_free_text(owner)
    await service.handle_text(owner, "running")

    with pytest.raises(OnboardingApplicationError) as failure:
        await service.confirm_parsed(other)

    assert failure.value.code == "parsed_value_missing"
    async with factory() as session:
        owner_state = await OnboardingRepository(session).require_for_user(
            user_id=owner_result.user_id
        )
        other_state = await OnboardingRepository(session).require_for_user(
            user_id=other_result.user_id
        )
        assert owner_state.pending_parsed_value is not None
        assert other_state.pending_parsed_value is None


@pytest.mark.asyncio
async def test_cancel_and_restart_reset_only_the_owned_session(
    onboarding_database: tuple[
        AsyncEngine,
        async_sessionmaker[AsyncSession],
    ],
) -> None:
    _, factory = onboarding_database
    service = OnboardingService(
        session_factory=factory,
        text_parser=unused_parser(),
        settings=settings(),
    )
    athlete = identity(4008)
    await service.start(athlete)
    await service.choose(athlete, "CONTINUE")

    cancelled = await service.cancel(athlete)
    restarted = await service.restart(athlete)

    assert cancelled.kind == "cancelled"
    assert restarted.current_step is OnboardingStep.CONSENT
    assert restarted.answers == {}


@pytest.mark.asyncio
async def test_concurrent_free_text_parse_is_rejected_until_first_result_is_ready(
    onboarding_database: tuple[
        AsyncEngine,
        async_sessionmaker[AsyncSession],
    ],
) -> None:
    _, factory = onboarding_database
    parser = GatedParser(asyncio.Event(), asyncio.Event())
    service = OnboardingService(
        session_factory=factory,
        text_parser=parser,
        settings=settings(),
    )
    athlete = identity(4012)
    await service.start(athlete)
    await service.choose(athlete, "CONTINUE")
    await service.begin_free_text(athlete)

    first = asyncio.create_task(service.handle_text(athlete, "running"))
    await asyncio.wait_for(parser.started.wait(), timeout=1)
    with pytest.raises(OnboardingApplicationError) as failure:
        await service.handle_text(athlete, "cycling")

    assert failure.value.code == "parse_in_progress"
    assert parser.calls == 1
    parser.release.set()
    result = await asyncio.wait_for(first, timeout=1)
    assert result.kind == "interpretation"
    assert result.parse_result is not None
    assert result.parse_result.normalized_value == "RUNNING"


@pytest.mark.asyncio
async def test_completed_onboarding_rejects_cancel_restart_and_back_replays(
    onboarding_database: tuple[
        AsyncEngine,
        async_sessionmaker[AsyncSession],
    ],
) -> None:
    _, factory = onboarding_database
    service = OnboardingService(
        session_factory=factory,
        text_parser=unused_parser(),
        settings=settings(),
    )
    athlete = identity(4013)
    started = await service.start(athlete)
    async with factory.begin() as session:
        await OnboardingRepository(session).complete(user_id=started.user_id)

    with pytest.raises(OnboardingApplicationError) as cancel_failure:
        await service.cancel(athlete)
    with pytest.raises(OnboardingApplicationError) as restart_failure:
        await service.restart(athlete)
    with pytest.raises(OnboardingApplicationError) as back_failure:
        await service.back(athlete)

    assert cancel_failure.value.code == "onboarding_not_active"
    assert restart_failure.value.code == "restart_not_allowed"
    assert back_failure.value.code == "onboarding_not_active"
    snapshot = await service.snapshot(athlete)
    assert snapshot.kind == "completed"
    assert snapshot.current_step is OnboardingStep.SUMMARY
