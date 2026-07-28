"""Deterministic LangChain runnable used by local development and tests."""

from __future__ import annotations

from enum import StrEnum

from langchain_core.messages import BaseMessage, HumanMessage
from langchain_core.runnables import RunnableConfig, RunnableLambda

from app.domain.enums import OnboardingStep
from app.integrations.llm.models import (
    EquipmentOnboardingOutput,
    HealthAreasOnboardingOutput,
    LLMProviderError,
    MultiValueOnboardingOutput,
    NumericOnboardingOutput,
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
            if scenario is FakeLLMScenario.CLARIFICATION:
                output = schema.model_validate(
                    {
                        "normalized_value": None,
                        "display_value": None,
                        "confidence": 0.55,
                        "requires_clarification": True,
                        "clarification_question": (
                            "Could you describe that in one more specific phrase?"
                        ),
                        "safety_flag": False,
                        "safety_reason": None,
                    }
                )
                return StructuredModelResponse(output=output)
            confidence = 0.35 if scenario is FakeLLMScenario.LOW_CONFIDENCE else 0.98
            normalized = _normalized_fake_value(step, schema, value)
            output = schema.model_validate(
                {
                    "normalized_value": normalized,
                    "display_value": value,
                    "confidence": confidence,
                    "requires_clarification": False,
                    "clarification_question": None,
                    "safety_flag": False,
                    "safety_reason": None,
                }
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


def _normalized_fake_value(
    step: OnboardingStep,
    schema: StructuredOutputSchema,
    user_text: str,
) -> str | int | float | list[str]:
    if issubclass(
        schema,
        (
            MultiValueOnboardingOutput,
            EquipmentOnboardingOutput,
            HealthAreasOnboardingOutput,
        ),
    ):
        return ["OTHER"]
    if issubclass(schema, NumericOnboardingOutput):
        try:
            return float(user_text)
        except ValueError:
            return 0
    folded = user_text.casefold().strip()
    if step is OnboardingStep.PRIMARY_SPORT:
        translations = {
            "running": "RUNNING",
            "correr": "RUNNING",
            "córrer": "RUNNING",
            "cycling": "CYCLING",
            "ciclismo": "CYCLING",
            "ciclisme": "CYCLING",
            "swimming": "SWIMMING",
            "natación": "SWIMMING",
            "natació": "SWIMMING",
            "triathlon": "TRIATHLON",
            "triatlón": "TRIATHLON",
            "triatló": "TRIATHLON",
        }
        return translations.get(folded, "OTHER")
    if step in {OnboardingStep.GOAL_TYPE, OnboardingStep.GOAL_PRIORITY}:
        return "OTHER"
    return user_text
