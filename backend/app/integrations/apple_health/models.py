"""Normalized, persistence-neutral Apple Health import records."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.domain.enums import Discipline, HeartRateTemporalQuality


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
    heart_rate_sample_count: int = 0
    heart_rate_quality: HeartRateTemporalQuality = HeartRateTemporalQuality.UNKNOWN
    heart_rate_reliable: bool = False


@dataclass(slots=True, frozen=True)
class ParsedAppleHealthExport:
    """Bounded parser output and safe counters."""

    workouts: tuple[ParsedWorkout, ...]
    workouts_found: int
    duplicate_workouts: int
    heart_rate_records_matched: int
    warnings: tuple[str, ...]
