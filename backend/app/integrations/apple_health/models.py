"""Normalized, persistence-neutral Apple Health import records."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from app.domain.enums import Discipline, HeartRateTemporalQuality

type SwimmingEnvironmentHint = Literal["POOL", "OPEN_WATER"]


@dataclass(slots=True, frozen=True)
class ParsedHeartRateObservation:
    """A relevant Apple Health heart-rate record matched to one workout."""

    source_record_key: str
    source_name: str | None
    started_at: datetime
    ended_at: datetime
    beats_per_minute: float
    temporal_quality: HeartRateTemporalQuality


@dataclass(slots=True)
class ParsedWorkout:
    """One canonical workout ready for an ownership-scoped upsert."""

    source_record_key: str
    source_workout_type: str
    discipline: Discipline
    source_name: str | None
    source_version: str | None
    device: str | None
    started_at: datetime
    ended_at: datetime
    duration_seconds: int
    distance_meters: float | None
    calories_kcal: float | None
    observations: list[ParsedHeartRateObservation] = field(default_factory=list)
    average_heart_rate: float | None = None
    max_heart_rate: float | None = None
    source_metadata: dict[str, str] = field(default_factory=dict)
    raw_sub_sport: str | None = None
    swimming_environment: SwimmingEnvironmentHint | None = None
    pool_length_meters: float | None = None


@dataclass(slots=True, frozen=True)
class ParsedAppleHealthExport:
    """Bounded parser output and safe counters."""

    workouts: tuple[ParsedWorkout, ...]
    workouts_found: int
    duplicate_workouts: int
    heart_rate_records_matched: int
    warnings: tuple[str, ...]
