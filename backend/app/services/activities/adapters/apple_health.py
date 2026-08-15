"""Apple Health workout import adapter."""

from __future__ import annotations

from collections.abc import Mapping

from app.domain.enums import ActivitySource, SwimmingEnvironment
from app.integrations.apple_health.models import ParsedWorkout
from app.schemas.workouts import PoolSwimmingDetailsData
from app.services.activities.contracts import (
    ActivityImportData,
    HeartRateObservationData,
)
from app.services.activities.normalization import swimming_environment, workout_title


def from_apple_health(workout: ParsedWorkout) -> ActivityImportData:
    """Map one parsed Apple workout to the exact-import contract."""

    source_metadata: dict[str, object] = {
        "source_name": workout.source_name,
        "source_version": workout.source_version,
        "device": workout.device,
        "creation_date": (
            workout.creation_date.isoformat()
            if workout.creation_date is not None
            else None
        ),
        "workout_statistics": [dict(item) for item in workout.workout_statistics],
    }
    parsed_metadata = getattr(workout, "source_metadata", None)
    if isinstance(parsed_metadata, Mapping):
        source_metadata["workout_metadata"] = dict(parsed_metadata)
    raw_sub_sport = getattr(workout, "raw_sub_sport", None)
    environment = swimming_environment(
        getattr(workout, "swimming_environment", None),
        raw_sport=workout.source_workout_type,
    )
    pool_length = getattr(workout, "pool_length_meters", None)
    pool_details = (
        PoolSwimmingDetailsData(pool_length_meters=pool_length)
        if environment is SwimmingEnvironment.POOL
        and isinstance(pool_length, int | float)
        and pool_length > 0
        else None
    )
    return ActivityImportData(
        source=ActivitySource.APPLE_HEALTH,
        external_id=workout.source_record_key,
        discipline=workout.discipline,
        raw_sport=workout.source_workout_type,
        raw_sub_sport=raw_sub_sport,
        title=workout_title(workout.discipline, "Apple Health"),
        started_at=workout.started_at,
        ended_at=workout.ended_at,
        duration_seconds=workout.duration_seconds,
        distance_meters=workout.distance_meters,
        calories_kcal=workout.calories_kcal,
        average_heart_rate=workout.average_heart_rate,
        max_heart_rate=workout.max_heart_rate,
        swimming_environment=environment,
        pool_details=pool_details,
        source_metadata=source_metadata,
        heart_rate_observations=tuple(
            HeartRateObservationData(
                source=ActivitySource.APPLE_HEALTH,
                source_record_key=item.source_record_key,
                source_name=item.source_name,
                started_at=item.started_at,
                ended_at=item.ended_at,
                beats_per_minute=item.beats_per_minute,
                temporal_quality=item.temporal_quality,
            )
            for item in workout.observations
        ),
    )


__all__ = ["from_apple_health"]
