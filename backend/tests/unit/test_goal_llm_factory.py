"""Focused tests for goal-model provider selection."""

from app.config import Settings
from app.integrations.llm.factory import create_goal_extraction_model
from app.integrations.llm.live import OpenAICompatibleOnboardingModel
from app.integrations.llm.mock import DeterministicFakeOnboardingModel


def test_goal_model_factory_selects_mock_provider() -> None:
    model = create_goal_extraction_model(
        Settings(environment="test", llm_mode="mock", llm_model="mock-goal")
    )

    assert isinstance(model, DeterministicFakeOnboardingModel)
    assert model.provider_mode == "mock"
    assert model.model_name == "mock-goal"


def test_goal_model_factory_selects_live_provider_without_invoking_it() -> None:
    model = create_goal_extraction_model(
        Settings(
            environment="test",
            llm_mode="live",
            llm_api_key="test-only-key",
            llm_model="live-goal",
        )
    )

    assert isinstance(model, OpenAICompatibleOnboardingModel)
    assert model.provider_mode == "live"
    assert model.model_name == "live-goal"
