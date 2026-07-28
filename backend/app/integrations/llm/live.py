"""Live OpenAI-compatible model integration through LangChain."""

from __future__ import annotations

from collections.abc import Mapping

from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, SecretStr

from app.domain.enums import OnboardingStep
from app.integrations.llm.models import (
    LLMConfigurationError,
    StructuredModelResponse,
    StructuredOutputSchema,
)
from app.observability.protocol import ProviderMode


class OpenAICompatibleOnboardingModel:
    """Lazy live adapter using LangChain structured-output support.

    Construction never requires a key, allowing deterministic bot/API paths to
    start normally. The key is checked only when a live free-text call occurs.
    """

    def __init__(
        self,
        *,
        api_key: SecretStr | None,
        model_name: str,
        base_url: str | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._api_key = api_key
        self._model_name = model_name
        self._base_url = base_url
        self._timeout_seconds = timeout_seconds
        self._chat_model: ChatOpenAI | None = None

    @property
    def provider_mode(self) -> ProviderMode:
        return "live"

    @property
    def model_name(self) -> str:
        return self._model_name

    def _get_chat_model(self) -> ChatOpenAI:
        if self._chat_model is not None:
            return self._chat_model
        if self._api_key is None or not self._api_key.get_secret_value():
            raise LLMConfigurationError("llm_api_key_missing")
        self._chat_model = ChatOpenAI(
            api_key=self._api_key,
            base_url=self._base_url,
            model=self._model_name,
            temperature=0,
            timeout=self._timeout_seconds,
            max_retries=1,
            extra_body={"thinking": {"type": "disabled"}},
        )
        return self._chat_model

    async def ainvoke_structured(
        self,
        *,
        step: OnboardingStep,
        schema: StructuredOutputSchema,
        messages: list[BaseMessage],
        config: RunnableConfig,
    ) -> StructuredModelResponse:
        del step
        model = self._get_chat_model()
        runnable = model.with_structured_output(
            schema,
            method="json_mode",
            include_raw=True,
        )
        result = await runnable.ainvoke(messages, config=config)
        if isinstance(result, BaseModel):
            return StructuredModelResponse(output=result)
        if not isinstance(result, Mapping):
            return StructuredModelResponse(output=result, malformed=True)

        parsed = result.get("parsed")
        parsing_error = result.get("parsing_error")
        raw = result.get("raw")
        prompt_tokens, completion_tokens = _token_usage(raw)
        return StructuredModelResponse(
            output=parsed,
            malformed=parsing_error is not None or parsed is None,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )


def _token_usage(raw: object) -> tuple[int | None, int | None]:
    if not isinstance(raw, AIMessage):
        return None, None
    usage = raw.usage_metadata
    if usage:
        return usage.get("input_tokens"), usage.get("output_tokens")
    token_usage = raw.response_metadata.get("token_usage")
    if not isinstance(token_usage, Mapping):
        return None, None
    prompt = token_usage.get("prompt_tokens")
    completion = token_usage.get("completion_tokens")
    return (
        prompt if isinstance(prompt, int) else None,
        completion if isinstance(completion, int) else None,
    )
