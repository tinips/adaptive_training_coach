"""Transient state for stateless onboarding-context LangGraph runs."""

from __future__ import annotations

from typing import Literal, TypedDict

from app.domain.enums import OnboardingStep
from app.schemas.onboarding_context import EquipmentRecommendationGoalContext

FreeTextValidationGraphOutcome = Literal[
    "accepted",
    "retry_required",
    "provider_error",
]
EquipmentRecommendationGraphOutcome = Literal[
    "recommended",
    "retry_required",
    "provider_error",
]


class FreeTextValidationGraphState(TypedDict, total=False):
    """State for a single free-text usability check with no checkpoints."""

    step: OnboardingStep
    user_text: str
    goal_context: EquipmentRecommendationGoalContext | None
    outcome: FreeTextValidationGraphOutcome
    error_code: str | None
    prompt_tokens: int | None
    completion_tokens: int | None


class EquipmentRecommendationGraphState(TypedDict, total=False):
    """State for one goal-based equipment recommendation with no checkpoints."""

    goal_context: EquipmentRecommendationGoalContext
    outcome: EquipmentRecommendationGraphOutcome
    recommendation: str | None
    error_code: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
