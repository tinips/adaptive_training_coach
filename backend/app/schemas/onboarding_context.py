"""Application contract for literal onboarding context validation."""

from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import OnboardingStep

FreeTextValidationOutcome = Literal["accepted", "retry_required", "provider_error"]


class FreeTextValidationWorkflowResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    outcome: FreeTextValidationOutcome
    error_code: str | None = Field(default=None, max_length=80)
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)


class ContextOnboardingWorkflow(Protocol):
    async def validate_free_text(
        self, *, step: OnboardingStep, user_text: str
    ) -> FreeTextValidationWorkflowResult: ...

    async def validate_text(
        self, *, step: OnboardingStep, user_text: str
    ) -> FreeTextValidationWorkflowResult: ...
