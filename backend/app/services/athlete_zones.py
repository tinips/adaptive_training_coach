"""Athlete-facing verification zones: HR (age-estimated), pace, and power.

Entirely separate from weekly_planning.zones's prescription-facing
ResolvedIntensityZones for heart rate: nothing here is read by the planner
or the prompt-building code. HR here is display-only; it can never become
a first-week prescription metric (see weekly_planning/zones.py, which no
longer has any code path that returns HEART_RATE_BPM at all).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from app.schemas.baseline import AthleteBaselineData
from app.services.weekly_planning.zones import (
    ResolvedIntensityZones,
    hr_zone_bands,
    power_zones,
    running_pace_zones,
    swim_pace_zones,
)

TANAKA_INTERCEPT = 208.0
TANAKA_AGE_COEFFICIENT = 0.7
HR_ZONE_CAVEAT = (
    "Age-estimated max heart rate is approximate (individual maxHR can vary "
    "±10-12 bpm) and will be refined from your observed workout heart "
    "rate over time."
)


class ReferenceHeartRateZones(BaseModel):
    """Age-estimated max HR and its easy/moderate/hard bands, display-only."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    estimated_max_hr_bpm: float
    easy: tuple[float, float]
    moderate: tuple[float, float]
    hard: tuple[float, float]
    caveat: str = HR_ZONE_CAVEAT


class AthleteDisplayZones(BaseModel):
    """Everything the "view my zones" command shows, independent of any plan."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    heart_rate: ReferenceHeartRateZones | None
    running: ResolvedIntensityZones | None
    cycling: ResolvedIntensityZones | None
    swimming: ResolvedIntensityZones | None


def estimate_max_hr_bpm(*, birth_year: int, current_year: int) -> float:
    """Tanaka formula: maxHR = 208 - 0.7 x age, age from whole birth years."""

    age = current_year - birth_year
    return TANAKA_INTERCEPT - TANAKA_AGE_COEFFICIENT * age


def resolve_reference_hr_zones(
    *, birth_year: int, current_year: int
) -> ReferenceHeartRateZones:
    """Age-based HR zones for verification/display only -- never prescribed."""

    max_hr = estimate_max_hr_bpm(birth_year=birth_year, current_year=current_year)
    easy, moderate, hard = hr_zone_bands(max_hr)
    return ReferenceHeartRateZones(
        estimated_max_hr_bpm=round(max_hr, 1),
        easy=easy,
        moderate=moderate,
        hard=hard,
    )


def resolve_athlete_display_zones(
    *,
    birth_year: int | None,
    baseline: AthleteBaselineData | None,
    current_year: int,
) -> AthleteDisplayZones:
    """Compose HR (age)/pace(race)/power(FTP)/swim-pace(400m), display only.

    Unlike the planner's zone resolver, this shows a zone whenever the
    matching baseline value exists, regardless of recent workout evidence --
    "view my zones" is explicitly plan-independent.
    """

    heart_rate = (
        resolve_reference_hr_zones(birth_year=birth_year, current_year=current_year)
        if birth_year is not None
        else None
    )
    race = (
        baseline.running.recent_race_result if baseline and baseline.running else None
    )
    running = (
        running_pace_zones(race.duration_seconds / race.distance_km)
        if race is not None
        else None
    )
    ftp = baseline.cycling.recent_ftp_watts if baseline and baseline.cycling else None
    cycling = power_zones(ftp) if ftp is not None else None
    threshold = (
        baseline.swimming.recent_400m_seconds
        if baseline and baseline.swimming
        else None
    )
    swimming = swim_pace_zones(threshold / 4) if threshold is not None else None
    return AthleteDisplayZones(
        heart_rate=heart_rate, running=running, cycling=cycling, swimming=swimming
    )


__all__ = [
    "AthleteDisplayZones",
    "ReferenceHeartRateZones",
    "estimate_max_hr_bpm",
    "resolve_athlete_display_zones",
    "resolve_reference_hr_zones",
]
