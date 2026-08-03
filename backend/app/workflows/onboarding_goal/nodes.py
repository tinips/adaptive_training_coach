"""Single-purpose node for structured conversational goal extraction."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import Runnable, RunnableConfig, RunnableLambda
from pydantic import BaseModel, ValidationError

from app.domain.enums import OnboardingStep
from app.integrations.llm.models import (
    GoalExtractionOutput,
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
    user_text: str,
    existing_draft: GoalExtractionOutput | None,
) -> list[BaseMessage]:
    """Send only the current answer and the safe accumulated draft."""

    draft_json = json.dumps(
        existing_draft.model_dump(mode="json") if existing_draft else None,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    today = datetime.now(UTC).date().isoformat()
    system_text = (
        "Extract and merge one athlete onboarding goal answer. Return exactly one "
        "JSON object matching the requested schema and no prose. The only semantic "
        "goal fields are main_goal, event_date, target_outcome, and "
        "secondary_priority. Use concise English while preserving the user's "
        "meaning. Preserve valid values from the existing draft unless the new "
        "answer explicitly changes them. main_goal must be specific enough to "
        "influence training; 'running' or 'train to run' alone is incomplete. "
        "target_outcome states what success means. secondary_priority is optional, "
        "must be explicitly stated, and must never be listed as missing. Use an "
        "event_date only for a complete, unambiguous calendar date; never invent a "
        "day for a month-only or otherwise ambiguous date. A null event_date is "
        "valid when the user has no date yet or the goal has no event. List only "
        "genuinely missing or ambiguous fields. Use COMPLETE only when main_goal "
        "and target_outcome are known and the date is known, explicitly unknown, "
        "or not applicable. Use NEEDS_CLARIFICATION otherwise. Use OFF_TOPIC when "
        "the answer is unrelated and do not derive goal facts from it. "
        f"Today's UTC date is {today}. Existing draft: {draft_json}"
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
            user_text=state["user_text"],
            existing_draft=state.get("existing_draft"),
        )
        try:
            response = await model.ainvoke_structured(
                step=OnboardingStep.GOAL_INTAKE,
                schema=GoalExtractionOutput,
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
            goal_draft = GoalExtractionOutput.model_validate(raw_output)
        except (ValidationError, TypeError, ValueError):
            return {
                "outcome": "fallback_required",
                "error_code": "malformed_structured_output",
                "prompt_tokens": response.prompt_tokens,
                "completion_tokens": response.completion_tokens,
            }
        return {
            "outcome": "extracted",
            "goal_draft": goal_draft,
            "error_code": None,
            "prompt_tokens": response.prompt_tokens,
            "completion_tokens": response.completion_tokens,
        }

    return RunnableLambda(extract_goal, name="extract_goal")
