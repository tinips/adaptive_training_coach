"""Small independently testable nodes for the onboarding-text graph."""

from __future__ import annotations

import json
from collections.abc import Mapping

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import Runnable, RunnableConfig, RunnableLambda
from pydantic import BaseModel, ValidationError

from app.domain.enums import OnboardingStep
from app.integrations.llm.models import (
    LLMConfigurationError,
    LLMProviderError,
    StructuredOnboardingModel,
    structured_output_schema_for_step,
)
from app.schemas.onboarding import OnboardingParseResult
from app.workflows.onboarding_text.state import OnboardingTextGraphState

NodeResult = OnboardingTextGraphState
AsyncGraphNode = Runnable[OnboardingTextGraphState, OnboardingTextGraphState]

_CONTEXT_KEYS: dict[OnboardingStep, tuple[str, ...]] = {
    OnboardingStep.GOAL_TYPE: ("primary_sport",),
    OnboardingStep.EVENT_NAME: ("primary_sport", "goal_type"),
    OnboardingStep.GOAL_PRIORITY: (
        "primary_sport",
        "goal_type",
        "event_status",
    ),
    OnboardingStep.EQUIPMENT: ("primary_sport",),
    OnboardingStep.POOL_ACCESS: ("primary_sport",),
    OnboardingStep.BIKE_ACCESS: ("primary_sport",),
    OnboardingStep.HEALTH_AREAS: ("primary_sport",),
    OnboardingStep.HEALTH_TIMING: ("health_areas",),
    OnboardingStep.HEALTH_DESCRIPTION: ("health_areas", "health_timing"),
}

_NORMALIZATION_RULES: dict[OnboardingStep, str] = {
    OnboardingStep.PRIMARY_SPORT: (
        "Normalize to RUNNING, CYCLING, TRIATHLON, SWIMMING, GENERAL_FITNESS, or OTHER."
    ),
    OnboardingStep.GOAL_TYPE: (
        "Normalize a recognized goal to its concise uppercase English code; "
        "otherwise use OTHER. Never invent a target time."
    ),
    OnboardingStep.GOAL_PRIORITY: (
        "Normalize to FINISH_SAFELY, PERSONAL_BEST, TARGET_TIME, "
        "HEALTH_CONSISTENCY, or OTHER."
    ),
    OnboardingStep.EQUIPMENT: (
        "Return a list using only the configured uppercase English equipment "
        "codes; use OTHER for an unlisted item."
    ),
    OnboardingStep.HEALTH_AREAS: (
        "Return body-area codes only. Do not infer or name a diagnosis."
    ),
    OnboardingStep.HEALTH_DESCRIPTION: (
        "Preserve the user's stated limitation without diagnosing it."
    ),
}


def build_messages(
    *,
    step: OnboardingStep,
    user_text: str,
    confirmed_context: Mapping[str, object],
) -> list[BaseMessage]:
    """Build a minimal one-turn prompt with only step-relevant context."""

    allowed_keys = _CONTEXT_KEYS.get(step, ())
    safe_context = {
        key: confirmed_context[key] for key in allowed_keys if key in confirmed_context
    }
    context_json = json.dumps(
        safe_context,
        ensure_ascii=False,
        default=str,
        separators=(",", ":"),
    )
    rule = _NORMALIZATION_RULES.get(
        step,
        "Preserve names and personal descriptions unless normalization is needed.",
    )
    system_text = (
        "Interpret exactly one onboarding answer and return only the requested "
        "structured schema. Accept any input language, but use English enum "
        "codes and an English clarification question. Do not invent facts or "
        f"provide medical diagnosis. {rule} Relevant confirmed context: "
        f"{context_json}"
    )
    return [
        SystemMessage(content=system_text),
        HumanMessage(content=user_text),
    ]


def make_parse_with_model_node(
    model: StructuredOnboardingModel,
) -> AsyncGraphNode:
    """Create the provider node while keeping setup outside graph topology."""

    async def parse_with_model(
        state: OnboardingTextGraphState,
        config: RunnableConfig,
    ) -> NodeResult:
        step = state["onboarding_step"]
        schema = structured_output_schema_for_step(step)
        messages = build_messages(
            step=step,
            user_text=state["user_text"],
            confirmed_context=state.get("confirmed_context", {}),
        )
        try:
            response = await model.ainvoke_structured(
                step=step,
                schema=schema,
                messages=messages,
                config=config,
            )
        except TimeoutError:
            return {
                "outcome": "provider_error",
                "error_code": "provider_timeout",
            }
        except LLMConfigurationError as exc:
            return {
                "outcome": "provider_error",
                "error_code": exc.code,
            }
        except LLMProviderError as exc:
            return {
                "outcome": "provider_error",
                "error_code": exc.code,
            }
        except Exception:
            # This is the provider safety boundary. Exception messages are not
            # retained or exposed because they can contain request details.
            return {
                "outcome": "provider_error",
                "error_code": "provider_failure",
            }
        return {
            "model_output": response.output,
            "model_malformed": response.malformed,
            "prompt_tokens": response.prompt_tokens,
            "completion_tokens": response.completion_tokens,
            "error_code": None,
        }

    return RunnableLambda(parse_with_model, name="parse_with_model")


async def validate_structured_output(
    state: OnboardingTextGraphState,
    config: RunnableConfig,
) -> NodeResult:
    """Revalidate model output at the application boundary with Pydantic."""

    del config
    if state.get("outcome") == "provider_error":
        return {}
    if state.get("model_malformed", False):
        return {
            "outcome": "fallback_required",
            "error_code": "malformed_structured_output",
        }
    raw_output = state.get("model_output")
    if raw_output is None:
        return {
            "outcome": "fallback_required",
            "error_code": "missing_structured_output",
        }
    if isinstance(raw_output, BaseModel):
        raw_output = raw_output.model_dump(mode="json")
    schema = structured_output_schema_for_step(state["onboarding_step"])
    try:
        step_output = schema.model_validate(raw_output)
        parse_result = OnboardingParseResult.model_validate(
            step_output.model_dump(mode="json")
        )
    except (ValidationError, TypeError, ValueError):
        return {
            "outcome": "fallback_required",
            "error_code": "malformed_structured_output",
        }
    return {
        "parse_result": parse_result,
        "error_code": None,
    }


def make_route_result_node(
    *,
    min_confidence: float,
) -> AsyncGraphNode:
    """Create the deterministic outcome-selection node."""

    async def route_result(
        state: OnboardingTextGraphState,
        config: RunnableConfig,
    ) -> NodeResult:
        del config
        existing_outcome = state.get("outcome")
        if existing_outcome in {"provider_error", "fallback_required"}:
            return {"outcome": existing_outcome}
        result = state.get("parse_result")
        if result is None:
            return {
                "outcome": "fallback_required",
                "error_code": "missing_parse_result",
            }
        if result.safety_flag:
            return {
                "outcome": "fallback_required",
                "error_code": "safety_flagged",
            }
        if result.requires_clarification:
            return {
                "outcome": "clarification_required",
                "error_code": "clarification_requested",
            }
        if result.normalized_value is None or not result.display_value:
            return {
                "outcome": "clarification_required",
                "error_code": "incomplete_parse",
            }
        if result.confidence < min_confidence:
            return {
                "outcome": "clarification_required",
                "error_code": "low_confidence",
            }
        return {
            "outcome": "confirmation_required",
            "error_code": None,
        }

    return RunnableLambda(route_result, name="route_result")


async def confirmation_required(
    state: OnboardingTextGraphState,
    config: RunnableConfig,
) -> NodeResult:
    del state, config
    return {"outcome": "confirmation_required"}


async def clarification_required(
    state: OnboardingTextGraphState,
    config: RunnableConfig,
) -> NodeResult:
    del state, config
    return {"outcome": "clarification_required"}


async def fallback_required(
    state: OnboardingTextGraphState,
    config: RunnableConfig,
) -> NodeResult:
    del state, config
    return {"outcome": "fallback_required"}


async def provider_error(
    state: OnboardingTextGraphState,
    config: RunnableConfig,
) -> NodeResult:
    del state, config
    return {"outcome": "provider_error"}
