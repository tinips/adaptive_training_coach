"""Validated self-reported training baseline contracts."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import CoachingStyle, Discipline


class _BaselineSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RecentRaceResult(_BaselineSchema):
    """A recent running performance marker supplied by the athlete."""

    distance_km: float = Field(gt=0, le=250)
    duration_seconds: int = Field(gt=0, le=24 * 60 * 60)


class RunningBaseline(_BaselineSchema):
    """Minimal running evidence used to prescribe a conservative first week."""

    typical_weekly_sessions: int = Field(ge=0, le=14)
    typical_weekly_duration_minutes: int = Field(ge=0, le=24 * 60)
    longest_recent_run_minutes: int = Field(ge=0, le=24 * 60)
    recent_race_result: RecentRaceResult | None = None


class CyclingBaseline(_BaselineSchema):
    """Minimal cycling evidence plus the constraints that affect session type."""

    typical_weekly_sessions: int = Field(ge=0, le=14)
    typical_weekly_duration_minutes: int = Field(ge=0, le=24 * 60)
    longest_recent_ride_minutes: int = Field(ge=0, le=24 * 60)
    riding_environment: Literal["INDOOR", "OUTDOOR", "BOTH", "NONE"]
    riding_confidence: Literal[
        "NEW_RIDER",
        "SIMPLE_ROUTES",
        "CONFIDENT",
        "NOT_CURRENTLY_RIDING",
    ]
    recent_ftp_watts: int | None = Field(default=None, gt=0, le=1000)


class SwimmingBaseline(_BaselineSchema):
    """Minimal swimming evidence, including the safety-relevant swim setting."""

    typical_weekly_sessions: int = Field(ge=0, le=14)
    typical_weekly_duration_minutes: int = Field(ge=0, le=24 * 60)
    longest_continuous_swim_meters: int = Field(ge=0, le=100_000)
    swimming_environment: Literal["POOL", "OPEN_WATER", "BOTH", "NONE"]
    pool_length_meters: Literal[25, 50] | None = None
    recent_400m_seconds: int | None = Field(default=None, gt=0, le=60 * 60)


class TriathlonBaseline(_BaselineSchema):
    """Small triathlon-only context that cannot be inferred from one sport."""

    prior_experience: Literal["NONE", "SPRINT", "OLYMPIC", "LONG_COURSE"]
    weakest_discipline: Literal["RUNNING", "CYCLING", "SWIMMING", "NO_CLEAR_WEAKNESS"]
    open_water_confidence: Literal["NOT_CONFIDENT", "SOME_EXPERIENCE", "CONFIDENT"]


class TrainingPreferences(_BaselineSchema):
    """What the athlete wants, as distinct from current training volume."""

    coaching_style: CoachingStyle
    desired_weekly_sessions: dict[Discipline, Annotated[int, Field(ge=0, le=14)]] = (
        Field(default_factory=dict)
    )
    fits_availability: bool | None = None


class AthleteBaselineData(_BaselineSchema):
    """The current version of the goal-adaptive onboarding baseline."""

    running: RunningBaseline | None = None
    cycling: CyclingBaseline | None = None
    swimming: SwimmingBaseline | None = None
    triathlon: TriathlonBaseline | None = None
    preferences: TrainingPreferences | None = None


__all__ = [
    "AthleteBaselineData",
    "CyclingBaseline",
    "RecentRaceResult",
    "RunningBaseline",
    "SwimmingBaseline",
    "TrainingPreferences",
    "TriathlonBaseline",
]
