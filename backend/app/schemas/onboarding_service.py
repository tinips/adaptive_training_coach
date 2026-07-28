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
from app.schemas.onboarding import OnboardingParseResult

OnboardingResultKind = Literal[
    "step",
    "summary",
    "awaiting_text",
    "interpretation",
    "clarification",
    "fallback",
    "provider_error",
    "rate_limited",
    "cancelled",
    "completed",
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
    parse_result: OnboardingParseResult | None = None
    clarification_question: str | None = None
    error_code: str | None = None
    created: bool = False
