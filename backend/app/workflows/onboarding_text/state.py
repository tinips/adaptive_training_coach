"""Typed state passed explicitly through the stateless onboarding graph."""

from __future__ import annotations

from typing import TypedDict
from uuid import UUID

from app.domain.enums import OnboardingStep
from app.schemas.onboarding import OnboardingParseResult, OnboardingTextOutcome


class OnboardingTextGraphState(TypedDict, total=False):
    """One isolated parse invocation; no checkpointer or database state."""

    user_id: UUID
    onboarding_step: OnboardingStep
    user_text: str
    confirmed_context: dict[str, object]
    parse_result: OnboardingParseResult
    outcome: OnboardingTextOutcome
    error_code: str | None
    model_output: object
    model_malformed: bool
    prompt_tokens: int | None
    completion_tokens: int | None
