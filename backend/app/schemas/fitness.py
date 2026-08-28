"""Typed boundaries for deterministic workout-derived baseline evidence."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import (
    ActivitySource,
    Discipline,
    HeartRateTemporalQuality,
    SwimmingEnvironment,
)


class _FitnessSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class HeartRateEvidence(_FitnessSchema):
    """One retained HR observation, including its temporal reliability label."""

    started_at: datetime
    ended_at: datetime
    beats_per_minute: float = Field(gt=0)
    temporal_quality: HeartRateTemporalQuality


class FitnessWorkoutEvidence(_FitnessSchema):
    """Redacted, normalized workout facts available to the baseline calculator."""

    workout_id: UUID
    discipline: Discipline
    source: ActivitySource
    started_at: datetime
    duration_seconds: int = Field(gt=0)
    fitness_input_updated_at: datetime
    distance_meters: float | None = Field(default=None, ge=0)
    moving_duration_seconds: int | None = Field(default=None, ge=0)
    calories_kcal: float | None = Field(default=None, ge=0)
    subtype: str | None = None
    swimming_environment: SwimmingEnvironment | None = None
    elevation_gain_meters: float | None = Field(default=None, ge=0)
    average_cadence: float | None = Field(default=None, ge=0)
    structured_exercise_count: int | None = Field(default=None, ge=0)
    heart_rate_observations: tuple[HeartRateEvidence, ...] = ()
    coarse_heart_rate_present: bool = False


class BaselineCalculation(_FitnessSchema):
    """Persistence-ready evidence for one immutable discipline baseline."""

    discipline: Discipline
    analysis_started_at: datetime
    analysis_ended_at: datetime
    calculated_at: datetime
    session_count: int = Field(gt=0)
    active_day_count: int = Field(gt=0)
    active_dates: tuple[date, ...] = ()
    total_duration_seconds: int = Field(gt=0)
    known_distance_meters: float | None = Field(default=None, ge=0)
    distance_session_count: int = Field(ge=0)
    longest_duration_seconds: int = Field(gt=0)
    longest_distance_meters: float | None = Field(default=None, ge=0)
    total_calories_kcal: float | None = Field(default=None, ge=0)
    reliable_hr_sample_count: int = Field(ge=0)
    reliable_average_hr_bpm: float | None = Field(default=None, gt=0)
    reliable_max_hr_bpm: float | None = Field(default=None, gt=0)
    confidence: float = Field(ge=0, le=1)
    discipline_metrics_jsonb: dict[str, object]
    evidence_summary_jsonb: dict[str, object]
    quality_flags_jsonb: list[str]
    source_workout_through_at: datetime
    input_updated_through_at: datetime
    input_digest: str = Field(min_length=64, max_length=64)
    calculation_version: int = Field(ge=1)
