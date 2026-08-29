"""Provider-independent types for structured onboarding model calls."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from langchain_core.messages import BaseMessage
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel

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
