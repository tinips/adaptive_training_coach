"""Deterministic LangChain runnable used by local development and tests."""

from __future__ import annotations

import json
import re
from enum import StrEnum
from typing import cast

from langchain_core.messages import BaseMessage, HumanMessage
from langchain_core.runnables import RunnableConfig, RunnableLambda

from app.domain.enums import OnboardingStep
from app.integrations.llm.models import (
    LLMProviderError,
    StructuredModelResponse,
    StructuredOutputSchema,
)
from app.observability.protocol import ProviderMode


class FakeLLMScenario(StrEnum):
    """Supported deterministic outcomes for the LangChain-based fake."""

    AUTO = "auto"
    SUCCESS = "success"
    MALFORMED = "malformed"
    PROVIDER_FAILURE = "provider_failure"


class DeterministicFakeOnboardingModel:
    """Fake structured model that still executes as a LangChain runnable."""

    def __init__(
        self,
        scenario: FakeLLMScenario = FakeLLMScenario.AUTO,
        *,
        model_name: str = "deterministic-onboarding-fake",
    ) -> None:
        self._scenario = scenario
        self._model_name = model_name

    @property
    def provider_mode(self) -> ProviderMode:
        return "mock"

    @property
    def model_name(self) -> str:
        return self._model_name

    async def ainvoke_structured(
        self,
        *,
        step: OnboardingStep,
        schema: StructuredOutputSchema,
        messages: list[BaseMessage],
        config: RunnableConfig,
    ) -> StructuredModelResponse:
        user_text = _last_user_text(messages)
        scenario, _ = self._resolve_scenario(user_text)

        async def respond(_: list[BaseMessage]) -> StructuredModelResponse:
            if scenario is FakeLLMScenario.PROVIDER_FAILURE:
                raise LLMProviderError("mock_provider_failure")
            if scenario is FakeLLMScenario.MALFORMED:
                return StructuredModelResponse(
                    output={"confidence": "not-a-number"},
                )
            payload = (
                _fake_availability(messages)
                if step is OnboardingStep.AVAILABILITY_INTAKE
                else _fake_weekly_plan(user_text)
            )
            output = schema.model_validate(payload)
            return StructuredModelResponse(
                output=output,
                prompt_tokens=8,
                completion_tokens=12,
            )

        runnable = RunnableLambda(respond, name="deterministic_structured_output")
        result = await runnable.ainvoke(messages, config=config)
        if not isinstance(result, StructuredModelResponse):
            raise LLMProviderError("mock_invalid_internal_response")
        return result

    def _resolve_scenario(
        self,
        user_text: str,
    ) -> tuple[FakeLLMScenario, str]:
        if self._scenario is not FakeLLMScenario.AUTO:
            return self._scenario, user_text
        prefix = "mock:"
        if not user_text.casefold().startswith(prefix):
            return FakeLLMScenario.SUCCESS, user_text
        parts = user_text.split(":", maxsplit=2)
        try:
            scenario = FakeLLMScenario(parts[1].casefold())
        except (IndexError, ValueError):
            return FakeLLMScenario.SUCCESS, user_text
        value = parts[2].strip() if len(parts) == 3 and parts[2].strip() else "Other"
        return scenario, value


def _last_user_text(messages: list[BaseMessage]) -> str:
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            if isinstance(message.content, str):
                return message.content.strip()
            break
    return ""


def _fake_weekly_plan(request_json: str) -> dict[str, object]:
    """Return a stable valid weekly plan for local bot and service tests."""

    from datetime import date, timedelta

    request = json.loads(request_json)
    week_start = date.fromisoformat(str(request["week_start"]))
    contexts = request.get("goal", {}).get("target_contexts", [])
    discipline = (
        str(contexts[0].get("discipline", "OTHER"))
        if isinstance(contexts, list) and contexts and isinstance(contexts[0], dict)
        else "OTHER"
    )
    days: list[dict[str, object]] = []
    for offset in range(7):
        day = week_start + timedelta(days=offset)
        if offset in {1, 4, 6}:
            days.append(
                {
                    "date": day.isoformat(),
                    "sessions": [
                        {
                            "discipline": discipline,
                            "objective": "Build consistent aerobic training",
                            "duration_minutes": 45,
                            "intensity": "EASY",
                            "structure": (
                                "Easy warm-up, steady main set, easy cool-down."
                            ),
                        }
                    ],
                    "rest_note": None,
                }
            )
        else:
            days.append(
                {
                    "date": day.isoformat(),
                    "sessions": [],
                    "rest_note": "Rest or gentle mobility.",
                }
            )
    return {"week_start": week_start.isoformat(), "days": days}


def _fake_availability(messages: list[BaseMessage]) -> dict[str, object]:
    current = _current_availability(messages)
    if current is not None:
        revised_days = current["days"]
        request = _last_user_text(messages).casefold()
        if "tuesday" in request and "swim" in request:
            duration_match = re.search(r"\b(\d{1,4})\b", request)
            duration = int(duration_match.group(1)) if duration_match else 60
            revised_days["tuesday"] = {
                "available": True,
                "disciplines": ["swimming"],
                "time_windows": [
                    {
                        "time_of_day": "evening" if "evening" in request else None,
                        "duration_minutes": duration,
                    }
                ],
            }
        return {
            "parse_status": "complete",
            "clarification_reason": None,
            "missing_details": [],
            "days": revised_days,
        }

    days: dict[str, object] = {}
    for day in (
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
    ):
        days[day] = {"available": False, "disciplines": [], "time_windows": []}
    days["tuesday"] = {
        "available": True,
        "disciplines": ["running"],
        "time_windows": [{"time_of_day": None, "duration_minutes": 60}],
    }
    return {
        "parse_status": "complete",
        "clarification_reason": None,
        "missing_details": [],
        "days": days,
    }


def _current_availability(
    messages: list[BaseMessage],
) -> dict[str, dict[str, object]] | None:
    marker = "weekly schedule:\n"
    suffix = "\n\nApply only the requested changes."
    for message in messages:
        content = message.content
        if not isinstance(content, str) or marker not in content:
            continue
        try:
            raw = content.split(marker, maxsplit=1)[1].split(suffix, maxsplit=1)[0]
            schedule = json.loads(raw)
            days = schedule.get("days")
            if isinstance(days, dict):
                return cast(
                    dict[str, dict[str, object]],
                    json.loads(json.dumps({"days": days})),
                )
        except (IndexError, json.JSONDecodeError):
            return None
    return None
