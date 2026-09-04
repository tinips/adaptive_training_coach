"""Deterministic intensity boundaries for the first-week probe."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import Discipline
from app.schemas.baseline import AthleteBaselineData
from app.schemas.fitness import BaselineCalculation


class ResolvedIntensityZones(BaseModel):
    """Known-safe first-week ranges, or an explicit RPE-only policy."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: Literal["NUMERIC", "RPE_FALLBACK"]
    metric: Literal[
        "RPE",
        "HEART_RATE_BPM",
        "POWER_WATTS",
        "PACE_SECONDS_PER_KM",
        "SWIM_PACE_SECONDS_PER_100M",
    ]
    easy: tuple[float, float] | None = None
    moderate: tuple[float, float] | None = None
    hard: tuple[float, float] | None = None
    guidance: str = Field(min_length=1, max_length=240)


def resolve_first_week_zones(
    *,
    baseline: AthleteBaselineData | None,
    calculations: dict[Discipline, BaselineCalculation | None],
    disciplines: tuple[Discipline, ...],
) -> dict[Discipline, ResolvedIntensityZones]:
    """Resolve onboarding thresholds without inventing a test or a threshold."""

    return {
        discipline: _resolve_discipline(
            discipline=discipline,
            baseline=baseline,
            calculation=calculations.get(discipline),
        )
        for discipline in disciplines
    }


def _resolve_discipline(
    *,
    discipline: Discipline,
    baseline: AthleteBaselineData | None,
    calculation: BaselineCalculation | None,
) -> ResolvedIntensityZones:
    if discipline is Discipline.CYCLING:
        ftp = (
            baseline.cycling.recent_ftp_watts if baseline and baseline.cycling else None
        )
        if ftp is not None:
            return _power_zones(ftp)
    if discipline is Discipline.RUNNING:
        race = (
            baseline.running.recent_race_result
            if baseline and baseline.running
            else None
        )
        if race is not None:
            return _running_pace_zones(race.duration_seconds / race.distance_km)
    if discipline is Discipline.SWIMMING:
        threshold = (
            baseline.swimming.recent_400m_seconds
            if baseline and baseline.swimming
            else None
        )
        if threshold is not None:
            return _swim_pace_zones(threshold / 4)
    if calculation is not None and calculation.reliable_max_hr_bpm is not None:
        return _heart_rate_zones(calculation.reliable_max_hr_bpm)
    return ResolvedIntensityZones(
        mode="RPE_FALLBACK",
        metric="RPE",
        guidance=(
            "No usable threshold is available: prescribe and record effort by RPE "
            "and breathing/feel, not pace, power, or heart-rate targets."
        ),
    )


def _power_zones(ftp: int) -> ResolvedIntensityZones:
    return ResolvedIntensityZones(
        mode="NUMERIC",
        metric="POWER_WATTS",
        easy=(round(ftp * 0.55), round(ftp * 0.75)),
        moderate=(round(ftp * 0.76), round(ftp * 0.90)),
        hard=(round(ftp * 0.91), round(ftp * 1.05)),
        guidance=(
            "Power ranges are derived from the reported FTP; no FTP test is needed."
        ),
    )


def _running_pace_zones(race_pace: float) -> ResolvedIntensityZones:
    return ResolvedIntensityZones(
        mode="NUMERIC",
        metric="PACE_SECONDS_PER_KM",
        easy=(round(race_pace * 1.10), round(race_pace * 1.25)),
        moderate=(round(race_pace * 0.98), round(race_pace * 1.09)),
        hard=(round(race_pace * 0.88), round(race_pace * 0.97)),
        guidance="Pace ranges are derived from the reported recent race result.",
    )


def _swim_pace_zones(threshold_pace: float) -> ResolvedIntensityZones:
    return ResolvedIntensityZones(
        mode="NUMERIC",
        metric="SWIM_PACE_SECONDS_PER_100M",
        easy=(round(threshold_pace * 1.10), round(threshold_pace * 1.25)),
        moderate=(round(threshold_pace * 0.98), round(threshold_pace * 1.09)),
        hard=(round(threshold_pace * 0.88), round(threshold_pace * 0.97)),
        guidance="Pace ranges are derived from the reported 400 m swim result.",
    )


def _heart_rate_zones(max_hr: float) -> ResolvedIntensityZones:
    return ResolvedIntensityZones(
        mode="NUMERIC",
        metric="HEART_RATE_BPM",
        easy=(round(max_hr * 0.60), round(max_hr * 0.75)),
        moderate=(round(max_hr * 0.76), round(max_hr * 0.85)),
        hard=(round(max_hr * 0.86), round(max_hr * 0.92)),
        guidance=(
            "Heart-rate ranges use reliable recent heart-rate evidence, not a test."
        ),
    )
