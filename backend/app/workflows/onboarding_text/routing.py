"""Conditional routing for the four safe terminal graph outcomes."""

from __future__ import annotations

from app.schemas.onboarding import OnboardingTextOutcome
from app.workflows.onboarding_text.state import OnboardingTextGraphState


def select_outcome(state: OnboardingTextGraphState) -> OnboardingTextOutcome:
    """Route only to an explicit safe terminal node."""

    return state.get("outcome", "fallback_required")
