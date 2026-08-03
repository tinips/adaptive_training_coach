"""Typed application boundary for the focused onboarding goal workflow."""

from __future__ import annotations

from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

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
