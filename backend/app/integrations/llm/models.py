"""Provider-independent types for structured onboarding model calls."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal, Protocol

from langchain_core.messages import BaseMessage
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, ConfigDict, Field, field_validator

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


type GoalFieldName = Literal[
    "main_goal",
    "event_date",
    "target_outcome",
    "secondary_priority",
]
type GoalMessageStatus = Literal[
    "COMPLETE",
    "NEEDS_CLARIFICATION",
    "OFF_TOPIC",
]
type GoalExtractionAction = Literal[
    "CREATE_GOAL",
    "UPDATE_EXISTING_GOAL",
]


class _GoalExtractionFields(BaseModel):
    """Shared validation for the persisted draft and model-returned patch."""

    model_config = ConfigDict(extra="forbid")

    main_goal: str | None = Field(max_length=500)
    event_date: date | None
    target_outcome: str | None = Field(max_length=500)
    secondary_priority: str | None = Field(max_length=500)
    missing_fields: list[GoalFieldName]
    ambiguous_fields: list[GoalFieldName]
    message_status: GoalMessageStatus

    @field_validator("main_goal", "target_outcome", "secondary_priority")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split())
        return normalized or None

    @field_validator("missing_fields", "ambiguous_fields")
    @classmethod
    def remove_duplicate_fields(
        cls,
        value: list[GoalFieldName],
    ) -> list[GoalFieldName]:
        return list(dict.fromkeys(value))


class GoalExtractionOutput(_GoalExtractionFields):
    """Complete accumulated goal draft persisted by the application."""


class GoalExtractionPatch(_GoalExtractionFields):
    """Field patch returned by the model for only the latest user message."""


StructuredOutputSchema = type[GoalExtractionPatch]


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
