"""Single construction point for mock and live onboarding model adapters."""

from __future__ import annotations

from app.config import Settings
from app.integrations.llm.live import OpenAICompatibleOnboardingModel
from app.integrations.llm.mock import (
    DeterministicFakeOnboardingModel,
    FakeLLMScenario,
)
from app.integrations.llm.models import StructuredOnboardingModel


def create_goal_extraction_model(
    settings: Settings,
    *,
    fake_scenario: FakeLLMScenario = FakeLLMScenario.AUTO,
    timeout_seconds: float = 30.0,
    model_name: str | None = None,
) -> StructuredOnboardingModel:
    """Create the focused goal model adapter without workflow topology."""

    if settings.llm_mode == "mock":
        return DeterministicFakeOnboardingModel(
            scenario=fake_scenario,
            model_name=model_name or settings.llm_model,
        )
    return OpenAICompatibleOnboardingModel(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url or None,
        model_name=model_name or settings.llm_model,
        timeout_seconds=timeout_seconds,
    )
