"""Focused tests for goal-model provider selection and live JSON recovery."""

from langchain_core.messages import AIMessage

from app.config import Settings
from app.integrations.llm.factory import create_goal_extraction_model
from app.integrations.llm.live import (
    OpenAICompatibleOnboardingModel,
    _recover_structured_json,
)
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


def test_live_adapter_recovers_valid_json_when_langchain_parsed_is_empty() -> None:
    output, malformed = _recover_structured_json(
        parsed=None,
        raw=AIMessage(content='{"items":[{"equipment_name":"Shoes"}]}'),
    )

    assert output == {"items": [{"equipment_name": "Shoes"}]}
    assert malformed is False


def test_live_adapter_keeps_invalid_raw_content_malformed() -> None:
    output, malformed = _recover_structured_json(
        parsed=None,
        raw=AIMessage(content="not json"),
    )

    assert output is None
    assert malformed is True
