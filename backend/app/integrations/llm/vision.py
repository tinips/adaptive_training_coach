"""Vision extraction of a workout summary screenshot into structured data.

Separate from the onboarding model in ``live.py``: that adapter's structured
output method is shaped around ``OnboardingStep`` and a LangGraph message
history it doesn't need here, and it defaults to the text-only DeepSeek
model. This is a plain one-shot call: one image in, one validated
``ManualWorkoutImportRequest`` out. See ``adapters/manual_screenshot.py``
for what happens to that object next, and the mobile HealthKit sync path's
known gaps for why this exists at all (see `_heart_rate_summary` there and
this module's own docstring intent).
"""

from __future__ import annotations

import base64

from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from pydantic import SecretStr, ValidationError

from app.schemas.manual_import import ManualWorkoutImportRequest


class ScreenshotExtractionError(RuntimeError):
    """The model produced no usable workout, or credentials are missing."""


_EXTRACTION_PROMPT = """\
This image is a screenshot of a single workout summary from a fitness app \
(for example Apple Health, or the source app's own detail screen). Extract \
every metric visible on it into the given schema.

Rules:
- If the image shows more than one workout card, extract only the most \
prominent or first one.
- discipline must be exactly "RUNNING", "CYCLING", or "SWIMMING" - infer it \
from the activity name/icon shown, even if the label is in another \
language (e.g. "Natation" or "Nage" is SWIMMING, "Course" or "Running" is \
RUNNING, "Cyclisme" or "Vélo" is CYCLING).
- source_app_name is the app or brand shown on the card (e.g. "Mi Fitness", \
"Apple Health", "Strava").
- started_at is the date/time shown, as a full ISO 8601 timestamp. If no \
timezone is shown, use the literal offset "+00:00".
- duration_seconds is the total training-time duration shown, converted to \
whole seconds.
- calories_active_kcal and calories_total_kcal: many apps show both an \
"active"/"exercise" calorie figure and a separate "total" figure - map each \
to its own field. If the card shows only one calorie number, put it in \
calories_active_kcal and leave calories_total_kcal empty.
- For SWIMMING only, fill the swimming object: environment is "POOL" unless \
the card explicitly says open water; total_lengths and total_strokes come \
from whatever "lengths"/"laps" and "strokes" counters are shown; \
primary_stroke is the named stroke style, mapped to one of FREESTYLE, \
BREASTSTROKE, BACKSTROKE, BUTTERFLY, MIXED, OTHER (e.g. "Nage libre" or \
"Freestyle" is FREESTYLE).
- Leave any field the image does not show empty rather than guessing a \
value.
"""


class DeepSeekWorkoutScreenshotExtractor:
    """Reads one workout screenshot and returns a validated import request.

    Construction never requires a key, mirroring
    ``OpenAICompatibleOnboardingModel`` - the key is checked only when
    ``extract`` is actually called, so a missing configuration never breaks
    process startup.
    """

    def __init__(
        self,
        *,
        api_key: SecretStr | None,
        model_name: str,
        base_url: str | None = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        self._api_key = api_key
        self._model_name = model_name
        self._base_url = base_url
        self._timeout_seconds = timeout_seconds
        self._chat_model: ChatOpenAI | None = None

    def _get_chat_model(self) -> ChatOpenAI:
        if self._chat_model is not None:
            return self._chat_model
        if self._api_key is None or not self._api_key.get_secret_value():
            raise ScreenshotExtractionError("llm_api_key_missing")
        self._chat_model = ChatOpenAI(
            api_key=self._api_key,
            base_url=self._base_url,
            model=self._model_name,
            temperature=0,
            timeout=self._timeout_seconds,
            max_retries=1,
        )
        return self._chat_model

    async def extract(
        self,
        *,
        image_bytes: bytes,
        image_media_type: str = "image/jpeg",
    ) -> ManualWorkoutImportRequest:
        """Return one validated workout, or raise ``ScreenshotExtractionError``."""

        model = self._get_chat_model()
        encoded = base64.standard_b64encode(image_bytes).decode("ascii")
        message = HumanMessage(
            content=[
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{image_media_type};base64,{encoded}"},
                },
                {"type": "text", "text": _EXTRACTION_PROMPT},
            ]
        )
        runnable = model.with_structured_output(
            ManualWorkoutImportRequest,
            method="function_calling",
        )
        try:
            result = await runnable.ainvoke([message])
        except Exception as error:
            raise ScreenshotExtractionError("vision_call_failed") from error

        if isinstance(result, ManualWorkoutImportRequest):
            return result
        try:
            return ManualWorkoutImportRequest.model_validate(result)
        except ValidationError as error:
            raise ScreenshotExtractionError("vision_output_invalid") from error


__all__ = ["DeepSeekWorkoutScreenshotExtractor", "ScreenshotExtractionError"]
