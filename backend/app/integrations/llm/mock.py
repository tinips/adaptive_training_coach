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
                else _fake_weekly_prescription(_planner_request(messages))
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


def _planner_request(messages: list[BaseMessage]) -> str:
    """Find the original planner JSON when a repair instruction is appended."""

    for message in reversed(messages):
        if not isinstance(message, HumanMessage) or not isinstance(
            message.content, str
        ):
            continue
        try:
            value = json.loads(message.content)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and "week_start" in value:
            return message.content
    return _last_user_text(messages)


def _fake_weekly_prescription(request_json: str) -> dict[str, object]:
    """Return stable unscheduled session intents for local planner tests."""

    from datetime import date

    request = json.loads(request_json)
    week_start = date.fromisoformat(str(request["week_start"]))
    goal = request.get("goal")
    contexts = goal.get("target_contexts", []) if isinstance(goal, dict) else []
    planned_disciplines = request.get("planned_disciplines", [])
    discipline = (
        str(contexts[0].get("discipline", "OTHER"))
        if isinstance(contexts, list) and contexts and isinstance(contexts[0], dict)
        else (
            str(planned_disciplines[0])
            if isinstance(planned_disciplines, list) and planned_disciplines
            else "OTHER"
        )
    )
    is_first_week = request.get("planner_mode") == "FIRST_WEEK"
    session = {
        "discipline": discipline,
        "purpose": "Build easy aerobic consistency.",
        "objective": "Build consistent aerobic training",
        "intensity": {
            "metric": "RPE",
            "target_range": [2, 3],
            "rpe_range": [2, 3],
            "guidance": "Easy, conversational effort.",
        },
        "targets": {"duration_minutes": 45, "rpe": 3},
        "execution": "Easy warm-up, steady main set, easy cool-down.",
    }
    if is_first_week:
        return {
            "week_start": week_start.isoformat(),
            "sessions": _fake_first_week_sessions(request),
            "guardrails": [],
            "logging_instructions": [],
            "tests": [],
        }
    scheduled_session = {**session, "priority": "ESSENTIAL", "can_share_day": True}
    return {
        "week_start": week_start.isoformat(),
        "sessions": [scheduled_session for _ in range(3)],
    }


def _fake_first_week_sessions(request: dict[str, object]) -> list[dict[str, object]]:
    """Return varied, tier-aware menus so local planning tests use the model path."""

    preferences = request.get("preferences")
    desired_by_discipline = (
        preferences.get("desired_weekly_sessions", {})
        if isinstance(preferences, dict)
        and isinstance(preferences.get("desired_weekly_sessions"), dict)
        else {}
    )
    tiers = request.get("first_week_baseline_tiers")
    tier_by_discipline = tiers if isinstance(tiers, dict) else {}
    zones = request.get("resolved_intensity_zones")
    zones_by_discipline = zones if isinstance(zones, dict) else {}
    disciplines = request.get("planned_disciplines")
    planned = disciplines if isinstance(disciplines, list) else []
    sessions: list[dict[str, object]] = []
    for raw_discipline in planned:
        if not isinstance(raw_discipline, str):
            continue
        tier = str(tier_by_discipline.get(raw_discipline, "UNPREPARED"))
        desired = desired_by_discipline.get(raw_discipline, 1)
        count = desired if isinstance(desired, int) and desired > 0 else 1
        if tier == "UNPREPARED":
            count = 1
        zone = zones_by_discipline.get(raw_discipline)
        for index in range(count):
            sessions.append(
                _fake_first_week_session(
                    discipline=raw_discipline,
                    index=index,
                    tier=tier,
                    zone=zone if isinstance(zone, dict) else None,
                )
            )
    return sessions or [
        {
            "discipline": "OTHER",
            "purpose": "Build gentle familiarity.",
            "objective": "Complete a short, easy session.",
            "intensity": {
                "metric": "RPE",
                "target_range": [2, 3],
                "rpe_range": [2, 3],
                "guidance": "Easy, conversational effort.",
            },
            "targets": {"duration_minutes": 30, "rpe": 3},
            "execution": "Keep it relaxed and finish with plenty in reserve.",
        }
    ]


def _fake_first_week_session(
    *,
    discipline: str,
    index: int,
    tier: str,
    zone: dict[str, object] | None,
) -> dict[str, object]:
    controlled = (
        zone is not None
        and zone.get("mode") == "NUMERIC"
        and index == 1
        and tier in {"DEVELOPING", "TRAINED", "WELL_TRAINED"}
        and isinstance(zone.get("moderate"), list)
    )
    if controlled:
        assert zone is not None
        intensity = {
            "metric": zone["metric"],
            "target_range": zone["moderate"],
            "rpe_range": [5, 6],
            "guidance": "Controlled tempo in the resolved zone; finish with reserve.",
        }
        purpose = "Characterize controlled tempo."
        objective = "Record how a sustained controlled effort feels today."
        execution = "Warm up easily, hold a controlled tempo, then cool down."
    elif (
        zone is not None
        and zone.get("mode") == "NUMERIC"
        and isinstance(zone.get("easy"), list)
    ):
        intensity = {
            "metric": zone["metric"],
            "target_range": zone["easy"],
            "rpe_range": [2, 4],
            "guidance": "Stay within the resolved easy zone.",
        }
        purpose, objective, execution = _fake_easy_role(index)
    else:
        intensity = {
            "metric": "RPE",
            "target_range": [2, 3],
            "rpe_range": [2, 3],
            "guidance": "Easy, conversational effort guided by feel.",
        }
        purpose, objective, execution = _fake_easy_role(index)
    rpe_range = intensity["rpe_range"]
    assert isinstance(rpe_range, list) and isinstance(rpe_range[1], int)
    targets: dict[str, int] = {"duration_minutes": 45}
    if discipline != "STRENGTH":
        targets["rpe"] = rpe_range[1]
    return {
        "discipline": discipline,
        "purpose": purpose,
        "objective": objective,
        "intensity": intensity,
        "targets": targets,
        "execution": execution,
    }


def _fake_easy_role(index: int) -> tuple[str, str, str]:
    roles = (
        (
            "Establish aerobic baseline.",
            "Complete relaxed aerobic work and record how it feels.",
            "Keep breathing comfortable and finish with plenty in reserve.",
        ),
        (
            "Practice relaxed movement economy.",
            "Notice form and breathing at an easy effort.",
            "Stay conversational and use smooth, repeatable movement.",
        ),
        (
            "Build low-stress consistency.",
            "Finish the easy work feeling able to do more.",
            "Keep the effort relaxed and stop if symptoms worsen.",
        ),
    )
    purpose, objective, execution = roles[index % len(roles)]
    if index < len(roles):
        return purpose, objective, execution
    return (
        f"{purpose} Session variation {index + 1}.",
        f"{objective} This is variation {index + 1}.",
        execution,
    )


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
