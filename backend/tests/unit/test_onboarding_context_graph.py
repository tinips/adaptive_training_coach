"""Tests for the compiled free-text onboarding boundary."""

from __future__ import annotations

import pytest

from app.config import Settings
from app.domain.enums import OnboardingStep
from app.integrations.llm.mock import FakeLLMScenario
from app.workflows.onboarding_context.graph import create_context_onboarding_workflow


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "step,text",
    [
        (OnboardingStep.AVAILABILITY_INTAKE, "Tuesday and Saturday mornings."),
        (OnboardingStep.HEALTH_LIMITATIONS_INTAKE, "No current limitations."),
    ],
)
async def test_context_validator_accepts_supported_nonempty_text(
    step: OnboardingStep,
    text: str,
) -> None:
    workflow = create_context_onboarding_workflow(
        Settings(environment="test", llm_mode="mock", llm_model="mock-context")
    )

    result = await workflow.validate_free_text(step=step, user_text=text)

    assert result.outcome == "accepted"
    assert result.error_code is None


@pytest.mark.asyncio
async def test_context_validator_retries_empty_text() -> None:
    workflow = create_context_onboarding_workflow(
        Settings(environment="test", llm_mode="mock", llm_model="mock-context")
    )

    result = await workflow.validate_free_text(
        step=OnboardingStep.AVAILABILITY_INTAKE,
        user_text="   ",
    )

    assert result.outcome == "retry_required"
    assert result.error_code == "empty_text"


@pytest.mark.asyncio
async def test_context_text_acceptance_does_not_call_a_failing_provider() -> None:
    workflow = create_context_onboarding_workflow(
        Settings(environment="test", llm_mode="mock", llm_model="mock-context"),
        fake_scenario=FakeLLMScenario.PROVIDER_FAILURE,
    )

    result = await workflow.validate_free_text(
        step=OnboardingStep.HEALTH_LIMITATIONS_INTAKE,
        user_text="private limitation text must not appear in the result",
    )

    assert result.outcome == "accepted"
    assert "private limitation" not in str(result.model_dump())


@pytest.mark.asyncio
async def test_context_validator_rejects_non_free_text_step() -> None:
    workflow = create_context_onboarding_workflow(
        Settings(environment="test", llm_mode="mock", llm_model="mock-context")
    )

    result = await workflow.validate_free_text(
        step=OnboardingStep.EQUIPMENT_INTAKE,
        user_text="anything",
    )

    assert result.outcome == "provider_error"
    assert result.error_code == "unsupported_context_step"
