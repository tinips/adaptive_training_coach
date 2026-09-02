"""LLM-backed extraction of an athlete's stated weekly availability."""
# ruff: noqa: E501

from __future__ import annotations

import json

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig

from app.domain.enums import OnboardingStep
from app.integrations.llm.models import LLMIntegrationError, StructuredOnboardingModel
from app.schemas.availability import AvailabilityExtraction, ConfirmedWeeklyAvailability

_PROMPT = """This is a structured data-extraction task, not a conversation or
planning task. Return only schema-valid JSON that the backend will validate and
store as a weekly availability draft; never return prose, workouts, advice, or
recommendations.

Return this exact object shape:
{
  "parse_status": "complete | needs_details | needs_clarification",
  "clarification_reason": "string or null",
  "missing_details": [{"day": "monday", "field": "duration_minutes", "description": "string"}],
  "days": {
    "monday": {"available": false, "disciplines": [], "time_windows": []},
    "tuesday": {"available": true, "disciplines": ["running"], "time_windows": [{"time_of_day": "morning or null", "duration_minutes": 60}]}
  }
}

The days object must contain exactly monday, tuesday, wednesday, thursday,
friday, saturday, and sunday. Use only running, cycling, swimming, or
strength_training for disciplines; and only morning, afternoon, evening, night,
or null for time_of_day. Durations are positive integer minutes. An unavailable
day must have empty disciplines and time_windows. An available day must have at
least one discipline.

Interpret ordinary conversational availability generously. Approximate wording is
usable: "about an hour", "one hour more or less", "around 60 minutes", and
"up to an hour" each mean duration_minutes 60. When a numeric duration is
followed by flexible wording such as "or as necessary", keep the stated number
as the practical planning limit (for example, "2 hours or as necessary" means
120 minutes). A bare second duration after an earlier duration expressed in
hours may use that same unit.

Expand common day groups: "every day" means all seven days; "during the week"
means Monday through Friday; and "weekends" means Saturday and Sunday. If a
generic training statement applies to a group of days, use the athlete's goal
disciplines for those days. If it also gives a discipline restriction, apply the
restriction to that discipline while retaining generic availability for the
other relevant disciplines. For example, "I can train every day, but swimming
only on weekends; about one hour during the week and two hours on weekends"
is complete: weekdays use the goal disciplines except swimming for 60 minutes;
weekend days use the goal disciplines including swimming for 120 minutes.

Only return needs_details when no usable duration can be inferred for an
otherwise available day. Return needs_clarification only for genuinely
contradictory or unintelligible input. Never invent a duration where the user
gave no duration at all."""


class AvailabilityExtractionService:
    def __init__(self, model: StructuredOnboardingModel) -> None:
        self._model = model

    async def extract(
        self, text: str, *, goal_disciplines: tuple[str, ...]
    ) -> AvailabilityExtraction:
        try:
            response = await self._model.ainvoke_structured(
                step=OnboardingStep.AVAILABILITY_INTAKE,
                schema=AvailabilityExtraction,
                messages=[
                    SystemMessage(
                        content=(
                            f"{_PROMPT}\n\nThis athlete's goal disciplines are: "
                            f"{', '.join(goal_disciplines)}. When the athlete says "
                            "they can 'train' on a day without naming an activity, "
                            "use these goal disciplines. Explicit restrictions in the "
                            "athlete's text always override this default."
                        )
                    ),
                    HumanMessage(content=text),
                ],
                config=RunnableConfig(),
            )
        except LLMIntegrationError as exc:
            raise AvailabilityExtractionError("availability_extraction_failed") from exc
        if response.malformed or response.output is None:
            raise AvailabilityExtractionError("availability_extraction_failed")
        try:
            return AvailabilityExtraction.model_validate(response.output)
        except ValueError as exc:
            raise AvailabilityExtractionError("availability_extraction_failed") from exc

    async def revise(
        self,
        *,
        current: ConfirmedWeeklyAvailability,
        change_request: str,
        goal_disciplines: tuple[str, ...],
    ) -> AvailabilityExtraction:
        """Apply one athlete-requested change while preserving the schedule's rest."""

        current_schedule = json.dumps(current.model_dump(mode="json"), sort_keys=True)
        try:
            response = await self._model.ainvoke_structured(
                step=OnboardingStep.AVAILABILITY_INTAKE,
                schema=AvailabilityExtraction,
                messages=[
                    SystemMessage(
                        content=(
                            f"{_PROMPT}\n\nThis is the athlete's current confirmed "
                            "weekly schedule:\n"
                            f"{current_schedule}\n\nApply only the requested changes. "
                            "Preserve every day, sport restriction, and time window not "
                            "explicitly changed by the athlete. Return a complete revised "
                            "schedule, never a patch. The athlete's goal disciplines are: "
                            f"{', '.join(goal_disciplines)}."
                        )
                    ),
                    HumanMessage(content=change_request),
                ],
                config=RunnableConfig(),
            )
        except LLMIntegrationError as exc:
            raise AvailabilityExtractionError("availability_extraction_failed") from exc
        if response.malformed or response.output is None:
            raise AvailabilityExtractionError("availability_extraction_failed")
        try:
            return AvailabilityExtraction.model_validate(response.output)
        except ValueError as exc:
            raise AvailabilityExtractionError("availability_extraction_failed") from exc


class AvailabilityExtractionError(RuntimeError):
    pass
