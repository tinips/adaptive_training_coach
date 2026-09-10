"""RPE-range-to-reference-HR-band mapping.

docs/decisions/locked.md, "Signal scoring, THRIVING, and volume status":
"planned session.intensity.rpe_range selects the evaluator's approximate HR
reference band: an entire range in 1-4 selects easy, 5-6 selects moderate,
and 7-10 selects hard. A range crossing those boundaries is NOT_COMPARABLE
for HR. Purpose, execution, guidance, titles, and notes are never parsed."
"""

from __future__ import annotations

from typing import Literal

from app.services.athlete_zones import ReferenceHeartRateZones
from app.services.weekly_evaluation import constants as c

ReferenceHrBandName = Literal["EASY", "MODERATE", "HARD"]


def resolve_reference_hr_band_name(
    rpe_range: tuple[int, int],
) -> ReferenceHrBandName | None:
    """The band name for a planned RPE range, or None if it crosses bands."""

    lower, upper = rpe_range
    if c.RPE_BAND_EASY_RANGE[0] <= lower and upper <= c.RPE_BAND_EASY_RANGE[1]:
        return "EASY"
    if c.RPE_BAND_MODERATE_RANGE[0] <= lower and upper <= c.RPE_BAND_MODERATE_RANGE[1]:
        return "MODERATE"
    if c.RPE_BAND_HARD_RANGE[0] <= lower and upper <= c.RPE_BAND_HARD_RANGE[1]:
        return "HARD"
    return None


def resolve_reference_hr_band(
    *, rpe_range: tuple[int, int], zones: ReferenceHeartRateZones
) -> tuple[ReferenceHrBandName, tuple[float, float]] | None:
    """The band name plus its bpm range, or None if the rpe_range is ambiguous."""

    name = resolve_reference_hr_band_name(rpe_range)
    if name is None:
        return None
    band = {"EASY": zones.easy, "MODERATE": zones.moderate, "HARD": zones.hard}[name]
    return name, band
