"""Typed application boundary for the focused onboarding goal workflow."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from app.integrations.llm.models import (
    GoalExtractionAction,
    GoalExtractionOutput,
    GoalExtractionPatch,
)

GoalExtractionOutcome = Literal[
    "extracted",
    "fallback_required",
    "provider_error",
]


class GoalExtractionWorkflowResult(BaseModel):
    """Sanitized result from one stateless compiled goal-extraction run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    outcome: GoalExtractionOutcome
    goal_patch: GoalExtractionPatch | None = None
    error_code: str | None = Field(default=None, max_length=80)
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)


class UpdatedOnboardingData(BaseModel):
    """Sanitized fields written by one ownership-scoped onboarding update."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    updated_fields: dict[str, JsonValue]


OnboardingUpdateHandler = Callable[..., Awaitable[UpdatedOnboardingData]]


class OnboardingModificationWorkflowResult(BaseModel):
    """Safe final result from the generic agent/tool modification loop."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    outcome: Literal[
        "onboarding_modified",
        "no_onboarding_update",
        "provider_error",
    ]
    confirmation: str | None = Field(default=None, max_length=1000)
    error_code: str | None = Field(default=None, max_length=80)


class GoalExtractor(Protocol):
    """Application-facing contract for conversational goal extraction."""

    async def extract(
        self,
        *,
        user_id: UUID,
        action: GoalExtractionAction,
        user_text: str,
        existing_draft: GoalExtractionOutput | None,
        current_date: str,
    ) -> GoalExtractionWorkflowResult:
        """Extract a patch from the latest answer without writing canonical data."""

    async def modify_onboarding_data(
        self,
        *,
        user_id: UUID,
        user_text: str,
        onboarding_updater: OnboardingUpdateHandler,
    ) -> OnboardingModificationWorkflowResult:
        """Run the tool-calling loop for an already-completed onboarding record."""
