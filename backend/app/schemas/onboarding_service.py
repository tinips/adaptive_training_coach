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

OnboardingResultKind = Literal[
    "step",
    "fallback",
    "provider_error",
    "rate_limited",
    "setup_introduction",
    "goal_intake",
    "goal_clarification",
    "goal_confirmation",
    "goal_addition",
    "goal_off_topic",
    "onboarding_modification",
    "goal_confirmed",
    "profile_birth_year_intake",
    "profile_gender_intake",
    "profile_weight_intake",
    "profile_height_intake",
    "availability_intake",
    "equipment_recommendation",
    "equipment_intake",
    "equipment_details_intake",
    "health_limitations_intake",
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
