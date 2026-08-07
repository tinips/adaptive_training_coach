"""Telegram rendering and deterministic callbacks for context onboarding."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from langchain_core.messages import HumanMessage

from app.bot import keyboards, messages
from app.bot.service import CoachBotApplicationService
from app.domain.enums import OnboardingStatus, OnboardingStep, UserStatus
from app.schemas.common import TelegramIdentity
from app.schemas.onboarding_service import OnboardingResultKind, OnboardingServiceResult
from app.services.onboarding import OnboardingService


def _identity() -> TelegramIdentity:
    return TelegramIdentity(
        telegram_user_id=8172,
        telegram_username="runner",
        first_name="Ada",
        language_code="en",
    )


def _result(
    kind: OnboardingResultKind,
    step: OnboardingStep,
    *,
    answers: dict[str, str] | None = None,
) -> OnboardingServiceResult:
    return OnboardingServiceResult(
        kind=kind,
        user_id=uuid4(),
        user_status=UserStatus.ONBOARDING_IN_PROGRESS,
        onboarding_status=OnboardingStatus.ACTIVE,
        current_step=step,
        answers=answers or {},
    )


def _facade(
    onboarding: object,
    *,
    account_queries: object | None = None,
    agent_workspace: object | None = None,
) -> CoachBotApplicationService:
    return CoachBotApplicationService(
        onboarding=cast(OnboardingService, onboarding),
        profiles=SimpleNamespace(),
        account_queries=cast(object, account_queries or SimpleNamespace()),
        accounts=SimpleNamespace(),
        strava=SimpleNamespace(),
        agent_workspace=cast(object, agent_workspace),
    )


@pytest.mark.asyncio
async def test_equipment_callback_uses_only_deterministic_onboarding_method() -> None:
    identity = _identity()
    onboarding = SimpleNamespace(
        choose_equipment=AsyncMock(
            return_value=_result(
                "health_limitations_intake",
                OnboardingStep.HEALTH_LIMITATIONS_INTAKE,
            )
        )
    )

    response = await _facade(onboarding).handle_callback(
        identity,
        "ob:v1:equipment:all",
    )

    onboarding.choose_equipment.assert_awaited_once_with(identity, "all")
    assert response.text == messages.HEALTH_LIMITATIONS_INTAKE
    assert response.keyboard == keyboards.health_limitations_keyboard()
    assert response.edit_existing is True


@pytest.mark.asyncio
async def test_health_callback_uses_only_deterministic_onboarding_method() -> None:
    identity = _identity()
    onboarding = SimpleNamespace(
        choose_health_limitations=AsyncMock(
            return_value=_result(
                "onboarding_completed",
                OnboardingStep.HEALTH_LIMITATIONS_INTAKE,
            )
        )
    )

    response = await _facade(onboarding).handle_callback(
        identity,
        "ob:v1:health:none",
    )

    onboarding.choose_health_limitations.assert_awaited_once_with(identity, "none")
    assert response.text == messages.ONBOARDING_COMPLETED
    assert response.edit_existing is True


@pytest.mark.asyncio
async def test_context_steps_render_the_correct_prompt_and_controls() -> None:
    facade = _facade(SimpleNamespace())
    identity = _identity()

    availability = await facade._render_onboarding(
        identity,
        _result("availability_intake", OnboardingStep.AVAILABILITY_INTAKE),
    )
    equipment = await facade._render_onboarding(
        identity,
        _result(
            "equipment_recommendation",
            OnboardingStep.EQUIPMENT_INTAKE,
            answers={"equipment_recommendation_text": "Running shoes and a watch."},
        ),
    )
    details = await facade._render_onboarding(
        identity,
        _result(
            "equipment_details_intake",
            OnboardingStep.EQUIPMENT_DETAILS_INTAKE,
        ),
    )
    health = await facade._render_onboarding(
        identity,
        _result(
            "health_limitations_intake",
            OnboardingStep.HEALTH_LIMITATIONS_INTAKE,
        ),
    )

    assert availability.text == messages.AVAILABILITY_INTAKE
    assert availability.keyboard == keyboards.profile_text_input_keyboard()
    assert "Running shoes and a watch." in equipment.text
    assert equipment.keyboard == keyboards.equipment_intake_keyboard()
    assert details.text == messages.EQUIPMENT_DETAILS_INTAKE
    assert details.keyboard == keyboards.profile_text_input_keyboard()
    assert health.text == messages.HEALTH_LIMITATIONS_INTAKE
    assert health.keyboard == keyboards.health_limitations_keyboard()


@pytest.mark.asyncio
async def test_context_validation_error_repeats_the_relevant_prompt() -> None:
    response = await _facade(SimpleNamespace())._render_onboarding(
        _identity(),
        _result(
            "context_validation_error",
            OnboardingStep.EQUIPMENT_DETAILS_INTAKE,
        ),
    )

    assert response.text == (
        f"{messages.CONTEXT_VALIDATION_ERROR}\n\n{messages.EQUIPMENT_DETAILS_INTAKE}"
    )
    assert response.keyboard == keyboards.profile_text_input_keyboard()


@pytest.mark.asyncio
async def test_active_onboarding_raw_context_bypasses_global_agent_checkpoint() -> None:
    identity = _identity()
    onboarding = SimpleNamespace(
        handle_text=AsyncMock(
            return_value=_result(
                "availability_intake",
                OnboardingStep.AVAILABILITY_INTAKE,
            )
        )
    )
    account_queries = SimpleNamespace(
        lifecycle=AsyncMock(
            return_value={
                "user_id": uuid4(),
                "status": UserStatus.ONBOARDING_IN_PROGRESS,
            }
        )
    )
    workspace = SimpleNamespace(
        invoke=AsyncMock(),
        delete_thread=AsyncMock(),
    )
    facade = _facade(
        onboarding,
        account_queries=account_queries,
        agent_workspace=workspace,
    )
    raw_limitation = "Private limitation: avoid hard downhill running."

    response = await facade.handle_agent_input(
        identity,
        HumanMessage(
            content=raw_limitation,
            additional_kwargs={"telegram_event_type": "text"},
        ),
    )

    onboarding.handle_text.assert_awaited_once_with(identity, raw_limitation)
    workspace.invoke.assert_not_awaited()
    workspace.delete_thread.assert_not_awaited()
    assert response.text == messages.AVAILABILITY_INTAKE
