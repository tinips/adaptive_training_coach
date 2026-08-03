"""Explicit state for one stateless goal-extraction graph run."""

from __future__ import annotations

from typing import TypedDict
from uuid import UUID

from app.integrations.llm.models import (
    GoalExtractionAction,
    GoalExtractionOutput,
    GoalExtractionPatch,
)
from app.schemas.onboarding_goal import GoalExtractionOutcome


class GoalExtractionGraphState(TypedDict, total=False):
    """No conversation history or checkpoint is retained by LangGraph."""

    user_id: UUID
    action: GoalExtractionAction
    user_text: str
    existing_draft: GoalExtractionOutput | None
    current_date: str
    goal_patch: GoalExtractionPatch
    outcome: GoalExtractionOutcome
    error_code: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
