"""Delivery-neutral results from stateful onboarding use cases."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, JsonValue

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
    "goal_confirmed",
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
    created: bool = False
