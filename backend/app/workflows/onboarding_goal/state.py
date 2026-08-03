"""Explicit state for one stateless goal-extraction graph run."""

from __future__ import annotations

from typing import Annotated, Literal, TypedDict
from uuid import UUID

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

from app.integrations.llm.models import (
    GoalExtractionAction,
    GoalExtractionOutput,
    GoalExtractionPatch,
)
from app.schemas.onboarding_goal import GoalExtractionOutcome, OnboardingUpdateHandler

GoalWorkflowAction = GoalExtractionAction | Literal["MODIFY_ONBOARDING_DATA"]
GoalWorkflowOutcome = (
    GoalExtractionOutcome
    | Literal[
        "onboarding_modified",
        "no_onboarding_update",
    ]
)


class GoalExtractionGraphState(TypedDict, total=False):
    """No conversation history or checkpoint is retained by LangGraph."""

    user_id: UUID
    action: GoalWorkflowAction
    user_text: str
    existing_draft: GoalExtractionOutput | None
    current_date: str
    goal_patch: GoalExtractionPatch
    messages: Annotated[list[BaseMessage], add_messages]
    onboarding_updater: OnboardingUpdateHandler
    onboarding_updated: bool
    confirmation: str
    outcome: GoalWorkflowOutcome
    error_code: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
