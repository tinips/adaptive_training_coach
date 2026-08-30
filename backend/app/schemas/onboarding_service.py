"""Delivery-neutral results from stateful onboarding use cases."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from app.domain.enums import (
    OnboardingStatus,
    OnboardingStep,
    UserStatus,
)
from app.schemas.capabilities import CapabilityReview, GoalExecutionAssessment

OnboardingResultKind = Literal[
    "step",
    "setup_introduction",
    "goal_intake",
    "goal_swimming_type",
    "goal_metric_intake",
    "goal_manual_targets",
    "goal_event_date",
    "goal_confirmed",
    "profile_birth_year_intake",
    "profile_gender_intake",
    "profile_weight_intake",
    "profile_height_intake",
    "availability_intake",
    "availability_review",
    "availability_details",
    "availability_clarification",
    "equipment_recommendation",
    "equipment_intake",
    "equipment_unmatched",
    "health_limitations_intake",
    "baseline_intake",
    "baseline_validation_error",
    "training_history_import",
    "context_validation_error",
    "profile_validation_error",
    "onboarding_completed",
    "cancelled",
]


class OnboardingServiceResult(BaseModel):
    """Safe result rendered by Telegram without accessing persistence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: OnboardingResultKind
    user_id: UUID
    user_status: UserStatus
    onboarding_status: OnboardingStatus
    current_step: OnboardingStep
    answers: dict[str, JsonValue]
    error_code: str | None = None
    confirmation: str | None = Field(default=None, max_length=1000)
    updated_fields: tuple[str, ...] = ()
    created: bool = False
    training_history_skipped: bool = False
    capability_review: CapabilityReview | None = None
    execution_assessment: GoalExecutionAssessment | None = None


class UpdatedOnboardingData(BaseModel):
    """Sanitized fields written by one ownership-scoped onboarding update."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    updated_fields: dict[str, JsonValue]
