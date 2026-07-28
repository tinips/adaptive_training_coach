"""Validated Strava provider and application boundary models."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.enums import (
    ActivitySource,
    Discipline,
    WebhookAspectType,
    WebhookObjectType,
)


def normalize_scopes(value: str | Sequence[str] | None) -> frozenset[str]:
    """Normalize comma/space-delimited or repeated OAuth scope values."""

    if value is None:
        return frozenset()
    raw_values = [value] if isinstance(value, str) else value
    scopes: set[str] = set()
    for raw in raw_values:
        for part in raw.replace(" ", ",").split(","):
            normalized = part.strip()
            if normalized:
                scopes.add(normalized)
    return frozenset(scopes)


class StravaAthlete(BaseModel):
    """Minimal athlete identity returned during token exchange."""

    model_config = ConfigDict(extra="ignore")

    id: int


class StravaTokenResponse(BaseModel):
    """OAuth token response without provider-specific data leakage."""

    model_config = ConfigDict(extra="ignore")

    token_type: str = "Bearer"
    access_token: str = Field(min_length=1)
    refresh_token: str = Field(min_length=1)
    expires_at: int
    expires_in: int | None = None
    athlete: StravaAthlete | None = None
    scope: str | None = None

    @property
    def expires_at_datetime(self) -> datetime:
        """Return expiry as a timezone-aware UTC timestamp."""

        return datetime.fromtimestamp(self.expires_at, tz=UTC)


class RateLimitPair(BaseModel):
    """Short-term and daily values encoded in one Strava rate header."""

    short_term: int
    daily: int

    @classmethod
    def parse(cls, raw: str | None) -> RateLimitPair | None:
        """Parse a ``short,daily`` header, returning ``None`` if absent/invalid."""

        if raw is None:
            return None
        try:
            short_term, daily = (int(item.strip()) for item in raw.split(",", 1))
        except (TypeError, ValueError):
            return None
        if short_term < 0 or daily < 0:
            return None
        return cls(short_term=short_term, daily=daily)


class StravaRateLimits(BaseModel):
    """Snapshot parsed from all four documented Strava rate headers."""

    overall_limit: RateLimitPair | None = None
    overall_usage: RateLimitPair | None = None
    read_limit: RateLimitPair | None = None
    read_usage: RateLimitPair | None = None

    @classmethod
    def from_headers(cls, headers: Mapping[str, str]) -> StravaRateLimits:
        """Parse response headers case-insensitively."""

        lowered = {key.lower(): value for key, value in headers.items()}
        return cls(
            overall_limit=RateLimitPair.parse(lowered.get("x-ratelimit-limit")),
            overall_usage=RateLimitPair.parse(lowered.get("x-ratelimit-usage")),
            read_limit=RateLimitPair.parse(lowered.get("x-readratelimit-limit")),
            read_usage=RateLimitPair.parse(lowered.get("x-readratelimit-usage")),
        )

    def is_near_limit(self, *, usage_ratio: float = 0.9) -> bool:
        """Return whether any known short/daily quota reached the safe threshold."""

        if not 0 < usage_ratio <= 1:
            raise ValueError("usage_ratio must be in (0, 1].")
        pairs = (
            (self.overall_usage, self.overall_limit),
            (self.read_usage, self.read_limit),
        )
        for usage, limit in pairs:
            if usage is None or limit is None:
                continue
            if limit.short_term and usage.short_term / limit.short_term >= usage_ratio:
                return True
            if limit.daily and usage.daily / limit.daily >= usage_ratio:
                return True
        return False


_RUN_TYPES = {"Run", "TrailRun", "VirtualRun"}
_RIDE_TYPES = {
    "Ride",
    "MountainBikeRide",
    "GravelRide",
    "EBikeRide",
    "EMountainBikeRide",
    "Velomobile",
    "VirtualRide",
}
_SWIM_TYPES = {"Swim"}
_STRENGTH_TYPES = {
    "Crossfit",
    "WeightTraining",
    "Workout",
    "HighIntensityIntervalTraining",
}
_WALK_HIKE_TYPES = {"Walk", "Hike", "Snowshoe"}


def discipline_for_sport_type(sport_type: str) -> Discipline:
    """Map Strava's evolving sport taxonomy into stable product disciplines."""

    if sport_type in _RUN_TYPES:
        return Discipline.RUN
    if sport_type in _RIDE_TYPES:
        return Discipline.RIDE
    if sport_type in _SWIM_TYPES:
        return Discipline.SWIM
    if sport_type in _STRENGTH_TYPES:
        return Discipline.STRENGTH
    if sport_type in _WALK_HIKE_TYPES:
        return Discipline.WALK_HIKE
    return Discipline.OTHER


class NormalizedStravaActivity(BaseModel):
    """Provider-neutral fields ready for a user-owned activity upsert."""

    source: ActivitySource = ActivitySource.STRAVA
    external_id: str
    sport: Discipline
    source_sport_type: str
    name: str
    started_at: datetime
    timezone: str | None = None
    duration_seconds: int
    moving_time_seconds: int | None = None
    distance_meters: float | None = None
    elevation_gain_meters: float | None = None
    average_heart_rate: float | None = None
    max_heart_rate: float | None = None
    average_speed: float | None = None
    average_watts: float | None = None
    trainer: bool = False
    commute: bool = False
    manual: bool = False
    raw_summary: dict[str, object] | None = None


class StravaActivitySummary(BaseModel):
    """Activity summary returned by ``GET /athlete/activities``."""

    model_config = ConfigDict(extra="ignore")

    id: int
    name: str = ""
    sport_type: str | None = None
    type: str | None = None
    start_date: datetime
    timezone: str | None = None
    elapsed_time: int
    moving_time: int | None = None
    distance: float | None = None
    total_elevation_gain: float | None = None
    average_heartrate: float | None = None
    max_heartrate: float | None = None
    average_speed: float | None = None
    average_watts: float | None = None
    trainer: bool = False
    commute: bool = False
    manual: bool = False

    @field_validator("start_date")
    @classmethod
    def require_aware_start(cls, value: datetime) -> datetime:
        """Reject ambiguous provider timestamps."""

        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Strava activity start_date must include a timezone.")
        return value.astimezone(UTC)

    def normalized(self) -> NormalizedStravaActivity:
        """Normalize nullable fields without inventing measurements."""

        source_type = self.sport_type or self.type or "Unknown"

        def nonnegative(value: int | float | None) -> int | float | None:
            if value is None:
                return None
            return max(value, 0)

        raw_summary: dict[str, object] = {
            "id": self.id,
            "sport_type": source_type,
            "start_date": self.start_date.isoformat(),
        }
        return NormalizedStravaActivity(
            external_id=str(self.id),
            sport=discipline_for_sport_type(source_type),
            source_sport_type=source_type,
            name=self.name,
            started_at=self.start_date,
            timezone=self.timezone,
            duration_seconds=max(self.elapsed_time, 0),
            moving_time_seconds=(
                int(value)
                if (value := nonnegative(self.moving_time)) is not None
                else None
            ),
            distance_meters=(
                float(value)
                if (value := nonnegative(self.distance)) is not None
                else None
            ),
            elevation_gain_meters=(
                float(value)
                if (value := nonnegative(self.total_elevation_gain)) is not None
                else None
            ),
            average_heart_rate=(
                float(value)
                if (value := nonnegative(self.average_heartrate)) is not None
                else None
            ),
            max_heart_rate=(
                float(value)
                if (value := nonnegative(self.max_heartrate)) is not None
                else None
            ),
            average_speed=(
                float(value)
                if (value := nonnegative(self.average_speed)) is not None
                else None
            ),
            average_watts=(
                float(value)
                if (value := nonnegative(self.average_watts)) is not None
                else None
            ),
            trainer=self.trainer,
            commute=self.commute,
            manual=self.manual,
            raw_summary=raw_summary,
        )


class StravaActivityPage(BaseModel):
    """One provider page plus rate-limit metadata."""

    activities: list[StravaActivitySummary]
    rate_limits: StravaRateLimits


class StravaWebhookEvent(BaseModel):
    """Validated webhook callback payload."""

    model_config = ConfigDict(extra="ignore")

    object_type: WebhookObjectType
    object_id: int
    aspect_type: WebhookAspectType
    owner_id: int
    event_time: int
    updates: dict[str, Any] = Field(default_factory=dict)
    subscription_id: int | None = None

    @property
    def occurred_at(self) -> datetime:
        """Return provider event time as UTC."""

        return datetime.fromtimestamp(self.event_time, tz=UTC)

    def external_event_key(self) -> str:
        """Build a stable, compact idempotency key from the canonical payload."""

        canonical = json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class StravaSyncStats(BaseModel):
    """Safe counters and rate metadata returned by a sync."""

    imported_count: int = 0
    updated_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0
    pages_fetched: int = 0
    stopped_at_cutoff: bool = False
    rate_limited: bool = False
    rate_limits: StravaRateLimits | None = None
