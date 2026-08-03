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
    GoalExtractionPatch,
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
    output = GoalExtractionPatch(
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
            "action": "CREATE_GOAL",
            "user_text": "I want to complete a marathon safely.",
            "existing_draft": None,
            "current_date": "2026-08-03",
        }
    )

    assert result["outcome"] == "extracted"
    assert result["goal_patch"] == output
    assert model.schemas == [GoalExtractionPatch]
    prompt = str(model.messages[0][0].content).casefold()
    assert "operation: create_goal" in prompt
    assert "current persisted draft: null" in prompt
    assert "one flat json object" in prompt
    assert "never nest fields under patch" in prompt
    assert "does not need to be numeric" in prompt
    assert "today's date is: 2026-08-03" in prompt
    assert str(model.messages[0][1].content) == (
        "I want to complete a marathon safely."
    )


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
            "action": "CREATE_GOAL",
            "user_text": "The race is next March.",
            "existing_draft": None,
            "current_date": "2026-08-03",
        }
    )

    assert result["outcome"] == "fallback_required"
    assert result["error_code"] == "malformed_structured_output"


@pytest.mark.asyncio
async def test_update_prompt_frames_short_reply_with_current_missing_field() -> None:
    output = GoalExtractionPatch(
        main_goal=None,
        event_date=None,
        target_outcome="Complete without stopping",
        secondary_priority=None,
        missing_fields=["event_date"],
        ambiguous_fields=[],
        message_status="NEEDS_CLARIFICATION",
    )
    model = StructuredGoalModel(StructuredModelResponse(output=output))
    graph = build_goal_extraction_graph(model=model)
    current = GoalExtractionOutput(
        main_goal="Run a marathon",
        event_date=None,
        target_outcome=None,
        secondary_priority=None,
        missing_fields=["target_outcome"],
        ambiguous_fields=[],
        message_status="NEEDS_CLARIFICATION",
    )

    result = await graph.ainvoke(
        {
            "user_id": uuid4(),
            "action": "UPDATE_EXISTING_GOAL",
            "user_text": "wihtout stopping",
            "existing_draft": current,
            "current_date": "2026-08-03",
        }
    )

    assert result["goal_patch"] == output
    prompt = str(model.messages[0][0].content).casefold()
    assert "short answer to the draft's current missing" in prompt
    assert '"missing_fields":["target_outcome"]' in prompt
    assert str(model.messages[0][1].content) == "wihtout stopping"


@pytest.mark.asyncio
async def test_month_and_day_prompt_requires_the_next_future_calendar_date() -> None:
    output = GoalExtractionPatch(
        main_goal=None,
        event_date="2026-07-11",
        target_outcome=None,
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
            "action": "UPDATE_EXISTING_GOAL",
            "user_text": "11 July",
            "existing_draft": None,
            "current_date": "2026-08-03",
        }
    )

    patch = result["goal_patch"]
    assert patch is not None
    assert patch.event_date is not None
    assert patch.event_date.isoformat() == "2027-07-11"
    prompt = str(model.messages[0][0].content)
    assert "Today's date is: 2026-08-03" in prompt
    assert "only a month and a day without a year" in prompt
    assert "always falls in the FUTURE" in prompt


@pytest.mark.asyncio
async def test_explicit_past_event_date_is_safely_returned_for_clarification() -> None:
    output = GoalExtractionPatch(
        main_goal=None,
        event_date="2025-07-11",
        target_outcome=None,
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
            "action": "UPDATE_EXISTING_GOAL",
            "user_text": "11 July 2025",
            "existing_draft": None,
            "current_date": "2026-08-03",
        }
    )

    patch = result["goal_patch"]
    assert patch is not None
    assert patch.event_date is None
    assert patch.ambiguous_fields == ["event_date"]
    assert patch.message_status == "NEEDS_CLARIFICATION"


@pytest.mark.asyncio
async def test_ambiguous_date_remains_null_without_an_unhandled_error() -> None:
    output = GoalExtractionPatch(
        main_goal=None,
        event_date=None,
        target_outcome=None,
        secondary_priority=None,
        missing_fields=[],
        ambiguous_fields=["event_date"],
        message_status="NEEDS_CLARIFICATION",
    )
    model = StructuredGoalModel(StructuredModelResponse(output=output))
    graph = build_goal_extraction_graph(model=model)

    result = await graph.ainvoke(
        {
            "user_id": uuid4(),
            "action": "UPDATE_EXISTING_GOAL",
            "user_text": "sometime in July",
            "existing_draft": None,
            "current_date": "2026-08-03",
        }
    )

    assert result["outcome"] == "extracted"
    assert result["goal_patch"] == output
