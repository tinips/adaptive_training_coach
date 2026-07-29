"""Normalized, persistence-neutral TCX import records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from app.domain.enums import Discipline, HeartRateTemporalQuality

type HeartRateProvenance = Literal[
    "MEASURED_SENSOR",
    "PROVIDER_SUMMARY",
    "UNAVAILABLE",
]


@dataclass(slots=True, frozen=True)
class ParsedTCXPosition:
    """One optional route position retained from a TCX trackpoint."""

    timestamp: datetime | None
    latitude_degrees: float
    longitude_degrees: float
    altitude_meters: float | None
    distance_meters: float | None


@dataclass(slots=True, frozen=True)
class ParsedTCXActivity:
    """One normalized TCX activity ready for ownership-scoped persistence."""

    source_record_key: str
    activity_id: str | None
    source_sport_type: str
    discipline: Discipline
    started_at: datetime | None
    ended_at: datetime | None
    duration_seconds: int | None
    distance_meters: float | None
    calories_kcal: float | None
    elevation_gain_meters: float | None
    minimum_altitude_meters: float | None
    maximum_altitude_meters: float | None
    average_heart_rate: float | None
    max_heart_rate: float | None
    heart_rate_sample_count: int
    heart_rate_quality: HeartRateTemporalQuality
    heart_rate_reliable: bool
    heart_rate_provenance: HeartRateProvenance
    average_cadence: float | None
    cadence_sample_count: int
    route_positions: tuple[ParsedTCXPosition, ...]
    warnings: tuple[str, ...]


__all__ = [
    "HeartRateProvenance",
    "ParsedTCXActivity",
    "ParsedTCXPosition",
]
