"""Vision extraction of a workout summary screenshot into structured data.

Separate from the onboarding model in ``live.py``: that adapter's structured
output method is shaped around ``OnboardingStep`` and a LangGraph message
history it doesn't need here, and it defaults to the text-only DeepSeek
model. This is a plain one-shot call: one image in, one validated
``ManualWorkoutImportRequest`` out. See ``adapters/manual_screenshot.py``
for what happens to that object next.
"""

from __future__ import annotations

import base64
import io
from datetime import datetime
from typing import Any

from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from app.schemas.manual_import import ManualWorkoutImportRequest


class ScreenshotExtractionError(RuntimeError):
    """The model produced no usable workout, or credentials are missing."""


_EXTRACTION_PROMPT = """\
This image is a screenshot of a single workout summary from a fitness app. Extract \
every metric visible on it into the given schema.

The current local date is {current_date}. Resolve relative labels such as \
"Today", "Aujourd'hui", "Hoy", or their localized equivalent to that date. \
If the screenshot gives a full date, always prefer it. Extract the displayed \
start time as well; the workout end time is derived from duration_seconds and \
must not be guessed or stored separately.

Rules:
- If the image shows more than one workout card, extract only the most \
prominent or first one.
- discipline must be exactly "RUNNING", "CYCLING", "SWIMMING", or \
"STRENGTH" - infer it \
from the activity name/icon shown, even if the label is in another \
language (e.g. "Natation" or "Nage" is SWIMMING, "Course" or "Running" is \
RUNNING, "Cyclisme" or "Vélo" is CYCLING).
- Map gym, weights, bodybuilding, physique, musculation, resistance, or \
strength training to STRENGTH. If the screenshot has both a title and an icon, \
prefer the title. Do not classify a generic activity as STRENGTH without one \
of those strength signals.
- source_app_name is the app or brand shown on the card (e.g. "Mi Fitness", \
"Strava").
- Parse localized dates and month abbreviations before falling back to a \
relative date: for example, Spanish "31 ago." means 31 August, and French \
"31 août" means 31 August. When a displayed date has a day and month but no \
year, use the current year unless that would put the workout in the future. \
Always preserve the displayed start time (for example, "8:58"). Never use the \
phone's capture time as the workout time.
- started_at is the date/time shown, as a full ISO 8601 timestamp. If no \
timezone is shown, use the literal offset "+00:00".
- duration_seconds is the total training-time duration shown, converted to \
whole seconds.
- calories_active_kcal and calories_total_kcal: many apps show both an \
"active"/"exercise" calorie figure and a separate "total" figure - map each \
to its own field. If the card shows only one calorie number, put it in \
calories_active_kcal and leave calories_total_kcal empty.
- Only extract a calorie value when the image explicitly labels it as calories \
or kcal. Do not treat points, moves, steps, speed, or a device score as \
calories (for example, "945 Moves" is not a calorie value).
- For SWIMMING only, fill the swimming object: environment is "POOL" unless \
the card explicitly says open water; total_lengths and total_strokes come \
from whatever "lengths"/"laps" and "strokes" counters are shown; \
primary_stroke is the named stroke style, mapped to one of FREESTYLE, \
BREASTSTROKE, BACKSTROKE, BUTTERFLY, MIXED, OTHER (e.g. "Nage libre" or \
"Freestyle" is FREESTYLE).
- Leave any field the image does not show empty rather than guessing a \
value.
- average_pace_seconds_per_km (RUNNING only): if a running pace is shown \
(e.g. "5:30/km" or "5:30 min/km"), convert it to whole seconds per \
kilometre (5:30 -> 330). Leave empty if no pace is shown.
- average_pace_seconds_per_100m (SWIMMING only): if a swim pace is shown \
(e.g. "1:45/100m"), convert it to whole seconds per 100 metres (1:45 -> \
105). Leave empty if no pace is shown.
- average_speed_kph, max_speed_kph, average_power_watts, max_power_watts \
(CYCLING only): extract directly whenever the screen shows speed in km/h \
or power in watts, which is common on a smart trainer or static bike \
display. Static bike screens are this athlete's primary equipment: read \
their watts and speed fields especially carefully when present. Leave any \
of these empty if not shown.
- average_cadence, max_cadence (RUNNING or CYCLING): extract steps-per-\
minute (running) or revolutions-per-minute (cycling) cadence if shown. \
Treadmill screens are this athlete's primary running equipment: read \
their pace field especially carefully when present. Leave empty if not \
shown.
"""

_CALORIE_AUDIT_PROMPT = """\
Audit only the calorie values in this workout screenshot. A calorie value must
have an adjacent label explicitly saying kcal, calories, Cal, or kcalories.
Values labelled moves, steps, speed, distance, time, incline, pace, points, or
a score are never calories and must be null. Return active and total calories
separately only when their labels make that distinction explicit. If exactly
one unqualified calorie value is visible, return it as active calories and
leave total calories null.
"""


class _CalorieAudit(BaseModel):
    """Narrow verification response for a metric frequently confused on consoles."""

    model_config = ConfigDict(extra="forbid")

    calories_active_kcal: float | None = Field(default=None, ge=0)
    calories_total_kcal: float | None = Field(default=None, ge=0)


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
        client_options: dict[str, object] = {}
        if self._base_url is not None and "deepseek.com" in self._base_url.lower():
            # DeepSeek V4 enables thinking by default, but its chat-completions
            # endpoint rejects the forced tool choice that structured output
            # needs while thinking is active.
            client_options["extra_body"] = {"thinking": {"type": "disabled"}}
        self._chat_model = ChatOpenAI(
            api_key=self._api_key,
            base_url=self._base_url,
            model=self._model_name,
            temperature=0,
            timeout=self._timeout_seconds,
            max_retries=1,
            **client_options,
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
        prompt = _EXTRACTION_PROMPT.format(
            current_date=datetime.now().astimezone().date().isoformat()
        )
        message = HumanMessage(
            content=[
                *self._image_parts(image_bytes, image_media_type),
                {"type": "text", "text": prompt},
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
            request = result
        else:
            try:
                request = ManualWorkoutImportRequest.model_validate(result)
            except ValidationError as error:
                raise ScreenshotExtractionError("vision_output_invalid") from error
        return await self._audit_calories(
            model=model,
            image_bytes=image_bytes,
            image_media_type=image_media_type,
            request=request,
        )

    async def _audit_calories(
        self,
        *,
        model: ChatOpenAI,
        image_bytes: bytes,
        image_media_type: str,
        request: ManualWorkoutImportRequest,
    ) -> ManualWorkoutImportRequest:
        """Reject console scores that an initial broad extraction calls calories."""

        if request.calories_active_kcal is None and request.calories_total_kcal is None:
            return request
        audit_message = HumanMessage(
            content=[
                *self._image_parts(image_bytes, image_media_type),
                {"type": "text", "text": _CALORIE_AUDIT_PROMPT},
            ]
        )
        try:
            audit = await model.with_structured_output(
                _CalorieAudit,
                method="function_calling",
            ).ainvoke([audit_message])
        except Exception:
            # A successful initial extraction is still useful if the optional
            # narrow audit cannot be obtained (for example, a transient API
            # problem).
            return request
        if not isinstance(audit, _CalorieAudit):
            try:
                audit = _CalorieAudit.model_validate(audit)
            except ValidationError:
                return request
        return request.model_copy(
            update={
                "calories_active_kcal": audit.calories_active_kcal,
                "calories_total_kcal": audit.calories_total_kcal,
            }
        )

    @staticmethod
    def _image_parts(
        image_bytes: bytes,
        image_media_type: str,
    ) -> list[dict[str, Any]]:
        """Attach the original plus a readable centre crop when possible."""

        parts = [
            {
                "type": "image_url",
                "image_url": {
                    "url": (
                        f"data:{image_media_type};base64,"
                        f"{base64.standard_b64encode(image_bytes).decode('ascii')}"
                    )
                },
            }
        ]
        detail_crop = _centre_detail_crop(image_bytes)
        if detail_crop is not None:
            parts.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": (
                            "data:image/jpeg;base64,"
                            f"{base64.standard_b64encode(detail_crop).decode('ascii')}"
                        )
                    },
                }
            )
        return parts


def _centre_detail_crop(image_bytes: bytes) -> bytes | None:
    """Return a magnified centre band to make small treadmill consoles legible."""

    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            if image.width < 200 or image.height < 300:
                return None
            top = round(image.height * 0.30)
            bottom = round(image.height * 0.75)
            crop = image.crop((0, top, image.width, bottom)).convert("RGB")
    except (OSError, UnidentifiedImageError):
        return None
    buffer = io.BytesIO()
    crop.save(buffer, format="JPEG", quality=95)
    return buffer.getvalue()


__all__ = ["DeepSeekWorkoutScreenshotExtractor", "ScreenshotExtractionError"]
