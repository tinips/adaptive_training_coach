"""The one seconds->min:sec pace renderer athlete-facing output must use."""

from __future__ import annotations

from app.services.formatting import format_pace_min_sec


def test_formats_running_pace_seconds_per_km_as_min_sec() -> None:
    assert format_pace_min_sec(296, unit_label="/km") == "4:56/km"


def test_formats_swimming_pace_seconds_per_100m_as_min_sec() -> None:
    assert format_pace_min_sec(95, unit_label="/100m") == "1:35/100m"


def test_pads_single_digit_seconds() -> None:
    assert format_pace_min_sec(305, unit_label="/km") == "5:05/km"


def test_rounds_fractional_seconds_before_formatting() -> None:
    assert format_pace_min_sec(296.6, unit_label="/km") == "4:57/km"


def test_never_renders_raw_seconds_without_a_colon() -> None:
    rendered = format_pace_min_sec(296, unit_label="/km")

    assert ":" in rendered
    assert rendered != "296/km"
