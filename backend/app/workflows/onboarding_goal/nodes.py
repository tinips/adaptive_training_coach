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
from app.workflows.prompts.onboarding import (
    explicit_onboarding_change_tool_policy,
    future_event_date_policy,
    render_goal_extraction_system_prompt,
)

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
        description=future_event_date_policy("schema_description"),
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
    system_text = render_goal_extraction_system_prompt(
        action=action,
        current_date=current_date,
        draft_json=draft_json,
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
    change_policy = explicit_onboarding_change_tool_policy(
        "onboarding_modification",
        tool_name="update_onboarding_data",
        supported_fields=(
            "main goal, target outcome, event date, age, birth year, gender, "
            "weight, and height"
        ),
    )
    return [
        SystemMessage(
            content=(
                f"{change_policy}Today's date is: "
                f"{current_date}. "
                f"{future_event_date_policy('onboarding_modification')}"
                "Never claim an update before the tool "
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
