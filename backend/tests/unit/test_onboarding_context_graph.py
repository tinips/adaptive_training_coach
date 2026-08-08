"""Focused tests for compiled availability, equipment, and limitation workflows."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.runnables import Runnable, RunnableConfig, RunnableLambda
from langchain_core.tools import BaseTool

from app.config import Settings
from app.domain.enums import OnboardingStep
from app.integrations.llm.mock import FakeLLMScenario
from app.integrations.llm.models import (
    StructuredModelResponse,
    StructuredOutputSchema,
)
from app.observability.protocol import ProviderMode
from app.schemas.onboarding_context import EquipmentRecommendationGoalContext
from app.workflows.onboarding_context.graph import (
    LangGraphContextOnboardingWorkflow,
    build_equipment_recommendation_graph,
    build_free_text_validation_graph,
    create_context_onboarding_workflow,
)
from app.workflows.onboarding_context.nodes import (
    EquipmentRecommendationItem,
    EquipmentRecommendationOutput,
    format_equipment_recommendation,
)


@dataclass
class StructuredContextModel:
    """Controllable provider boundary for stateless context graph tests."""

    responses: list[StructuredModelResponse]
    schemas: list[StructuredOutputSchema] = field(default_factory=list)
    steps: list[OnboardingStep] = field(default_factory=list)
    messages: list[list[BaseMessage]] = field(default_factory=list)

    @property
    def provider_mode(self) -> ProviderMode:
        return "mock"

    @property
    def model_name(self) -> str:
        return "structured-context-test"

    async def ainvoke_structured(
        self,
        *,
        step: OnboardingStep,
        schema: StructuredOutputSchema,
        messages: list[BaseMessage],
        config: RunnableConfig,
    ) -> StructuredModelResponse:
        del config
        self.steps.append(step)
        self.schemas.append(schema)
        self.messages.append(messages)
        return self.responses.pop(0)

    def bind_tools(
        self,
        tools: Sequence[BaseTool],
    ) -> Runnable[Any, AIMessage]:
        del tools
        return RunnableLambda(lambda _: AIMessage(content=""))


def _workflow(model: StructuredContextModel) -> LangGraphContextOnboardingWorkflow:
    return LangGraphContextOnboardingWorkflow(
        free_text_validation_graph=build_free_text_validation_graph(model=model),
        equipment_recommendation_graph=build_equipment_recommendation_graph(
            model=model,
        ),
        model=model,
        workflow_name="onboarding-context-test",
    )


@pytest.mark.asyncio
async def test_context_validator_accepts_raw_availability_without_returning_it() -> (
    None
):
    model = StructuredContextModel(responses=[])
    workflow = _workflow(model)
    raw_answer = "I can train Tuesday and Thursday after 18:00, plus Saturday."

    result = await workflow.validate_free_text(
        step=OnboardingStep.AVAILABILITY_INTAKE,
        user_text=raw_answer,
        goal_context=EquipmentRecommendationGoalContext(
            main_goal="Complete a marathon",
            target_outcome="Finish comfortably",
        ),
    )

    assert result.outcome == "accepted"
    assert result.error_code is None
    assert result.model_dump() == {
        "outcome": "accepted",
        "error_code": None,
        "prompt_tokens": None,
        "completion_tokens": None,
    }
    assert model.schemas == []


@pytest.mark.asyncio
async def test_context_validator_accepts_vague_or_unrelated_nonempty_text() -> None:
    model = StructuredContextModel(responses=[])
    workflow = _workflow(model)

    vague = await workflow.validate_text(
        step=OnboardingStep.EQUIPMENT_DETAILS_INTAKE,
        user_text="not sure",
    )
    unrelated = await workflow.validate_free_text(
        step=OnboardingStep.HEALTH_LIMITATIONS_INTAKE,
        user_text="hello",
    )

    assert vague.outcome == "accepted"
    assert unrelated.outcome == "accepted"
    assert model.schemas == []


@pytest.mark.asyncio
async def test_context_validator_retries_empty_text_without_calling_provider() -> None:
    model = StructuredContextModel(responses=[])
    workflow = _workflow(model)

    result = await workflow.validate_free_text(
        step=OnboardingStep.AVAILABILITY_INTAKE,
        user_text="   ",
    )

    assert result.outcome == "retry_required"
    assert result.error_code == "empty_text"
    assert model.schemas == []


@pytest.mark.asyncio
async def test_equipment_recommendation_uses_goal_fields_and_returns_short_text() -> (
    None
):
    model = StructuredContextModel(
        responses=[
            StructuredModelResponse(
                output={
                    "items": [
                        {
                            "equipment_name": "Road bike",
                            "importance": "Essential",
                            "when_needed": "Start now — first rides",
                        },
                        {
                            "equipment_name": "Helmet",
                            "importance": "Essential",
                            "when_needed": "Start now — every ride",
                        },
                        {
                            "equipment_name": "Front and rear lights",
                            "importance": "Recommended",
                            "when_needed": "Base training — lower-light rides",
                        },
                    ]
                },
                prompt_tokens=7,
                completion_tokens=9,
            )
        ]
    )
    workflow = _workflow(model)

    result = await workflow.recommend_equipment(
        main_goal="Finish a cycling gran fondo",
        target_outcome="Complete it comfortably",
        event_date=date(2027, 5, 10),
        secondary_priority=None,
    )

    assert result.outcome == "recommended"
    assert result.recommendation == (
        "Equipment              Importance  When needed\n"
        "---------------------  ----------  ---------------------------------\n"
        "Road bike              Essential   Start now — first rides\n"
        "Helmet                 Essential   Start now — every ride\n"
        "Front and rear lights  Recommended  Base training — lower-light rides"
    )
    assert model.schemas == [EquipmentRecommendationOutput]
    assert model.steps == [OnboardingStep.EQUIPMENT_RECOMMENDATION]
    assert not any(isinstance(message, HumanMessage) for message in model.messages[0])
    prompt = str(model.messages[0][0].content).casefold()
    assert "cycling gran fondo" in prompt
    assert "physical coach" in prompt
    assert "essential, recommended, or optional" in prompt
    assert '"items"' in prompt
    assert '"equipment_name"' in prompt
    assert '"when_needed"' in prompt


@pytest.mark.asyncio
async def test_equipment_recommendation_requires_a_confirmed_main_goal() -> None:
    model = StructuredContextModel(responses=[])
    workflow = _workflow(model)

    result = await workflow.recommend_equipment(
        main_goal=None,
        target_outcome=None,
        event_date=None,
        secondary_priority=None,
    )

    assert result.outcome == "retry_required"
    assert result.error_code == "missing_goal_context"
    assert model.schemas == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "items",
    [
        [
            {"equipment_name": "Running shoes", "importance": "Essential"},
            {"equipment_name": "running shoes", "importance": "Recommended"},
        ],
        [{"equipment_name": "Weekly training plan", "importance": "Essential"}],
        [{"equipment_name": "Knee treatment", "importance": "Essential"}],
        [{"equipment_name": "Running shoes", "importance": "Critical"}],
    ],
)
async def test_equipment_recommendation_rejects_nonessential_or_oversized_output(
    items: list[dict[str, str]],
) -> None:
    model = StructuredContextModel(
        responses=[StructuredModelResponse(output={"items": items})]
    )
    workflow = _workflow(model)

    result = await workflow.recommend_equipment(
        main_goal="Complete a marathon",
        target_outcome="Finish comfortably",
        event_date=None,
        secondary_priority=None,
    )

    assert result.outcome == "retry_required"
    assert result.error_code == "malformed_structured_output"
    assert result.recommendation is None


@pytest.mark.asyncio
async def test_equipment_recommendation_accepts_more_than_five_items() -> None:
    items = [
        {
            "equipment_name": f"Item {index}",
            "importance": "Essential",
            "when_needed": "Start now — first sessions",
        }
        for index in range(1, 7)
    ]
    workflow = _workflow(
        StructuredContextModel(
            responses=[StructuredModelResponse(output={"items": items})]
        )
    )

    result = await workflow.recommend_equipment(
        main_goal="Complete a marathon",
        target_outcome="Finish comfortably",
        event_date=None,
        secondary_priority=None,
    )

    assert result.outcome == "recommended"
    assert result.recommendation is not None
    assert "Item 6" in result.recommendation


@pytest.mark.asyncio
async def test_equipment_recommendation_accepts_a_long_item_name() -> None:
    long_name = "Running shoes suitable for long-distance road training and racing"
    workflow = _workflow(
        StructuredContextModel(
            responses=[
                StructuredModelResponse(
                    output={
                        "items": [
                            {
                                "equipment_name": long_name,
                                "importance": "Essential",
                                "when_needed": "Race-specific prep — long runs",
                            }
                        ]
                    }
                )
            ]
        )
    )

    result = await workflow.recommend_equipment(
        main_goal="Complete a marathon",
        target_outcome="Finish comfortably",
        event_date=None,
        secondary_priority=None,
    )

    assert result.outcome == "recommended"
    assert result.recommendation is not None
    assert long_name in result.recommendation


def test_equipment_table_is_stable_and_has_three_columns() -> None:
    assert format_equipment_recommendation(
        [
            EquipmentRecommendationItem(
                equipment_name="Running shoes",
                importance="Essential",
                when_needed="Start now — every run",
            ),
            EquipmentRecommendationItem(
                equipment_name="Water bottle",
                importance="Recommended",
                when_needed="Base training — longer sessions",
            ),
        ]
    ) == (
        "Equipment      Importance  When needed\n"
        "-------------  ----------  -------------------------------\n"
        "Running shoes  Essential   Start now — every run\n"
        "Water bottle   Recommended  Base training — longer sessions"
    )


def test_equipment_table_rejects_output_beyond_telegram_capacity() -> None:
    items = [
        EquipmentRecommendationItem(
            equipment_name=f"Equipment item number {index} for long-distance training",
            importance="Recommended",
            when_needed="Advanced prep — goal-specific sessions",
        )
        for index in range(1, 41)
    ]

    with pytest.raises(ValueError, match="Telegram message capacity"):
        format_equipment_recommendation(items)


@pytest.mark.asyncio
async def test_default_fake_model_supports_both_context_schema_shapes() -> None:
    workflow = create_context_onboarding_workflow(
        Settings(environment="test", llm_mode="mock", llm_model="mock-context")
    )

    validation = await workflow.validate_free_text(
        step=OnboardingStep.AVAILABILITY_INTAKE,
        user_text="Tuesday and Saturday mornings work for me.",
    )
    recommendation = await workflow.recommend_equipment(
        main_goal="Complete a triathlon",
        target_outcome="Finish comfortably",
        event_date=None,
        secondary_priority=None,
    )

    assert validation.outcome == "accepted"
    assert recommendation.outcome == "recommended"
    assert recommendation.recommendation is not None


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
    assert result.error_code is None
    assert "private limitation" not in str(result.model_dump())
