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
    # running_pace_zones(252.0) yields easy=(277, 315) seconds/km; pinning the
    # min:sec rendering of the real lower bound proves format_pace_min_sec was
    # actually applied, not just that a colon appears somewhere in the message.
    assert "4:37/km" in text
    assert "277-315" not in text  # never show the raw seconds-per-km bounds
    assert "no numeric source" in text.lower()  # swimming has no baseline


def test_zones_view_notes_missing_birth_year() -> None:
    zones = AthleteDisplayZones(
        heart_rate=None, running=None, cycling=None, swimming=None
    )

    text = zones_view(zones)

    assert "birth year" in text.lower() or "profile" in text.lower()
