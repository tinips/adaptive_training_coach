"""Focused tests for the compiled conversational goal extraction graph."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.runnables import Runnable, RunnableConfig, RunnableLambda
from langchain_core.tools import BaseTool

from app.domain.enums import OnboardingStep
from app.integrations.llm.models import (
    GoalExtractionOutput,
    GoalExtractionPatch,
    StructuredModelResponse,
    StructuredOutputSchema,
)
from app.observability.protocol import ProviderMode
from app.schemas.onboarding_goal import UpdatedOnboardingData
from app.workflows.onboarding_goal.graph import build_goal_extraction_graph
from app.workflows.onboarding_goal.nodes import (
    UpdateOnboardingSchema,
    build_goal_messages,
    build_onboarding_modification_messages,
    update_onboarding_data,
)
from app.workflows.prompts.onboarding import (
    GOAL_EXTRACTION_CONTRACT,
    GOAL_EXTRACTION_CONTRACT_VERSION,
    explicit_onboarding_change_tool_policy,
    future_event_date_policy,
)


@dataclass
class StructuredGoalModel:
    response: StructuredModelResponse
    schemas: list[StructuredOutputSchema] = field(default_factory=list)
    messages: list[list[BaseMessage]] = field(default_factory=list)
    agent_responses: list[AIMessage] = field(default_factory=list)
    bound_tool_names: list[str] = field(default_factory=list)

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

    def bind_tools(
        self,
        tools: Sequence[BaseTool],
    ) -> Runnable[Any, AIMessage]:
        self.bound_tool_names.extend(item.name for item in tools)

        async def respond(
            messages: list[BaseMessage],
            config: RunnableConfig,
        ) -> AIMessage:
            del config
            return self.agent_responses.pop(0)

        return RunnableLambda(respond)


def test_update_onboarding_schema_is_sparse_described_and_runtime_hidden() -> None:
    validated = UpdateOnboardingSchema()
    assert validated.model_dump(exclude={"runtime"}) == {
        "main_goal": None,
        "target_outcome": None,
        "age": None,
        "birth_year": None,
        "gender": None,
        "weight_kg": None,
        "height_cm": None,
        "event_date": None,
        "availability_text": None,
        "equipment_text": None,
        "health_limitations_text": None,
    }

    schema = update_onboarding_data.tool_call_schema.model_json_schema()
    properties = schema["properties"]
    assert set(properties) == {
        "main_goal",
        "target_outcome",
        "age",
        "birth_year",
        "gender",
        "weight_kg",
        "height_cm",
        "event_date",
        "availability_text",
        "equipment_text",
        "health_limitations_text",
    }
    assert "required" not in schema
    assert all(properties[field].get("description") for field in properties)
    assert properties["event_date"]["description"] == (
        "The athlete's explicit event date as an ISO calendar date in "
        "YYYY-MM-DD format. Resolve a month and day without a year to the "
        "next future occurrence relative to today's date."
    )


def test_update_onboarding_schema_preserves_raw_context_values() -> None:
    availability = "Tuesday & Thursday: 45 min; Sunday: 2 h"
    equipment = "Road bike only — no indoor trainer"
    limitations = "Mild knee discomfort after downhill running"

    validated = UpdateOnboardingSchema(
        availability_text=availability,
        equipment_text=equipment,
        health_limitations_text=limitations,
    )

    assert validated.availability_text == availability
    assert validated.equipment_text == equipment
    assert validated.health_limitations_text == limitations


def test_goal_extraction_prompt_uses_versioned_static_contract() -> None:
    messages = build_goal_messages(
        action="UPDATE_EXISTING_GOAL",
        user_text="without stopping",
        existing_draft=None,
        current_date="2026-08-03",
    )

    assert GOAL_EXTRACTION_CONTRACT_VERSION == "2"
    assert "Today's date is" not in GOAL_EXTRACTION_CONTRACT
    assert "Current persisted draft" not in GOAL_EXTRACTION_CONTRACT
    assert str(messages[0].content) == (
        f"{GOAL_EXTRACTION_CONTRACT}Today's date is: 2026-08-03. "
        "Operation: UPDATE_EXISTING_GOAL. Current persisted draft: null"
    )
    assert str(messages[1].content) == "without stopping"


def test_goal_extraction_contract_keeps_json_fields_and_all_statuses() -> None:
    for required_fragment in (
        "Return exactly one flat JSON object matching the requested schema and no "
        "prose.",
        "main_goal, event_date, target_outcome, secondary_priority, "
        "missing_fields, ambiguous_fields, and message_status.",
        "Use COMPLETE only when main_goal and target_outcome are known",
        "target_outcome is 'Finish in a decent time' and secondary_priority is "
        "'Maintain muscle'.",
        "Use NEEDS_CLARIFICATION otherwise.",
        "Use OFF_TOPIC when the answer is unrelated:",
    ):
        assert required_fragment in GOAL_EXTRACTION_CONTRACT


def test_future_event_date_policy_preserves_each_legacy_consumer_wording() -> None:
    assert future_event_date_policy("goal_extraction") == (
        "Use an event_date only for a complete, unambiguous calendar date; "
        "never invent a day for a month-only or otherwise ambiguous date. "
        "Training goals are inherently future events. If the athlete provides a "
        "calendar date containing only a month and a day without a year, calculate "
        "the correct calendar year such that the resulting event_date always falls "
        "in the FUTURE relative to today's date. If the athlete explicitly supplies "
        "a year that makes the date past, return event_date as null and mark "
        "event_date ambiguous instead of changing the explicit year. "
    )
    assert future_event_date_policy("onboarding_modification") == (
        "Training events are future events. For an explicit month and day without "
        "a year, set event_date to the next occurrence strictly after today and "
        "send it as YYYY-MM-DD. If a supplied date is ambiguous or explicitly in "
        "the past, ask for clarification and do not send event_date. "
    )


def test_explicit_onboarding_change_policy_preserves_legacy_tool_wording() -> None:
    assert explicit_onboarding_change_tool_policy(
        "telegram_orchestrator",
        tool_name="update_onboarding_data",
        supported_fields="goal, weight, age, height, event_date",
    ) == (
        "1. DATA CORRECTIONS: If the user explicitly wants to change, update, "
        "correct, or replace an athlete field (goal, weight, age, height, "
        "event_date), you MUST call 'update_onboarding_data'. This rule overrides "
        "any active question.\n"
    )
    assert explicit_onboarding_change_tool_policy(
        "onboarding_modification",
        tool_name="update_onboarding_data",
        supported_fields=(
            "main goal, target outcome, event date, age, birth year, gender, "
            "weight, and height"
        ),
    ) == (
        "You manage modifications to an athlete's completed onboarding data. The "
        "supported fields are main goal, target outcome, event date, age, birth "
        "year, gender, weight, and height. Call update_onboarding_data once with "
        "every supported value explicitly supplied in the latest request, even when "
        "fields belong to different records. Do not call the tool for an incomplete "
        "request such as 'change my goal'; ask a short clarifying question instead. "
        "A main goal must name a concrete race, distance, discipline, or measurable "
        "athletic objective. Vague phrases such as 'something fast', 'a race', or "
        "'get fitter' are not valid main goals; ask for a concrete race or distance. "
        "Treat the athlete's newest message as authoritative. If they abandon or "
        "replace a pending request, follow the new request and do not carry "
        "abandoned values into the tool call. Preserve concrete main-goal wording "
        "without embellishment: for example, use 'Ironman', '5k race', or "
        "'Barcelona Marathon' when that is what the athlete asks for. Never infer "
        "demographic values. "
    )


def test_onboarding_modification_prompt_supports_private_raw_context_updates() -> None:
    prompt = str(
        build_onboarding_modification_messages("My knee is sore after hills.")[
            0
        ].content
    )

    assert (
        "Availability, equipment, and training limitations can each be updated "
        "independently."
    ) in prompt
    assert "copy only the relevant user-supplied value" in prompt
    assert "equipment_text='ALL_RECOMMENDED'" in prompt
    assert "health_limitations_text='NONE_REPORTED'" in prompt
    assert (
        "never quote, restate, summarise, or otherwise expose their content"
    ) in prompt


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
async def test_compiled_goal_graph_preserves_off_topic_structured_result() -> None:
    output = GoalExtractionPatch(
        main_goal=None,
        event_date=None,
        target_outcome=None,
        secondary_priority=None,
        missing_fields=[],
        ambiguous_fields=[],
        message_status="OFF_TOPIC",
    )
    model = StructuredGoalModel(StructuredModelResponse(output=output))
    graph = build_goal_extraction_graph(model=model)

    result = await graph.ainvoke(
        {
            "user_id": uuid4(),
            "action": "CREATE_GOAL",
            "user_text": "What shoes should I buy?",
            "existing_draft": None,
            "current_date": "2026-08-03",
        }
    )

    assert result["outcome"] == "extracted"
    assert result["goal_patch"] == output


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


@pytest.mark.asyncio
async def test_onboarding_modification_calls_tool_updates_state_and_confirms() -> None:
    user_id = uuid4()
    updater = AsyncMock(
        return_value=UpdatedOnboardingData(
            updated_fields={
                "main_goal": "Finish an Ironman 70.3",
                "target_outcome": "Finish in a decent time",
            }
        )
    )
    model = StructuredGoalModel(
        response=StructuredModelResponse(output=None),
        agent_responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "update_onboarding_data",
                        "args": {
                            "main_goal": "Finish an Ironman 70.3",
                            "target_outcome": "Finish in a decent time",
                            "age": None,
                        },
                        "id": "update-goal-1",
                        "type": "tool_call",
                    }
                ],
            ),
        ],
    )
    graph = build_goal_extraction_graph(model=model)

    result = await graph.ainvoke(
        {
            "user_id": user_id,
            "action": "MODIFY_ONBOARDING_DATA",
            "user_text": ("change my goal to finish my ironman 70.3 in a decent time"),
            "messages": build_onboarding_modification_messages(
                "change my goal to finish my ironman 70.3 in a decent time"
            ),
            "onboarding_updater": updater,
            "onboarding_updated": False,
        }
    )

    assert model.bound_tool_names == ["update_onboarding_data"]
    updater.assert_awaited_once_with(
        user_id=user_id,
        payload={
            "main_goal": "Finish an Ironman 70.3",
            "target_outcome": "Finish in a decent time",
        },
    )
    assert result["onboarding_updated"] is True
    assert result["outcome"] == "onboarding_modified"
    assert result["updated_fields"] == ["main_goal", "target_outcome"]
