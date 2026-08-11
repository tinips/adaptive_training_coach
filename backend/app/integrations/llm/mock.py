"""Deterministic LangChain runnable used by local development and tests."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from enum import StrEnum
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.runnables import Runnable, RunnableConfig, RunnableLambda
from langchain_core.tools import BaseTool

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
    LOW_CONFIDENCE = "low_confidence"
    CLARIFICATION = "clarification"
    MALFORMED = "malformed"
    PROVIDER_FAILURE = "provider_failure"
    TIMEOUT = "timeout"


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
        del step
        user_text = _last_user_text(messages)
        scenario, value = self._resolve_scenario(user_text)

        async def respond(_: list[BaseMessage]) -> StructuredModelResponse:
            if scenario is FakeLLMScenario.PROVIDER_FAILURE:
                raise LLMProviderError("mock_provider_failure")
            if scenario is FakeLLMScenario.TIMEOUT:
                raise TimeoutError
            if scenario is FakeLLMScenario.MALFORMED:
                return StructuredModelResponse(
                    output={"confidence": "not-a-number"},
                )
            needs_clarification = scenario in {
                FakeLLMScenario.CLARIFICATION,
                FakeLLMScenario.LOW_CONFIDENCE,
            }
            if _is_free_text_validation_schema(schema):
                output = schema.model_validate(
                    {"accepted": not needs_clarification},
                )
            else:
                output = schema.model_validate(
                    _fake_goal_output(
                        value,
                        needs_clarification=needs_clarification,
                    )
                )
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

    def bind_tools(
        self,
        tools: Sequence[BaseTool],
    ) -> Runnable[Any, AIMessage]:
        """Provide deterministic tool calls for local onboarding modifications."""

        tool_names = {item.name for item in tools}

        async def respond(
            messages: list[BaseMessage],
            config: RunnableConfig,
        ) -> AIMessage:
            del config
            latest = messages[-1]
            if isinstance(latest, ToolMessage):
                if latest.name == "dispatch_telegram_input" and isinstance(
                    latest.content, str
                ):
                    decoded = json.loads(latest.content)
                    if isinstance(decoded, dict) and isinstance(
                        decoded.get("response_text"), str
                    ):
                        return AIMessage(content=decoded["response_text"])
                return AIMessage(
                    content="Your onboarding data has been updated successfully."
                )
            text = _last_user_text(messages)
            payload = _fake_onboarding_update(text)
            if "update_onboarding_data" in tool_names and payload:
                return AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "update_onboarding_data",
                            "args": payload,
                            "id": "mock-update-onboarding",
                            "type": "tool_call",
                        }
                    ],
                )
            if "dispatch_telegram_input" in tool_names:
                event_type = "text"
                if isinstance(latest, HumanMessage):
                    raw_event_type = latest.additional_kwargs.get("telegram_event_type")
                    if raw_event_type == "callback":
                        event_type = "callback"
                return AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "dispatch_telegram_input",
                            "args": {"event_type": event_type, "content": text},
                            "id": "mock-dispatch-telegram",
                            "type": "tool_call",
                        }
                    ],
                )
            return AIMessage(content="Tell me what onboarding data to change.")

        return RunnableLambda(respond, name="deterministic_onboarding_tool_agent")

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


def _fake_goal_output(
    user_text: str,
    *,
    needs_clarification: bool,
) -> dict[str, object]:
    return {
        "main_goal": user_text or None,
        "event_date": None,
        "target_outcome": None if needs_clarification else "Achieve the stated goal",
        "secondary_priority": None,
        "missing_fields": ["target_outcome"] if needs_clarification else [],
        "ambiguous_fields": [],
        "message_status": (
            "NEEDS_CLARIFICATION" if needs_clarification else "COMPLETE"
        ),
    }


def _is_free_text_validation_schema(schema: StructuredOutputSchema) -> bool:
    """Recognize the isolated validator without importing workflow modules."""

    return set(schema.model_fields) == {"accepted"}


def _fake_onboarding_update(user_text: str) -> dict[str, object]:
    folded = user_text.casefold()
    if "ironman 70.3" in folded and "decent time" in folded:
        return {
            "main_goal": "Finish an Ironman 70.3",
            "target_outcome": "Finish in a decent time",
        }
    payload: dict[str, object] = {}
    age = re.search(r"\bage(?:\s+(?:to|is))?\s+(\d{2,3})\b", folded)
    weight = re.search(
        r"\bweight(?:\s+(?:to|is))?\s+(\d{2,3}(?:\.\d+)?)\s*(?:kg)?\b",
        folded,
    )
    birth_year = re.search(
        r"\b(?:birth year|born)(?:\s+(?:to|is|in))?\s+((?:19|20)\d{2})\b",
        folded,
    )
    height = re.search(
        r"\bheight(?:\s+(?:to|is))?\s+(\d{3})\s*(?:cm)?\b",
        folded,
    )
    if age is not None:
        payload["age"] = int(age.group(1))
    if weight is not None:
        payload["weight_kg"] = float(weight.group(1))
    if birth_year is not None:
        payload["birth_year"] = int(birth_year.group(1))
    if height is not None:
        payload["height_cm"] = int(height.group(1))
    availability = _literal_value_after_label(user_text, "availability")
    if availability is not None:
        payload["availability_text"] = availability
    if re.search(
        r"\b(?:no|none)\b[^.\n]*(?:injur|limitation|restriction)",
        folded,
    ):
        payload["health_limitations_text"] = "NONE_REPORTED"
    else:
        limitations = _literal_value_after_label(user_text, "limitations")
        if limitations is None:
            limitations = _literal_value_after_label(user_text, "injuries")
        if limitations is None:
            limitations = _literal_value_after_label(user_text, "injury")
        if limitations is not None:
            payload["health_limitations_text"] = limitations
    return payload


def _literal_value_after_label(user_text: str, label: str) -> str | None:
    """Return the unmodified tail after an explicit conversational field label."""

    match = re.search(rf"\b{re.escape(label)}\b", user_text, flags=re.IGNORECASE)
    if match is None:
        return None
    value = user_text[match.end() :]
    value = re.sub(r"^\s*(?:is|to|are|:|=)\s*", "", value, flags=re.IGNORECASE)
    return value if value.strip() else None
