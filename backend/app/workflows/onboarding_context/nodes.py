"""LangGraph nodes for stateless onboarding-context model calls."""

from __future__ import annotations

import re
from typing import Literal

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import Runnable, RunnableConfig, RunnableLambda
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

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

EquipmentImportance = Literal["Essential", "Recommended", "Optional"]
_TRAINING_STAGES = (
    "Start now",
    "Base training",
    "Race-specific prep",
    "Advanced prep",
    "Race day",
)


class EquipmentRecommendationItem(BaseModel):
    """One equipment item and its practical priority."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    equipment_name: str = Field(min_length=1)
    importance: EquipmentImportance
    when_needed: str = Field(min_length=1)

    @field_validator("equipment_name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("equipment name must not be blank")
        return normalized

    @field_validator("when_needed")
    @classmethod
    def normalize_training_stage(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("when needed must not be blank")
        if not any(
            normalized.startswith(f"{stage} —") or normalized.startswith(f"{stage}:")
            for stage in _TRAINING_STAGES
        ):
            raise ValueError("when needed must start with an allowed training stage")
        return normalized


class EquipmentRecommendationOutput(BaseModel):
    """Short structured material recommendation for a confirmed goal."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    items: list[EquipmentRecommendationItem] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_items(self) -> EquipmentRecommendationOutput:
        names = [item.equipment_name.casefold() for item in self.items]
        if len(set(names)) != len(names):
            raise ValueError("equipment items must be unique")
        text = " ".join(item.equipment_name for item in self.items)
        if _PROHIBITED_RECOMMENDATION_CONTENT.search(text) is not None:
            raise ValueError("recommendation must not contain plan or medical content")
        return self


class EquipmentInterpretationOutput(BaseModel):
    """Facts stated by the athlete; unknowns stay explicitly unknown."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    available: list[str] = []
    unavailable: list[str] = []
    access_constraints: list[str] = []
    substitutions: list[str] = []
    unknowns: list[str] = []


def format_equipment_recommendation(
    items: list[EquipmentRecommendationItem],
) -> str:
    """Render stable plain text columns suitable for Telegram's ``<pre>`` block."""

    name_width = max(len("Equipment"), *(len(item.equipment_name) for item in items))
    importance_width = len("Importance")
    stage_width = max(len("When needed"), *(len(item.when_needed) for item in items))
    header = (
        f"{'Equipment':<{name_width}}  {'Importance':<{importance_width}}  When needed"
    )
    divider = f"{'-' * name_width}  {'-' * importance_width}  {'-' * stage_width}"
    rows = [
        f"{item.equipment_name:<{name_width}}  {item.importance:<{importance_width}}  "
        f"{item.when_needed}"
        for item in items
    ]
    table = "\n".join([header, divider, *rows])
    if len(table) > 3_500:
        raise ValueError("equipment table exceeds Telegram message capacity")
    return table


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
        return {"outcome": "accepted", "error_code": None}

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
        if goal_context.equipment_text is not None:
            messages = [
                SystemMessage(
                    content=(
                        "Interpret only facts explicitly stated in the athlete's "
                        "equipment answer. Never infer ownership, bike type, "
                        "schedules, medical facts, or a training plan. Unknown "
                        "details go in unknowns. Deterministic "
                        f"recommendation: {goal_context.recommendation_text or ''}"
                    )
                ),
                HumanMessage(content=goal_context.equipment_text),
            ]
            try:
                response = await model.ainvoke_structured(
                    step=OnboardingStep.EQUIPMENT_DETAILS_INTAKE,
                    schema=EquipmentInterpretationOutput,
                    messages=messages,
                    config=config,
                )
                interpretation_output = EquipmentInterpretationOutput.model_validate(
                    response.output
                )
            except Exception:
                return {"outcome": "provider_error", "error_code": "provider_failure"}
            return {
                "outcome": "recommended",
                "interpretation": interpretation_output.model_dump(),
                "error_code": None,
            }
        if goal_context.main_goal is None:
            return {"outcome": "retry_required", "error_code": "missing_goal_context"}
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
            return {"outcome": "provider_error", "error_code": "provider_timeout"}
        except LLMConfigurationError as exc:
            return {"outcome": "provider_error", "error_code": exc.code}
        except LLMProviderError as exc:
            return {"outcome": "provider_error", "error_code": exc.code}
        except Exception:
            return {"outcome": "provider_error", "error_code": "provider_failure"}

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
        try:
            recommendation = format_equipment_recommendation(output.items)
        except ValueError:
            return {
                "outcome": "retry_required",
                "error_code": "malformed_structured_output",
                "prompt_tokens": response.prompt_tokens,
                "completion_tokens": response.completion_tokens,
            }
        return {
            "outcome": "recommended",
            "recommendation": recommendation,
            "error_code": None,
            "prompt_tokens": response.prompt_tokens,
            "completion_tokens": response.completion_tokens,
        }

    return RunnableLambda(recommend, name="recommend_onboarding_equipment")


def _equipment_recommendation_step() -> OnboardingStep:
    """Return the explicit persisted recommendation checkpoint."""

    return OnboardingStep.EQUIPMENT_RECOMMENDATION
