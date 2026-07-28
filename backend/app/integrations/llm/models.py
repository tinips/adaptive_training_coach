"""Provider-independent types for structured onboarding model calls."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from langchain_core.messages import BaseMessage
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import OnboardingStep
from app.observability.protocol import ProviderMode


class LLMIntegrationError(RuntimeError):
    """Base exception with only a stable safe code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class LLMConfigurationError(LLMIntegrationError):
    """Raised lazily when live invocation lacks required configuration."""


class LLMProviderError(LLMIntegrationError):
    """Deterministic fake/provider error used without exposing upstream text."""


class _CommonStructuredOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_value: str | None = Field(default=None, max_length=500)
    confidence: float = Field(ge=0.0, le=1.0)
    requires_clarification: bool = False
    clarification_question: str | None = Field(default=None, max_length=300)
    safety_flag: bool = False
    safety_reason: str | None = Field(default=None, max_length=200)


class ScalarOnboardingOutput(_CommonStructuredOutput):
    """Structured output for a free-form scalar answer."""

    normalized_value: str | None = Field(default=None, max_length=500)


class MultiValueOnboardingOutput(_CommonStructuredOutput):
    """Structured output for a free-form multi-select answer."""

    normalized_value: list[str] | None = None


class NumericOnboardingOutput(_CommonStructuredOutput):
    """Structured output type retained for future explicitly LLM-backed units.

    Numeric onboarding input in this milestone is parsed deterministically and
    does not enter the graph.
    """

    normalized_value: int | float | None = None


class PrimarySportOnboardingOutput(ScalarOnboardingOutput):
    """Constrained normalized category for a custom primary sport answer."""

    normalized_value: (
        Literal[
            "RUNNING",
            "CYCLING",
            "TRIATHLON",
            "SWIMMING",
            "GENERAL_FITNESS",
            "OTHER",
        ]
        | None
    ) = None


class GoalTypeOnboardingOutput(ScalarOnboardingOutput):
    """Constrained normalized goal category shared by dynamic sport options."""

    normalized_value: (
        Literal[
            "FIVE_K",
            "TEN_K",
            "HALF_MARATHON",
            "MARATHON",
            "TRAIL",
            "CYCLING_EVENT",
            "GRAN_FONDO",
            "SPRINT_TRIATHLON",
            "OLYMPIC_TRIATHLON",
            "HALF_IRONMAN_70_3",
            "IRONMAN",
            "FIRST_TRIATHLON",
            "IMPROVE_TECHNIQUE",
            "OPEN_WATER_SWIMMING",
            "SPECIFIC_EVENT",
            "GENERAL_HEALTH",
            "IMPROVE_ENDURANCE",
            "IMPROVE_PERFORMANCE",
            "LOSE_BODY_FAT",
            "BUILD_STRENGTH",
            "OTHER",
        ]
        | None
    ) = None


class GoalPriorityOnboardingOutput(ScalarOnboardingOutput):
    """Constrained normalized goal-priority category."""

    normalized_value: (
        Literal[
            "FINISH_SAFELY",
            "PERSONAL_BEST",
            "TARGET_TIME",
            "HEALTH_CONSISTENCY",
            "OTHER",
        ]
        | None
    ) = None


class EquipmentOnboardingOutput(_CommonStructuredOutput):
    """Constrained list of normalized equipment categories."""

    normalized_value: (
        list[
            Literal[
                "RUNNING_SHOES",
                "ROAD_BIKE",
                "MOUNTAIN_BIKE",
                "INDOOR_BIKE_TRAINER",
                "SWIMMING_POOL",
                "GYM",
                "RESISTANCE_BANDS",
                "SPORTS_WATCH",
                "HEART_RATE_CHEST_STRAP",
                "OTHER",
            ]
        ]
        | None
    ) = None


class HealthAreasOnboardingOutput(_CommonStructuredOutput):
    """Constrained body-area categories that cannot represent diagnoses."""

    normalized_value: (
        list[
            Literal[
                "NONE",
                "SHOULDER",
                "BACK",
                "HIP",
                "KNEE",
                "ANKLE_FOOT",
                "OTHER",
            ]
        ]
        | None
    ) = None


StructuredOutputSchema = type[
    ScalarOnboardingOutput
    | MultiValueOnboardingOutput
    | NumericOnboardingOutput
    | PrimarySportOnboardingOutput
    | GoalTypeOnboardingOutput
    | GoalPriorityOnboardingOutput
    | EquipmentOnboardingOutput
    | HealthAreasOnboardingOutput
]


def structured_output_schema_for_step(
    step: OnboardingStep,
) -> StructuredOutputSchema:
    """Select a narrow Pydantic schema for the requested onboarding step."""

    if step is OnboardingStep.PRIMARY_SPORT:
        return PrimarySportOnboardingOutput
    if step is OnboardingStep.GOAL_TYPE:
        return GoalTypeOnboardingOutput
    if step is OnboardingStep.GOAL_PRIORITY:
        return GoalPriorityOnboardingOutput
    if step is OnboardingStep.EQUIPMENT:
        return EquipmentOnboardingOutput
    if step is OnboardingStep.HEALTH_AREAS:
        return HealthAreasOnboardingOutput
    if step in {OnboardingStep.AGE, OnboardingStep.HEIGHT, OnboardingStep.WEIGHT}:
        return NumericOnboardingOutput
    return ScalarOnboardingOutput


@dataclass(frozen=True, slots=True)
class StructuredModelResponse:
    """Sanitized response passed from the integration into graph validation."""

    output: object | None
    malformed: bool = False
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class StructuredOnboardingModel(Protocol):
    """Minimal model interface consumed by LangGraph nodes."""

    @property
    def provider_mode(self) -> ProviderMode:
        """Configured provider mode."""

    @property
    def model_name(self) -> str:
        """Safe configured model identifier."""

    async def ainvoke_structured(
        self,
        *,
        step: OnboardingStep,
        schema: StructuredOutputSchema,
        messages: list[BaseMessage],
        config: RunnableConfig,
    ) -> StructuredModelResponse:
        """Invoke a LangChain runnable that returns structured output."""
