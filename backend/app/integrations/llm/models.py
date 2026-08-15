"""Provider-independent types for structured onboarding model calls."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal, Protocol

from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.runnables import Runnable, RunnableConfig
from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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
type TemplateDecision = Literal["USE_EXISTING", "CREATE"]
type SupportingTemplateDecision = Literal[
    "USE_EXISTING",
    "CREATE",
    "NONE",
    "UNSUPPORTED",
]


class GoalTemplateSummary(BaseModel):
    """Compact active catalog row supplied to goal classification."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,63}$")
    kind: Literal["PRIMARY", "SUPPORTING"]
    display_name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=500)


class PrimaryTemplateCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: TemplateDecision
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,63}$")
    display_name: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def require_created_definition(self) -> PrimaryTemplateCandidate:
        if self.decision == "CREATE" and (
            not self.display_name or not self.description
        ):
            raise ValueError("created templates require a name and description")
        return self


class SupportingTemplateCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: SupportingTemplateDecision
    code: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{2,63}$")
    display_name: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_decision_fields(self) -> SupportingTemplateCandidate:
        if self.decision in {"NONE", "UNSUPPORTED"}:
            if any((self.code, self.display_name, self.description)):
                raise ValueError("non-template decisions cannot define a template")
        elif self.code is None:
            raise ValueError("template decisions require a code")
        elif self.decision == "CREATE" and (
            not self.display_name or not self.description
        ):
            raise ValueError("created templates require a name and description")
        return self


class _GoalExtractionFields(BaseModel):
    """Shared validation for the persisted draft and model-returned patch."""

    model_config = ConfigDict(extra="forbid")

    main_goal: str | None = Field(max_length=500)
    event_date: date | None
    target_outcome: str | None = Field(max_length=500)
    secondary_priority: str | None = Field(max_length=500)
    primary_template: PrimaryTemplateCandidate | None = None
    supporting_template: SupportingTemplateCandidate | None = None
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


# The same provider boundary is shared by focused onboarding graphs.  Each
# graph owns its response schema and revalidates the returned object before it
# crosses its application boundary.
StructuredOutputSchema = type[BaseModel]


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

    def bind_tools(
        self,
        tools: Sequence[BaseTool],
    ) -> Runnable[Any, AIMessage]:
        """Return a tool-capable chat runnable without invoking the provider."""
