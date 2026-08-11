"""Supported literal onboarding-context steps."""

from __future__ import annotations

from typing import Final

from app.domain.enums import OnboardingStep

_SUPPORTED_STEPS: Final = frozenset(
    {
        OnboardingStep.AVAILABILITY_INTAKE,
        OnboardingStep.HEALTH_LIMITATIONS_INTAKE,
    }
)


def is_supported_free_text_step(step: OnboardingStep) -> bool:
    return step in _SUPPORTED_STEPS
