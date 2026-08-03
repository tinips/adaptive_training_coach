"""Focused tests for the compiled conversational goal extraction graph."""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4

import pytest
from langchain_core.messages import BaseMessage
from langchain_core.runnables import RunnableConfig

from app.domain.enums import OnboardingStep
from app.integrations.llm.models import (
    GoalExtractionOutput,
    StructuredModelResponse,
    StructuredOutputSchema,
)
from app.observability.protocol import ProviderMode
from app.workflows.onboarding_goal.graph import build_goal_extraction_graph


@dataclass
class StructuredGoalModel:
    response: StructuredModelResponse
    schemas: list[StructuredOutputSchema] = field(default_factory=list)
    messages: list[list[BaseMessage]] = field(default_factory=list)

    @property
    def provider_mode(self) -> ProviderMode:
        return "mock"

    @property
    def model_name(self) -> str:
        return "structured-goal-test"

    async def ainvoke_structured(
        self,
        *,
        step: OnboardingStep,
        schema: StructuredOutputSchema,
        messages: list[BaseMessage],
        config: RunnableConfig,
    ) -> StructuredModelResponse:
        del config
        assert step is OnboardingStep.GOAL_INTAKE
        self.schemas.append(schema)
        self.messages.append(messages)
        return self.response


@pytest.mark.asyncio
async def test_compiled_goal_graph_requests_and_revalidates_narrow_schema() -> None:
    output = GoalExtractionOutput(
        main_goal="Complete a marathon",
        event_date=None,
        target_outcome="Finish safely",
        secondary_priority=None,
        missing_fields=[],
        ambiguous_fields=[],
        message_status="COMPLETE",
    )
    model = StructuredGoalModel(StructuredModelResponse(output=output))
    graph = build_goal_extraction_graph(model=model)

    result = await graph.ainvoke(
        {
            "user_id": uuid4(),
            "user_text": "I want to complete a marathon safely.",
            "existing_draft": None,
        }
    )

    assert result["outcome"] == "extracted"
    assert result["goal_draft"] == output
    assert model.schemas == [GoalExtractionOutput]
    assert "existing draft: null" in str(model.messages[0][0].content).casefold()


@pytest.mark.asyncio
async def test_compiled_goal_graph_rejects_malformed_structured_result() -> None:
    model = StructuredGoalModel(
        StructuredModelResponse(
            output={
                "main_goal": "Complete a marathon",
                "event_date": "next March",
                "target_outcome": "Finish",
                "secondary_priority": None,
                "missing_fields": [],
                "ambiguous_fields": [],
                "message_status": "COMPLETE",
            }
        )
    )
    graph = build_goal_extraction_graph(model=model)

    result = await graph.ainvoke(
        {
            "user_id": uuid4(),
            "user_text": "The race is next March.",
            "existing_draft": None,
        }
    )

    assert result["outcome"] == "fallback_required"
    assert result["error_code"] == "malformed_structured_output"
