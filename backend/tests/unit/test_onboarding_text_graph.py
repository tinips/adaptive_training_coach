"""Focused topology and routing tests for the compiled stateless LangGraph."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.config import Settings
from app.domain.enums import OnboardingStep
from app.integrations.llm.factory import create_onboarding_text_model
from app.integrations.llm.mock import (
    DeterministicFakeOnboardingModel,
    FakeLLMScenario,
)
from app.observability.protocol import (
    AIWorkflowRunError,
    AIWorkflowRunMetadata,
    AIWorkflowRunResult,
)
from app.schemas.onboarding import OnboardingParseResult
from app.workflows.onboarding_text.graph import (
    LangGraphOnboardingTextParser,
    build_onboarding_text_graph,
    create_onboarding_text_parser,
)
from app.workflows.onboarding_text.nodes import build_messages


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "environment": "test",
        "llm_mode": "mock",
        "llm_min_confidence": 0.75,
        "llm_model": "test-economical-model",
    }
    values.update(overrides)
    return Settings.model_validate(values)


def _parser_for(
    scenario: FakeLLMScenario,
) -> LangGraphOnboardingTextParser:
    model = DeterministicFakeOnboardingModel(scenario=scenario)
    graph = build_onboarding_text_graph(model=model, min_confidence=0.75)
    return LangGraphOnboardingTextParser(
        graph=graph,
        model=model,
        workflow_name="test_onboarding_text",
    )


@pytest.mark.asyncio
async def test_valid_parse_routes_to_typed_confirmation() -> None:
    parser = _parser_for(FakeLLMScenario.SUCCESS)
    result = await parser.parse(
        user_id=uuid4(),
        step=OnboardingStep.PRIMARY_SPORT,
        user_text="córrer",
        confirmed_context={},
    )
    assert result.outcome == "confirmation_required"
    assert isinstance(result.parse_result, OnboardingParseResult)
    assert result.parse_result.normalized_value == "RUNNING"
    assert result.parse_result.display_value == "córrer"
    assert result.prompt_tokens == 8
    assert result.completion_tokens == 12


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scenario", "outcome", "error_code"),
    [
        (
            FakeLLMScenario.LOW_CONFIDENCE,
            "clarification_required",
            "low_confidence",
        ),
        (
            FakeLLMScenario.CLARIFICATION,
            "clarification_required",
            "clarification_requested",
        ),
        (
            FakeLLMScenario.MALFORMED,
            "fallback_required",
            "malformed_structured_output",
        ),
        (
            FakeLLMScenario.PROVIDER_FAILURE,
            "provider_error",
            "mock_provider_failure",
        ),
        (
            FakeLLMScenario.TIMEOUT,
            "provider_error",
            "provider_timeout",
        ),
    ],
)
async def test_required_fake_scenarios_route_safely(
    scenario: FakeLLMScenario,
    outcome: str,
    error_code: str,
) -> None:
    result = await _parser_for(scenario).parse(
        user_id=uuid4(),
        step=OnboardingStep.GOAL_TYPE,
        user_text="A multilingual answer",
        confirmed_context={"primary_sport": "RUNNING"},
    )
    assert result.outcome == outcome
    assert result.error_code == error_code
    if outcome in {"fallback_required", "provider_error"}:
        assert result.parse_result is None


def test_compiled_graph_has_one_explicit_stateless_topology() -> None:
    parser = create_onboarding_text_parser(_settings())
    graph = parser.graph
    nodes = set(graph.get_graph().nodes)
    assert nodes == {
        "__start__",
        "parse_with_model",
        "validate_structured_output",
        "route_result",
        "confirmation_required",
        "clarification_required",
        "fallback_required",
        "provider_error",
        "__end__",
    }
    assert graph.checkpointer is None


@pytest.mark.asyncio
async def test_live_and_mock_modes_share_topology_and_live_key_is_lazy() -> None:
    mock_settings = _settings(llm_mode="mock")
    live_settings = _settings(llm_mode="live", llm_api_key=None)
    mock_parser = create_onboarding_text_parser(mock_settings)
    live_parser = create_onboarding_text_parser(live_settings)
    assert set(mock_parser.graph.get_graph().nodes) == set(
        live_parser.graph.get_graph().nodes
    )

    result = await live_parser.parse(
        user_id=uuid4(),
        step=OnboardingStep.PRIMARY_SPORT,
        user_text="running",
        confirmed_context={},
    )
    assert result.outcome == "provider_error"
    assert result.error_code == "llm_api_key_missing"


def test_factory_returns_model_for_each_supported_mode() -> None:
    mock = create_onboarding_text_model(_settings(llm_mode="mock"))
    live = create_onboarding_text_model(_settings(llm_mode="live", llm_api_key=None))
    assert mock.provider_mode == "mock"
    assert live.provider_mode == "live"


def test_prompt_context_is_step_scoped_and_excludes_sensitive_unrelated_data() -> None:
    messages = build_messages(
        step=OnboardingStep.GOAL_TYPE,
        user_text="Vull millorar",
        confirmed_context={
            "primary_sport": "RUNNING",
            "health_description": "sensitive text",
            "telegram_token": "secret",
            "complete_profile": {"age": 42},
        },
    )
    system_content = str(messages[0].content)
    assert "RUNNING" in system_content
    assert "sensitive text" not in system_content
    assert "secret" not in system_content
    assert "complete_profile" not in system_content


class _RecordingObserver:
    def __init__(self) -> None:
        self.started: list[AIWorkflowRunMetadata] = []
        self.completed: list[AIWorkflowRunResult] = []
        self.failed: list[AIWorkflowRunError] = []

    async def on_run_started(self, metadata: AIWorkflowRunMetadata) -> None:
        self.started.append(metadata)

    async def on_run_completed(self, result: AIWorkflowRunResult) -> None:
        self.completed.append(result)

    async def on_run_failed(self, error: AIWorkflowRunError) -> None:
        self.failed.append(error)


@pytest.mark.asyncio
async def test_observer_receives_safe_metadata_without_user_text_or_profile() -> None:
    observer = _RecordingObserver()
    model = DeterministicFakeOnboardingModel()
    parser = LangGraphOnboardingTextParser(
        graph=build_onboarding_text_graph(model=model, min_confidence=0.75),
        model=model,
        workflow_name="safe_workflow",
        observer=observer,
    )
    await parser.parse(
        user_id=uuid4(),
        step=OnboardingStep.GOAL_TYPE,
        user_text="private raw text",
        confirmed_context={
            "primary_sport": "RUNNING",
            "health_description": "private health detail",
        },
    )
    assert len(observer.started) == 1
    assert len(observer.completed) == 1
    assert observer.failed == []
    metadata = observer.started[0].model_dump()
    assert "user_text" not in metadata
    assert "user_id" not in metadata
    assert "confirmed_context" not in metadata
    assert "private raw text" not in repr(metadata)
    assert "private health detail" not in repr(metadata)
