"""Configuration safeguards for the live screenshot vision client."""

from __future__ import annotations

import io

from PIL import Image
from pydantic import SecretStr

from app.integrations.llm import vision


def test_deepseek_screenshot_extraction_disables_thinking_for_structured_output(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_chat_model(**kwargs: object) -> object:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(vision, "ChatOpenAI", fake_chat_model)
    extractor = vision.DeepSeekWorkoutScreenshotExtractor(
        api_key=SecretStr("test-key"),
        base_url="https://api.deepseek.com",
        model_name="deepseek-v4-flash-vision-exp",
    )

    extractor._get_chat_model()

    assert captured["extra_body"] == {"thinking": {"type": "disabled"}}


def test_screenshot_extraction_adds_a_centre_detail_crop() -> None:
    image = Image.new("RGB", (576, 1280), color="black")
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG")

    parts = vision.DeepSeekWorkoutScreenshotExtractor._image_parts(
        buffer.getvalue(),
        "image/jpeg",
    )

    assert len(parts) == 2
    assert parts[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_extraction_prompt_covers_pace_power_speed_and_cadence() -> None:
    prompt = vision._EXTRACTION_PROMPT

    assert "average_pace_seconds_per_km" in prompt
    assert "average_pace_seconds_per_100m" in prompt
    assert "average_power_watts" in prompt
    assert "average_speed_kph" in prompt
    assert "average_cadence" in prompt
    assert "static bike" in prompt.lower() or "smart trainer" in prompt.lower()
    assert "treadmill" in prompt.lower()
