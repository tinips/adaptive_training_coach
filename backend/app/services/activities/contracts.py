"""Shared contracts for exact-identity workout imports."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from app.domain.enums import (
    ActivitySource,
    CyclingType,
    Discipline,
    HeartRateTemporalQuality,
    HikingType,
    RunningType,
    StrengthType,
    SwimmingEnvironment,
)
from app.schemas.workouts import PoolSwimmingDetailsData, StrengthExercise

ActivityUpsertOutcome = Literal["inserted", "updated", "unchanged"]


@dataclass(slots=True, frozen=True)
class HeartRateObservationData:
    """One normalized source measurement associated with a workout."""

    source: ActivitySource
    source_record_key: str
    source_name: str | None
    started_at: datetime
    ended_at: datetime
    beats_per_minute: float
    temporal_quality: HeartRateTemporalQuality


@dataclass(slots=True)
class ActivityImportData:
    """Normalized source data for one exactly identified workout."""

    source: ActivitySource
    external_id: str | None
    discipline: Discipline
    raw_sport: str
    title: str | None
    started_at: datetime
    duration_seconds: int
    raw_sub_sport: str | None = None
    notes: str | None = None
    ended_at: datetime | None = None
    timezone: str | None = None
    moving_duration_seconds: int | None = None
    distance_meters: float | None = None
    elevation_gain_meters: float | None = None
    elevation_loss_meters: float | None = None
    calories_kcal: float | None = None
    average_heart_rate: float | None = None
    max_heart_rate: float | None = None
    average_cadence: float | None = None
    max_cadence: float | None = None
    average_speed_kph: float | None = None
    max_speed_kph: float | None = None
    average_pace_seconds_per_km: float | None = None
    average_pace_seconds_per_100m: float | None = None
    running_type: RunningType | None = None
    cycling_type: CyclingType | None = None
    hiking_type: HikingType | None = None
    swimming_environment: SwimmingEnvironment | None = None
    pool_details: PoolSwimmingDetailsData | None = None
    strength_type: StrengthType | None = None
    session_focus: str | None = None
    exercises: tuple[StrengthExercise, ...] = ()
    activity_name: str | None = None
    activity_description: str | None = None
    route_points: tuple[dict[str, object], ...] = ()
    source_metadata: dict[str, object] = field(default_factory=dict)
    unsupported_metrics: dict[str, object] = field(default_factory=dict)
    heart_rate_observations: tuple[HeartRateObservationData, ...] = ()


class ActivitySourceConflictError(ValueError):
    """A provider key is already linked to another owned workout."""


class ActivityImportValidationError(ValueError):
    """An imported record cannot satisfy the canonical workout boundary."""


__all__ = [
    "ActivityImportData",
    "ActivityImportValidationError",
    "ActivitySourceConflictError",
    "ActivityUpsertOutcome",
    "HeartRateObservationData",
]
