"""Normalized, persistence-neutral TCX import records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.domain.enums import Discipline


@dataclass(slots=True, frozen=True)
class ParsedTCXPosition:
    """One optional route position retained from a TCX trackpoint."""

    timestamp: datetime | None
    latitude_degrees: float
    longitude_degrees: float
    altitude_meters: float | None
    distance_meters: float | None


@dataclass(slots=True, frozen=True)
class ParsedTCXHeartRateObservation:
    """One timestamped TCX heart-rate trackpoint."""

    source_record_key: str
    timestamp: datetime
    beats_per_minute: float


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
    heart_rate_records_matched: int
    average_cadence: float | None
    cadence_sample_count: int
    route_positions: tuple[ParsedTCXPosition, ...]
    warnings: tuple[str, ...]
    raw_sub_sport: str | None = None
    elevation_loss_meters: float | None = None
    max_cadence: float | None = None
    heart_rate_observations: tuple[ParsedTCXHeartRateObservation, ...] = ()


__all__ = [
    "ParsedTCXActivity",
    "ParsedTCXHeartRateObservation",
    "ParsedTCXPosition",
]
