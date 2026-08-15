"""TCX workout import adapter."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime

from app.domain.enums import (
    ActivitySource,
    HeartRateTemporalQuality,
)
from app.integrations.tcx.models import ParsedTCXActivity, ParsedTCXPosition
from app.services.activities.contracts import (
    ActivityImportData,
    ActivityImportValidationError,
    HeartRateObservationData,
)
from app.services.activities.normalization import swimming_environment, workout_title


def from_tcx(parsed: ParsedTCXActivity) -> ActivityImportData:
    """Map one parsed TCX activity to the exact-import contract."""

    if parsed.started_at is None or parsed.duration_seconds is None:
        raise ActivityImportValidationError(
            "TCX workout requires a start time and duration"
        )
    source_metadata: dict[str, object] = {
        "tcx_activity_id": parsed.activity_id,
        "minimum_altitude_meters": parsed.minimum_altitude_meters,
        "maximum_altitude_meters": parsed.maximum_altitude_meters,
        "cadence_sample_count": parsed.cadence_sample_count,
        "parser_warnings": list(parsed.warnings),
    }
    return ActivityImportData(
        source=ActivitySource.TCX,
        # A TCX Id is stable provider identity. Files without one use the
        # shared normalized-value fingerprint.
        external_id=(parsed.source_record_key if parsed.activity_id else None),
        discipline=parsed.discipline,
        raw_sport=parsed.source_sport_type,
        raw_sub_sport=getattr(parsed, "raw_sub_sport", None),
        title=workout_title(parsed.discipline, "TCX"),
        started_at=parsed.started_at,
        ended_at=parsed.ended_at,
        duration_seconds=parsed.duration_seconds,
        distance_meters=parsed.distance_meters,
        elevation_gain_meters=parsed.elevation_gain_meters,
        elevation_loss_meters=getattr(parsed, "elevation_loss_meters", None),
        calories_kcal=parsed.calories_kcal,
        average_heart_rate=parsed.average_heart_rate,
        max_heart_rate=parsed.max_heart_rate,
        average_cadence=parsed.average_cadence,
        max_cadence=getattr(parsed, "max_cadence", None),
        swimming_environment=swimming_environment(
            None,
            raw_sport=parsed.source_sport_type,
        ),
        route_points=tuple(_route_point(item) for item in parsed.route_positions),
        source_metadata=source_metadata,
        heart_rate_observations=tuple(
            HeartRateObservationData(
                source=ActivitySource.TCX,
                source_record_key=item.source_record_key,
                source_name=None,
                started_at=item.timestamp,
                ended_at=item.timestamp,
                beats_per_minute=item.beats_per_minute,
                temporal_quality=HeartRateTemporalQuality.EXACT_SAMPLE,
            )
            for item in parsed.heart_rate_observations
        ),
    )


def _route_point(position: ParsedTCXPosition) -> dict[str, object]:
    return {
        key: value.isoformat() if isinstance(value, datetime) else value
        for key, value in asdict(position).items()
        if value is not None
    }


__all__ = ["from_tcx"]
