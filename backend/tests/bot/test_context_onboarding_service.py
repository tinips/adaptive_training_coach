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
from app.schemas.profile_settings import ProfileSettingsResult, ProfileSettingsStep
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
async def test_profile_settings_callbacks_strip_the_transport_prefix() -> None:
    identity = _identity()
    onboarding = SimpleNamespace(
        open_profile_settings=AsyncMock(
            return_value=ProfileSettingsResult(step=ProfileSettingsStep.MENU)
        ),
        choose_profile_settings=AsyncMock(
            return_value=ProfileSettingsResult(step=ProfileSettingsStep.AVAILABILITY)
        ),
    )
    facade = _facade(onboarding)

    opened = await facade.handle_callback(identity, "ps:v1:open")
    availability = await facade.handle_callback(identity, "ps:v1:section:availability")

    onboarding.open_profile_settings.assert_awaited_once_with(identity)
    onboarding.choose_profile_settings.assert_awaited_once_with(
        identity, "section:availability"
    )
    assert opened.text == messages.PROFILE_SETTINGS_MENU
    assert availability.text == messages.PROFILE_AVAILABILITY


@pytest.mark.asyncio
async def test_development_step_bypasses_global_agent_for_completed_accounts() -> None:
    identity = _identity()
    onboarding = SimpleNamespace(
        seed_development_step=AsyncMock(
            return_value=_result(
                "availability_intake", OnboardingStep.AVAILABILITY_INTAKE
            )
        )
    )
    workspace = SimpleNamespace(invoke=AsyncMock())
    response = await _facade(onboarding, agent_workspace=workspace).handle_agent_input(
        identity, HumanMessage(content="/dev_step availability")
    )

    onboarding.seed_development_step.assert_awaited_once_with(identity, "availability")
    workspace.invoke.assert_not_awaited()
    assert response.text == messages.AVAILABILITY_INTAKE


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
            answers={
                "equipment_recommendation_text": (
                    "Equipment      Importance  When needed\n"
                    "-------------  ----------  ---------------------\n"
                    "Running shoes  Essential   Start now — every run"
                )
            },
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
    assert "swim at a pool" in availability.text
    assert "ride for up to two hours" in availability.text
    assert availability.keyboard == keyboards.profile_text_input_keyboard()
    assert "<pre>" in equipment.text
    assert "Equipment      Importance  When needed" in equipment.text
    assert "Running shoes  Essential   Start now" in equipment.text
    assert equipment.keyboard == keyboards.equipment_intake_keyboard()
    assert details.text == messages.EQUIPMENT_DETAILS_INTAKE
    assert details.keyboard == keyboards.equipment_details_keyboard()
    assert health.text == messages.HEALTH_LIMITATIONS_INTAKE
    assert health.keyboard == keyboards.health_limitations_keyboard()


@pytest.mark.asyncio
async def test_goal_date_clarification_renders_date_choices() -> None:
    response = await _facade(SimpleNamespace())._render_onboarding(
        _identity(),
        _result(
            "goal_clarification",
            OnboardingStep.GOAL_INTAKE,
            answers={"_goal_clarification_field": "event_date"},
        ),
    )

    assert response.text == "When is the event? Send YYYY-MM-DD, or choose Not yet."
    assert response.keyboard == keyboards.goal_date_clarification_keyboard()


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
