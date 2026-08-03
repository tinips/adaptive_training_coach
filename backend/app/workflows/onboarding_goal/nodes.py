"""Single-purpose node for structured conversational goal extraction."""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Annotated, Literal, cast

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.runnables import Runnable, RunnableConfig, RunnableLambda
from langchain_core.tools import InjectedToolArg, tool
from langgraph.prebuilt import ToolRuntime
from langgraph.types import Command
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    ValidationError,
    field_validator,
)

from app.domain.enums import OnboardingStep
from app.integrations.llm.models import (
    GoalExtractionAction,
    GoalExtractionOutput,
    GoalExtractionPatch,
    LLMConfigurationError,
    LLMProviderError,
    StructuredOnboardingModel,
)
from app.workflows.onboarding_goal.state import GoalExtractionGraphState

GoalExtractionNode = Runnable[
    GoalExtractionGraphState,
    GoalExtractionGraphState,
]
OnboardingModificationNode = Runnable[
    GoalExtractionGraphState,
    GoalExtractionGraphState,
]
_FOUR_DIGIT_YEAR_PATTERN = re.compile(r"(?<![0-9])(?:19|20)[0-9]{2}(?![0-9])")


class UpdateOnboardingSchema(BaseModel):
    """Validated athlete fields available to the generic modification tool."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    main_goal: str | None = Field(
        default=None,
        max_length=500,
        description="The athlete's primary training or event goal.",
    )
    target_outcome: str | None = Field(
        default=None,
        max_length=500,
        description="The result that would make the athlete's goal successful.",
    )
    age: int | None = Field(
        default=None,
        ge=16,
        le=100,
        description="The athlete's current age in complete years, from 16 to 100.",
    )
    birth_year: int | None = Field(
        default=None,
        ge=1940,
        le=2008,
        description="The athlete's corrected four-digit birth year.",
    )
    gender: Literal["MALE", "FEMALE", "OTHER_UNSPECIFIED"] | None = Field(
        default=None,
        description="The athlete's competition category / biological sex.",
    )
    weight_kg: float | None = Field(
        default=None,
        ge=35,
        le=250,
        description="The athlete's current body weight in kilograms.",
    )
    height_cm: int | None = Field(
        default=None,
        ge=120,
        le=230,
        description="The athlete's height in whole centimeters.",
    )
    event_date: str | None = Field(
        default=None,
        min_length=10,
        max_length=10,
        pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$",
        description=(
            "The athlete's explicit event date as an ISO calendar date in "
            "YYYY-MM-DD format. Resolve a month and day without a year to the "
            "next future occurrence relative to today's date."
        ),
    )

    # TODO: Add future restrictions, limitations, and schedules here

    runtime: Annotated[
        ToolRuntime[None, GoalExtractionGraphState] | None,
        InjectedToolArg,
    ] = None

    @field_validator("main_goal", "target_outcome")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split())
        return normalized or None


@tool("update_onboarding_data", args_schema=UpdateOnboardingSchema)
async def update_onboarding_data(
    runtime: ToolRuntime[None, GoalExtractionGraphState],
    **kwargs: object,
) -> Command[Literal["agent"]]:
    """Update only the supplied onboarding fields for the active athlete."""

    state = runtime.state
    clean_payload = {
        key: cast(JsonValue, value)
        for key, value in kwargs.items()
        if value is not None
    }
    updater = state["onboarding_updater"]
    updated = await updater(
        user_id=state["user_id"],
        payload=clean_payload,
    )
    content = json.dumps(
        {
            "updated_fields": updated.updated_fields,
            "updated": True,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return Command(
        update={
            "messages": [
                ToolMessage(
                    content=content,
                    name="update_onboarding_data",
                    tool_call_id=runtime.tool_call_id or "update-onboarding-data",
                )
            ],
            "onboarding_updated": True,
        }
    )


def build_goal_messages(
    *,
    action: GoalExtractionAction,
    user_text: str,
    existing_draft: GoalExtractionOutput | None,
    current_date: str,
) -> list[BaseMessage]:
    """Send the operation, current draft, and latest answer as separate inputs."""

    draft_json = json.dumps(
        existing_draft.model_dump(mode="json") if existing_draft else None,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    system_text = (
        "Extract a field patch from the latest athlete onboarding goal answer. "
        "Return exactly one flat JSON object matching the requested schema and no "
        "prose. The top-level keys must be main_goal, event_date, target_outcome, "
        "secondary_priority, missing_fields, ambiguous_fields, and message_status. "
        "Never nest fields under patch, goal, result, or any other wrapper key. "
        "The only semantic patch fields are main_goal, event_date, target_outcome, "
        "and secondary_priority. Set a semantic field only when the latest user "
        "message explicitly adds or corrects it; otherwise return null. Null means "
        "preserve the current draft value. Never copy unchanged values from the "
        "current draft into the patch. For UPDATE_EXISTING_GOAL, the latest message "
        "may be a short answer to the draft's current missing or ambiguous field. "
        "Interpret that fragment in the context of the current draft: for example, "
        "if target_outcome is missing, 'without stopping' means target_outcome is "
        "'Complete without stopping'; if event_date is missing, '11 July 2027' "
        "supplies event_date. Correct obvious spelling mistakes when the intended "
        "meaning is clear. Use concise English while preserving the user's meaning. "
        "main_goal must be specific enough to "
        "influence training; 'running' or 'train to run' alone is incomplete. "
        "target_outcome states what success means and does not need to be numeric. "
        "An explicitly stated qualitative outcome such as finishing safely, without "
        "stopping, in a good time, or in a decent time is valid. Preserve that "
        "meaning concisely instead of requiring a specific finish time. "
        "secondary_priority is optional, "
        "must be explicitly stated, and must never be listed as missing. Use an "
        "event_date only for a complete, unambiguous calendar date; never invent a "
        "day for a month-only or otherwise ambiguous date. Training goals are "
        "inherently future events. If the athlete provides a calendar date "
        "containing only a month and a day without a year, calculate the correct "
        "calendar year such that the resulting event_date always falls in the "
        "FUTURE relative to today's date. If the athlete explicitly supplies a "
        "year that makes the date past, return event_date as null and mark "
        "event_date ambiguous instead of changing the explicit year. A null "
        "event_date is "
        "valid when the user has no date yet or the goal has no event. List only "
        "genuinely missing or ambiguous fields. Use COMPLETE only when main_goal "
        "and target_outcome are known and the date is known, explicitly unknown, "
        "or not applicable. Use NEEDS_CLARIFICATION otherwise. Use OFF_TOPIC when "
        "the answer is unrelated: in that case return null for every semantic patch "
        "field and do not derive goal facts from it. missing_fields, "
        "ambiguous_fields, and message_status must describe the resulting goal after "
        "the patch is applied to the current draft. "
        f"Today's date is: {current_date}. Operation: {action}. "
        f"Current persisted draft: {draft_json}"
    )
    return [
        SystemMessage(content=system_text),
        HumanMessage(content=user_text),
    ]


def make_extract_goal_node(
    model: StructuredOnboardingModel,
) -> GoalExtractionNode:
    """Build the provider boundary used by the compiled graph."""

    async def extract_goal(
        state: GoalExtractionGraphState,
        config: RunnableConfig,
    ) -> GoalExtractionGraphState:
        action = state["action"]
        if action == "MODIFY_ONBOARDING_DATA":
            return {
                "outcome": "provider_error",
                "error_code": "invalid_workflow_route",
            }
        messages = build_goal_messages(
            action=action,
            user_text=state["user_text"],
            existing_draft=state.get("existing_draft"),
            current_date=state["current_date"],
        )
        try:
            response = await model.ainvoke_structured(
                step=OnboardingStep.GOAL_INTAKE,
                schema=GoalExtractionPatch,
                messages=messages,
                config=config,
            )
        except TimeoutError:
            return {
                "outcome": "provider_error",
                "error_code": "provider_timeout",
            }
        except LLMConfigurationError as exc:
            return {"outcome": "provider_error", "error_code": exc.code}
        except LLMProviderError as exc:
            return {"outcome": "provider_error", "error_code": exc.code}
        except Exception:
            return {
                "outcome": "provider_error",
                "error_code": "provider_failure",
            }

        if response.malformed or response.output is None:
            return {
                "outcome": "fallback_required",
                "error_code": "malformed_structured_output",
                "prompt_tokens": response.prompt_tokens,
                "completion_tokens": response.completion_tokens,
            }
        raw_output = response.output
        if isinstance(raw_output, BaseModel):
            raw_output = raw_output.model_dump(mode="json")
        try:
            goal_patch = GoalExtractionPatch.model_validate(raw_output)
        except (ValidationError, TypeError, ValueError):
            return {
                "outcome": "fallback_required",
                "error_code": "malformed_structured_output",
                "prompt_tokens": response.prompt_tokens,
                "completion_tokens": response.completion_tokens,
            }
        goal_patch = _normalize_event_date(
            goal_patch,
            current_date=state["current_date"],
            user_text=state["user_text"],
        )
        return {
            "outcome": "extracted",
            "goal_patch": goal_patch,
            "error_code": None,
            "prompt_tokens": response.prompt_tokens,
            "completion_tokens": response.completion_tokens,
        }

    return RunnableLambda(extract_goal, name="extract_goal")


def build_onboarding_modification_messages(user_text: str) -> list[BaseMessage]:
    """Build agent context for a completed athlete-data modification."""

    current_date = date.today().isoformat()
    return [
        SystemMessage(
            content=(
                "You manage modifications to an athlete's completed onboarding "
                "data. The supported fields are main goal, target outcome, event "
                "date, age, birth year, gender, weight, and height. Call "
                "update_onboarding_data "
                "once with every supported value explicitly supplied in the latest "
                "request, even when fields belong to different records. Do not call "
                "the tool for an incomplete request such as 'change my goal'; ask a "
                "short clarifying question instead. A main goal must name a concrete "
                "race, distance, discipline, or measurable athletic objective. Vague "
                "phrases such as 'something fast', 'a race', or 'get fitter' are not "
                "valid main goals; ask for a concrete race or distance. Treat the "
                "athlete's newest message as authoritative. If they abandon or "
                "replace a pending request, follow the new request and do not carry "
                "abandoned values into the tool call. Preserve concrete main-goal "
                "wording without embellishment: for example, use 'Ironman', '5k "
                "race', or 'Barcelona Marathon' when that is what the athlete asks "
                "for. Never infer demographic values. Today's date is: "
                f"{current_date}. Training events are future events. For an explicit "
                "month and day without a year, set event_date to the next occurrence "
                "strictly after today and send it as YYYY-MM-DD. If a supplied date "
                "is ambiguous or explicitly in the past, ask for clarification and "
                "do not send event_date. Never claim an update before the tool "
                "succeeds. After the tool result, reply with one concise, friendly "
                "confirmation that states only the fields actually saved. If no "
                "supported change is requested, ask which onboarding field they want "
                "to update."
            )
        ),
        HumanMessage(content=user_text),
    ]


def make_onboarding_modification_agent_node(
    model: StructuredOnboardingModel,
) -> OnboardingModificationNode:
    """Bind the update tool and build one ReAct-style agent turn."""

    tool_model = model.bind_tools([update_onboarding_data])

    async def call_agent(
        state: GoalExtractionGraphState,
        config: RunnableConfig,
    ) -> GoalExtractionGraphState:
        try:
            response = await tool_model.ainvoke(state["messages"], config=config)
        except TimeoutError:
            return {
                "messages": [AIMessage(content="")],
                "outcome": "provider_error",
                "error_code": "provider_timeout",
            }
        except LLMConfigurationError as exc:
            return {
                "messages": [AIMessage(content="")],
                "outcome": "provider_error",
                "error_code": exc.code,
            }
        except LLMProviderError as exc:
            return {
                "messages": [AIMessage(content="")],
                "outcome": "provider_error",
                "error_code": exc.code,
            }
        except Exception:
            return {
                "messages": [AIMessage(content="")],
                "outcome": "provider_error",
                "error_code": "provider_failure",
            }

        update: GoalExtractionGraphState = {"messages": [response]}
        if not response.tool_calls:
            confirmation = _message_text(response)
            update["confirmation"] = confirmation
            update["outcome"] = (
                "onboarding_modified"
                if state.get("onboarding_updated")
                else "no_onboarding_update"
            )
        return update

    return RunnableLambda(call_agent, name="onboarding_modification_agent")


def _message_text(message: AIMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content.strip()
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and isinstance(block.get("text"), str):
            parts.append(block["text"])
    return " ".join(parts).strip()


def _normalize_event_date(
    patch: GoalExtractionPatch,
    *,
    current_date: str,
    user_text: str,
) -> GoalExtractionPatch:
    """Resolve yearless dates forward and reject explicitly nonfuture dates."""

    if patch.event_date is None:
        return patch
    try:
        anchor = date.fromisoformat(current_date)
    except ValueError:
        return patch
    if patch.event_date > anchor:
        return patch
    if _FOUR_DIGIT_YEAR_PATTERN.search(user_text) is None:
        candidate = patch.event_date
        next_year = candidate.year + 1
        while candidate <= anchor:
            try:
                candidate = candidate.replace(year=next_year)
            except ValueError:
                next_year += 1
                continue
            next_year += 1
        return patch.model_copy(update={"event_date": candidate})
    ambiguous = list(dict.fromkeys([*patch.ambiguous_fields, "event_date"]))
    missing = [field for field in patch.missing_fields if field != "event_date"]
    return patch.model_copy(
        update={
            "event_date": None,
            "missing_fields": missing,
            "ambiguous_fields": ambiguous,
            "message_status": "NEEDS_CLARIFICATION",
        }
    )
