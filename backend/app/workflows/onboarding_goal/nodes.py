"""Single-purpose node for structured conversational goal extraction."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import Runnable, RunnableConfig, RunnableLambda
from pydantic import BaseModel, ValidationError

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


def build_goal_messages(
    *,
    action: GoalExtractionAction,
    user_text: str,
    existing_draft: GoalExtractionOutput | None,
) -> list[BaseMessage]:
    """Send the operation, current draft, and latest answer as separate inputs."""

    draft_json = json.dumps(
        existing_draft.model_dump(mode="json") if existing_draft else None,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    today = datetime.now(UTC).date().isoformat()
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
        "day for a month-only or otherwise ambiguous date. A null event_date is "
        "valid when the user has no date yet or the goal has no event. List only "
        "genuinely missing or ambiguous fields. Use COMPLETE only when main_goal "
        "and target_outcome are known and the date is known, explicitly unknown, "
        "or not applicable. Use NEEDS_CLARIFICATION otherwise. Use OFF_TOPIC when "
        "the answer is unrelated: in that case return null for every semantic patch "
        "field and do not derive goal facts from it. missing_fields, "
        "ambiguous_fields, and message_status must describe the resulting goal after "
        "the patch is applied to the current draft. "
        f"Today's UTC date is {today}. Operation: {action}. "
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
        messages = build_goal_messages(
            action=state["action"],
            user_text=state["user_text"],
            existing_draft=state.get("existing_draft"),
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
        return {
            "outcome": "extracted",
            "goal_patch": goal_patch,
            "error_code": None,
            "prompt_tokens": response.prompt_tokens,
            "completion_tokens": response.completion_tokens,
        }

    return RunnableLambda(extract_goal, name="extract_goal")
