"""Safe, vendor-neutral contracts for future workflow tracing."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import OnboardingStep

ProviderMode = Literal["mock", "live"]
AIWorkflowOutcome = Literal[
    "confirmation_required",
    "fallback_required",
    "provider_error",
]


class AIWorkflowRunMetadata(BaseModel):
    """Metadata safe to send to an observer.

    Raw user text, confirmed profile content, prompts, credentials, external
    tokens, and Telegram identifiers are intentionally not representable.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    workflow_name: str = Field(min_length=1, max_length=100)
    run_id: UUID
    onboarding_step: OnboardingStep
    provider_mode: ProviderMode
    model_name: str = Field(min_length=1, max_length=120)
    started_at: datetime


class AIWorkflowRunResult(BaseModel):
    """Safe completion record for a workflow invocation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    metadata: AIWorkflowRunMetadata
    outcome: AIWorkflowOutcome
    completed_at: datetime
    latency_ms: int = Field(ge=0)
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    error_code: str | None = Field(default=None, max_length=80)


class AIWorkflowRunError(BaseModel):
    """Sanitized failure record; provider exception text is never included."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    metadata: AIWorkflowRunMetadata
    failed_at: datetime
    latency_ms: int = Field(ge=0)
    error_code: str = Field(min_length=1, max_length=80)


class AIWorkflowObserver(Protocol):
    """Observer that can later be backed by Langfuse without workflow changes."""

    async def on_run_started(self, metadata: AIWorkflowRunMetadata) -> None:
        """Observe a run start."""

    async def on_run_completed(self, result: AIWorkflowRunResult) -> None:
        """Observe a terminal non-provider-error outcome."""

    async def on_run_failed(self, error: AIWorkflowRunError) -> None:
        """Observe a provider or unexpected workflow failure."""
