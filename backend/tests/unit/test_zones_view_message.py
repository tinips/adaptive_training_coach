"""Rendering tests for the read-only "view my zones" message."""

from __future__ import annotations

from app.bot.messages import zones_view
from app.services.athlete_zones import AthleteDisplayZones, ReferenceHeartRateZones
from app.services.weekly_planning.zones import power_zones, running_pace_zones


def test_zones_view_renders_hr_pace_and_power_with_min_sec() -> None:
    zones = AthleteDisplayZones(
        heart_rate=ReferenceHeartRateZones(
            estimated_max_hr_bpm=182.8,
            easy=(110, 137),
            moderate=(139, 155),
            hard=(157, 168),
        ),
        running=running_pace_zones(252.0),
        cycling=power_zones(260),
        swimming=None,
    )

    text = zones_view(zones)

    assert "182.8" in text or "183" in text
    assert "approximate" in text
    assert ":" in text  # pace rendered as min:sec somewhere
    assert "296" not in text  # never show a raw pace-in-seconds number
    assert "no numeric source" in text.lower()  # swimming has no baseline


def test_zones_view_notes_missing_birth_year() -> None:
    zones = AthleteDisplayZones(
        heart_rate=None, running=None, cycling=None, swimming=None
    )

    text = zones_view(zones)

    assert "birth year" in text.lower() or "profile" in text.lower()
