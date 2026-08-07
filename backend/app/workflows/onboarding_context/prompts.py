"""Static prompts for raw-text onboarding context workflows."""

from __future__ import annotations

import json
from typing import Final

from app.domain.enums import OnboardingStep
from app.schemas.onboarding_context import EquipmentRecommendationGoalContext

_CONTEXT_STEP_INSTRUCTIONS: Final[dict[str, str]] = {
    "AVAILABILITY_INTAKE": (
        "The answer must say something usable about weekly training availability, "
        "such as available days, number of days, times, durations, constraints, "
        "or a combination of these."
    ),
    "EQUIPMENT_DETAILS_INTAKE": (
        "The answer must say something usable about equipment the athlete has, "
        "does not have, can access, or an equipment-related constraint."
    ),
    "HEALTH_LIMITATIONS_INTAKE": (
        "The answer must state a physical limitation, injury status, health "
        "constraint, or that no such limitation applies."
    ),
}

EQUIPMENT_RECOMMENDATION_CONTRACT: Final = (
    "Recommend only a short, practical list of essential equipment appropriate to "
    "the confirmed athlete goal. Return only the requested structured response. "
    "Write concise English plain text, at most five short items or sentences. Do "
    "not generate a training plan, infer injuries, provide medical advice, mention "
    "brands, or claim to know what the athlete already owns. If the goal is broad, "
    "give a conservative general-training recommendation."
)


def is_supported_free_text_step(step: OnboardingStep) -> bool:
    """Return whether the step is one of the raw-context intake steps."""

    return step.value in _CONTEXT_STEP_INSTRUCTIONS


def render_equipment_recommendation_system_prompt(
    *,
    goal_context: EquipmentRecommendationGoalContext,
) -> str:
    """Render a recommendation request from canonical goal fields only."""

    goal_json = json.dumps(
        goal_context.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"{EQUIPMENT_RECOMMENDATION_CONTRACT} Confirmed goal: {goal_json}"
