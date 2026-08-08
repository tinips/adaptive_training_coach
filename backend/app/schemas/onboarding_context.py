"""Application contracts for raw-text onboarding context workflows.

The workflow accepts any non-empty answer and the application stores the
original text. These models deliberately never expose an interpreted version.
"""

from __future__ import annotations

from datetime import date
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.enums import OnboardingStep

FreeTextValidationOutcome = Literal[
    "accepted",
    "retry_required",
    "provider_error",
]
EquipmentRecommendationOutcome = Literal[
    "recommended",
    "retry_required",
    "provider_error",
]


class EquipmentRecommendationGoalContext(BaseModel):
    """Goal fields supplied to an equipment recommendation run.

    The canonical goal is already persisted by the application.  This model is
    transient graph input only and is never used to write a goal.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    main_goal: str | None = Field(default=None, max_length=500)
    target_outcome: str | None = Field(default=None, max_length=500)
    event_date: date | None = None
    secondary_priority: str | None = Field(default=None, max_length=500)
    equipment_text: str | None = Field(default=None, max_length=4096)
    recommendation_text: str | None = Field(default=None, max_length=3500)

    @field_validator(
        "main_goal",
        "target_outcome",
        "secondary_priority",
    )
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split())
        return normalized or None


class FreeTextValidationWorkflowResult(BaseModel):
    """Safe result for one free-text usability check.

    No answer text, extracted fields, or model rationale is returned, so callers
    cannot accidentally replace the athlete's original answer with an LLM
    interpretation.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    outcome: FreeTextValidationOutcome
    error_code: str | None = Field(default=None, max_length=80)
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)


class EquipmentRecommendationWorkflowResult(BaseModel):
    """Safe result for one goal-based equipment recommendation run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    outcome: EquipmentRecommendationOutcome
    recommendation: str | None = Field(default=None, max_length=3_500)
    error_code: str | None = Field(default=None, max_length=80)
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)


class EquipmentInterpretationWorkflowResult(BaseModel):
    """Structured, non-recommendation interpretation of athlete wording."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    outcome: FreeTextValidationOutcome
    interpretation: dict[str, list[str]] | None = None
    error_code: str | None = Field(default=None, max_length=80)


class ContextOnboardingWorkflow(Protocol):
    """Stateless graph boundary used by the onboarding application service."""

    async def validate_free_text(
        self,
        *,
        step: OnboardingStep,
        user_text: str,
        goal_context: EquipmentRecommendationGoalContext | None = None,
    ) -> FreeTextValidationWorkflowResult:
        """Accept a non-empty raw answer for the active context question."""

    async def validate_text(
        self,
        *,
        step: OnboardingStep,
        user_text: str,
        goal_context: EquipmentRecommendationGoalContext | None = None,
    ) -> FreeTextValidationWorkflowResult:
        """Compatibility alias for ``validate_free_text``."""

    async def recommend_equipment(
        self,
        *,
        main_goal: str | None,
        target_outcome: str | None,
        event_date: date | None,
        secondary_priority: str | None,
    ) -> EquipmentRecommendationWorkflowResult:
        """Return a short equipment suggestion for confirmed goal fields."""

    async def interpret_equipment(
        self, *, goal_context: EquipmentRecommendationGoalContext
    ) -> EquipmentInterpretationWorkflowResult:
        """Interpret raw equipment wording against a deterministic recommendation."""
