"""Transient state for stateless onboarding-context LangGraph runs."""

from __future__ import annotations

from typing import Literal, TypedDict

from app.domain.enums import OnboardingStep

FreeTextValidationGraphOutcome = Literal["accepted", "retry_required", "provider_error"]


class FreeTextValidationGraphState(TypedDict, total=False):
    step: OnboardingStep
    user_text: str
    outcome: FreeTextValidationGraphOutcome
    error_code: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
