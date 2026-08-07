"""LangGraph nodes for stateless onboarding-context model calls."""

from __future__ import annotations

import re

from langchain_core.messages import BaseMessage, SystemMessage
from langchain_core.runnables import Runnable, RunnableConfig, RunnableLambda
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.domain.enums import OnboardingStep
from app.integrations.llm.models import (
    LLMConfigurationError,
    LLMProviderError,
    StructuredOnboardingModel,
)
from app.schemas.onboarding_context import EquipmentRecommendationGoalContext
from app.workflows.onboarding_context.prompts import (
    is_supported_free_text_step,
    render_equipment_recommendation_system_prompt,
)
from app.workflows.onboarding_context.state import (
    EquipmentRecommendationGraphState,
    FreeTextValidationGraphState,
)

FreeTextValidationNode = Runnable[
    FreeTextValidationGraphState,
    FreeTextValidationGraphState,
]
EquipmentRecommendationNode = Runnable[
    EquipmentRecommendationGraphState,
    EquipmentRecommendationGraphState,
]
_RECOMMENDATION_SENTENCE_BREAK = re.compile(r"[.!?]+")
_RECOMMENDATION_LIST_ITEM = re.compile(r"^(?:[-*•]|[0-9]+[.)])\s+")
_PROHIBITED_RECOMMENDATION_CONTENT = re.compile(
    r"\b(?:"
    r"(?:training|workout|exercise|weekly)\s+plan|"
    r"diagnos(?:is|e)|"
    r"medical\s+(?:advice|condition|treatment)|"
    r"prescri(?:be|ption)|"
    r"treat(?:ment|ing)\b"
    r")",
    re.IGNORECASE,
)


class EquipmentRecommendationOutput(BaseModel):
    """Short model-generated equipment suggestion for a confirmed goal."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    recommendation: str = Field(min_length=1, max_length=700)

    @field_validator("recommendation")
    @classmethod
    def normalize_recommendation(cls, value: str) -> str:
        nonempty_lines = [line.strip() for line in value.splitlines() if line.strip()]
        list_item_count = sum(
            1 for line in nonempty_lines if _RECOMMENDATION_LIST_ITEM.match(line)
        )
        if list_item_count > 5:
            raise ValueError("recommendation must contain at most five items")
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("recommendation must not be blank")
        sentence_count = len(
            [
                sentence
                for sentence in _RECOMMENDATION_SENTENCE_BREAK.split(normalized)
                if sentence.strip()
            ]
        )
        if sentence_count > 5:
            raise ValueError("recommendation must contain at most five sentences")
        if _PROHIBITED_RECOMMENDATION_CONTENT.search(normalized) is not None:
            raise ValueError("recommendation must not contain plan or medical content")
        return normalized


def build_equipment_recommendation_messages(
    *,
    goal_context: EquipmentRecommendationGoalContext,
) -> list[BaseMessage]:
    """Build a recommendation request from goal data only."""

    return [
        SystemMessage(
            content=render_equipment_recommendation_system_prompt(
                goal_context=goal_context,
            )
        )
    ]


def make_free_text_validation_node(
    model: StructuredOnboardingModel,
) -> FreeTextValidationNode:
    """Accept every non-empty raw context answer without an LLM judgement."""

    del model

    async def validate(
        state: FreeTextValidationGraphState,
        config: RunnableConfig,
    ) -> FreeTextValidationGraphState:
        step = state["step"]
        user_text = state["user_text"]
        if not is_supported_free_text_step(step):
            return {
                "outcome": "provider_error",
                "error_code": "unsupported_context_step",
            }
        if not user_text.strip():
            return {
                "outcome": "retry_required",
                "error_code": "empty_text",
            }
        return {
            "outcome": "accepted",
            "error_code": None,
        }

    return RunnableLambda(validate, name="validate_onboarding_context_text")


def make_equipment_recommendation_node(
    model: StructuredOnboardingModel,
) -> EquipmentRecommendationNode:
    """Build the structured recommendation boundary for canonical goal fields."""

    async def recommend(
        state: EquipmentRecommendationGraphState,
        config: RunnableConfig,
    ) -> EquipmentRecommendationGraphState:
        goal_context = state["goal_context"]
        if goal_context.main_goal is None:
            return {
                "outcome": "retry_required",
                "error_code": "missing_goal_context",
            }
        try:
            response = await model.ainvoke_structured(
                step=_equipment_recommendation_step(),
                schema=EquipmentRecommendationOutput,
                messages=build_equipment_recommendation_messages(
                    goal_context=goal_context,
                ),
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
                "outcome": "retry_required",
                "error_code": "malformed_structured_output",
                "prompt_tokens": response.prompt_tokens,
                "completion_tokens": response.completion_tokens,
            }
        raw_output = response.output
        if isinstance(raw_output, BaseModel):
            raw_output = raw_output.model_dump(mode="json")
        try:
            output = EquipmentRecommendationOutput.model_validate(raw_output)
        except (ValidationError, TypeError, ValueError):
            return {
                "outcome": "retry_required",
                "error_code": "malformed_structured_output",
                "prompt_tokens": response.prompt_tokens,
                "completion_tokens": response.completion_tokens,
            }
        return {
            "outcome": "recommended",
            "recommendation": output.recommendation,
            "error_code": None,
            "prompt_tokens": response.prompt_tokens,
            "completion_tokens": response.completion_tokens,
        }

    return RunnableLambda(recommend, name="recommend_onboarding_equipment")


def _equipment_recommendation_step() -> OnboardingStep:
    """Return the explicit persisted recommendation checkpoint."""

    return OnboardingStep.EQUIPMENT_RECOMMENDATION
