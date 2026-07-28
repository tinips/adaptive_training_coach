"""Typed boundaries shared by deterministic and LLM onboarding paths."""

from __future__ import annotations

from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from app.domain.enums import OnboardingStep

OnboardingTextOutcome = Literal[
    "confirmation_required",
    "clarification_required",
    "fallback_required",
    "provider_error",
]


class OnboardingParseResult(BaseModel):
    """A provider-independent interpretation of one free-text answer.

    The model is deliberately limited to JSON-compatible values because pending
    interpretations are stored in a JSONB staging column. It contains no raw
    prompt, full profile, provider exception, or credential data.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    normalized_value: JsonValue | None = None
    display_value: str | None = Field(default=None, max_length=500)
    confidence: float = Field(ge=0.0, le=1.0)
    requires_clarification: bool = False
    clarification_question: str | None = Field(default=None, max_length=300)
    safety_flag: bool = False
    safety_reason: str | None = Field(default=None, max_length=200)


class OnboardingTextWorkflowResult(BaseModel):
    """Safe result returned by the compiled onboarding-text workflow."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    outcome: OnboardingTextOutcome
    parse_result: OnboardingParseResult | None = None
    error_code: str | None = Field(default=None, max_length=80)
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)


class OnboardingTextParser(Protocol):
    """Application-facing contract for explicit free-text interpretation."""

    async def parse(
        self,
        *,
        user_id: UUID,
        step: OnboardingStep,
        user_text: str,
        confirmed_context: dict[str, object],
    ) -> OnboardingTextWorkflowResult:
        """Interpret one answer without mutating confirmed onboarding data."""


class OnboardingTransition(BaseModel):
    """Copy-on-write deterministic transition suitable for JSONB persistence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    current_step: OnboardingStep
    answers: dict[str, JsonValue]
    return_to_summary: bool = False


class MultiselectUpdate(BaseModel):
    """Result of toggling one deterministic multi-select option."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    values: list[str]
    changed: bool


SummaryEditSection = Literal[
    "goal",
    "availability",
    "equipment",
    "limitations",
    "coach_style",
    "baseline",
]
