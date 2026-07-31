"""Strava workout import adapter."""

from __future__ import annotations

from app.domain.enums import CyclingType
from app.schemas.strava import NormalizedStravaActivity
from app.services.activities.contracts import ActivityImportData
from app.services.activities.normalization import (
    human_activity_name,
    swimming_environment,
)


def from_strava(normalized: NormalizedStravaActivity) -> ActivityImportData:
    """Map one normalized Strava record to the exact-import contract."""

    raw = normalized.raw_summary or {}
    average_speed_mps = normalized.average_speed
    max_speed_mps = getattr(normalized, "max_speed", None)
    return ActivityImportData(
        source=normalized.source,
        external_id=normalized.external_id,
        discipline=normalized.sport,
        raw_sport=normalized.source_sport_type,
        title=normalized.name or human_activity_name(normalized.source_sport_type),
        started_at=normalized.started_at,
        timezone=normalized.timezone,
        duration_seconds=normalized.duration_seconds,
        moving_duration_seconds=normalized.moving_time_seconds,
        distance_meters=normalized.distance_meters,
        elevation_gain_meters=normalized.elevation_gain_meters,
        average_heart_rate=normalized.average_heart_rate,
        max_heart_rate=normalized.max_heart_rate,
        average_cadence=getattr(normalized, "average_cadence", None),
        average_speed_kph=(
            average_speed_mps * 3.6 if average_speed_mps is not None else None
        ),
        max_speed_kph=(max_speed_mps * 3.6 if max_speed_mps is not None else None),
        cycling_type=(CyclingType.STATIONARY if normalized.trainer else None),
        swimming_environment=swimming_environment(
            None,
            raw_sport=normalized.source_sport_type,
        ),
        source_metadata={
            "timezone": normalized.timezone,
            "average_speed_mps": average_speed_mps,
            "max_speed_mps": max_speed_mps,
            "average_watts": normalized.average_watts,
            "trainer": normalized.trainer,
            "commute": normalized.commute,
            "provider_manual": normalized.manual,
            "raw_summary": raw,
        },
    )


__all__ = ["from_strava"]
