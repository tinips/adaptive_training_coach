"""Deterministic workout identity, normalization, and validation."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import cast

from app.domain.enums import (
    ActivitySource,
    CyclingType,
    Discipline,
    HikingType,
    RunningType,
    StrengthType,
    SwimmingEnvironment,
)
from app.schemas.workouts import (
    CyclingWorkoutDetailsData,
    HikingWorkoutDetailsData,
    RunningWorkoutDetailsData,
    SwimmingWorkoutDetailsData,
    WorkoutDetailsData,
    values_conflict,
)
from app.services.activities.contracts import (
    ActivityImportData,
    ActivityImportValidationError,
)


def exact_activity_external_id(
    *,
    external_id: str | None,
    source: ActivitySource,
    discipline: Discipline,
    started_at: datetime,
    duration_seconds: int,
    distance_meters: float | None,
) -> str:
    """Return a provider ID or a deterministic normalized-value fingerprint."""

    if external_id is not None and (cleaned := external_id.strip()):
        return cleaned
    payload = {
        "source": source.value,
        "discipline": discipline.value,
        "started_at": as_utc(started_at).isoformat(timespec="microseconds"),
        "duration_seconds": duration_seconds,
        "distance_meters": (
            None if distance_meters is None else format(float(distance_meters), ".12g")
        ),
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return f"fingerprint:{hashlib.sha256(serialized.encode('utf-8')).hexdigest()}"


def normalize_import(incoming: ActivityImportData) -> None:
    """Resolve conservative canonical fallbacks and exact source identity."""

    raw = f"{incoming.raw_sport} {incoming.raw_sub_sport or ''}".casefold()
    if (
        incoming.discipline is Discipline.HIKING
        and "walk" in raw
        and not any(
            token in raw for token in ("hike", "hiking", "trek", "mountain", "snowshoe")
        )
    ):
        incoming.discipline = Discipline.OTHER
    if incoming.discipline is Discipline.SWIMMING:
        if incoming.swimming_environment is None:
            incoming.swimming_environment = swimming_environment(
                None,
                raw_sport=raw,
            )
        if incoming.swimming_environment is None:
            incoming.swimming_environment = SwimmingEnvironment.UNKNOWN
            incoming.source_metadata["normalization_fallback"] = (
                "swimming_environment_unknown"
            )
        elif (
            incoming.swimming_environment is SwimmingEnvironment.POOL
            and incoming.pool_details is None
        ):
            incoming.source_metadata["normalization_fallback"] = "pool_length_unknown"
            incoming.discipline = Discipline.OTHER
    incoming.running_type = incoming.running_type or running_type(raw)
    incoming.cycling_type = incoming.cycling_type or cycling_type(raw)
    incoming.hiking_type = incoming.hiking_type or hiking_type(raw)
    incoming.strength_type = incoming.strength_type or strength_type(raw)
    incoming.activity_name = incoming.activity_name or human_activity_name(
        incoming.raw_sub_sport or incoming.raw_sport
    )
    if incoming.discipline is Discipline.OTHER:
        incoming.unsupported_metrics = {
            **incoming.unsupported_metrics,
            **{
                key: value
                for key, value in (
                    ("moving_duration_seconds", incoming.moving_duration_seconds),
                    ("elevation_gain_meters", incoming.elevation_gain_meters),
                    ("elevation_loss_meters", incoming.elevation_loss_meters),
                    ("calories_kcal", incoming.calories_kcal),
                    ("average_cadence", incoming.average_cadence),
                    ("max_cadence", incoming.max_cadence),
                    ("average_speed_kph", incoming.average_speed_kph),
                    ("max_speed_kph", incoming.max_speed_kph),
                    ("average_power_watts", incoming.average_power_watts),
                    ("max_power_watts", incoming.max_power_watts),
                    ("route_points", list(incoming.route_points)),
                )
                if value not in (None, [], ())
            },
        }
    incoming.external_id = exact_activity_external_id(
        external_id=incoming.external_id,
        source=incoming.source,
        discipline=incoming.discipline,
        started_at=incoming.started_at,
        duration_seconds=incoming.duration_seconds,
        distance_meters=incoming.distance_meters,
    )


def validate_import(incoming: ActivityImportData) -> None:
    """Validate the normalized persistence boundary."""

    if incoming.external_id is None or len(incoming.external_id) > 128:
        raise ActivityImportValidationError("Invalid workout source key")
    if incoming.duration_seconds <= 0:
        raise ActivityImportValidationError("Workout duration must be positive")
    as_utc(incoming.started_at)
    if incoming.ended_at is not None:
        as_utc(incoming.ended_at)
    for value in (
        incoming.moving_duration_seconds,
        incoming.distance_meters,
        incoming.elevation_gain_meters,
        incoming.elevation_loss_meters,
        incoming.calories_kcal,
        incoming.average_heart_rate,
        incoming.max_heart_rate,
        incoming.average_cadence,
        incoming.max_cadence,
        incoming.average_speed_kph,
        incoming.max_speed_kph,
        incoming.average_power_watts,
        incoming.max_power_watts,
    ):
        if value is not None and value < 0:
            raise ActivityImportValidationError("Workout metrics cannot be negative")
    observation_keys: set[str] = set()
    for observation in incoming.heart_rate_observations:
        if observation.source is not incoming.source:
            raise ActivityImportValidationError(
                "Heart-rate observation source does not match workout source"
            )
        if (
            not observation.source_record_key
            or len(observation.source_record_key) > 64
            or observation.source_record_key in observation_keys
        ):
            raise ActivityImportValidationError(
                "Invalid heart-rate observation source key"
            )
        observation_keys.add(observation.source_record_key)
        started_at = as_utc(observation.started_at)
        ended_at = as_utc(observation.ended_at)
        if ended_at < started_at:
            raise ActivityImportValidationError(
                "Heart-rate observation period is invalid"
            )
        if (
            not math.isfinite(observation.beats_per_minute)
            or observation.beats_per_minute <= 0
        ):
            raise ActivityImportValidationError(
                "Heart-rate observation must be positive"
            )


def record_canonical_conflicts(
    incoming: ActivityImportData,
    derived_details: WorkoutDetailsData,
) -> None:
    """Retain provider pace/speed conflicts as traceability metadata."""

    conflict: dict[str, float] = {}
    if isinstance(derived_details, RunningWorkoutDetailsData) and values_conflict(
        incoming.average_pace_seconds_per_km,
        derived_details.average_pace_seconds_per_km,
    ):
        conflict = {
            "imported_average_pace_seconds_per_km": (
                incoming.average_pace_seconds_per_km or 0
            ),
            "canonical_average_pace_seconds_per_km": (
                derived_details.average_pace_seconds_per_km or 0
            ),
        }
    elif isinstance(derived_details, HikingWorkoutDetailsData) and values_conflict(
        incoming.average_pace_seconds_per_km,
        derived_details.average_pace_seconds_per_km,
    ):
        conflict = {
            "imported_average_pace_seconds_per_km": (
                incoming.average_pace_seconds_per_km or 0
            ),
            "canonical_average_pace_seconds_per_km": (
                derived_details.average_pace_seconds_per_km or 0
            ),
        }
    elif isinstance(derived_details, CyclingWorkoutDetailsData) and values_conflict(
        incoming.average_speed_kph,
        derived_details.average_speed_kph,
    ):
        conflict = {
            "imported_average_speed_kph": incoming.average_speed_kph or 0,
            "canonical_average_speed_kph": derived_details.average_speed_kph or 0,
        }
    elif isinstance(derived_details, SwimmingWorkoutDetailsData) and values_conflict(
        incoming.average_pace_seconds_per_100m,
        derived_details.average_pace_seconds_per_100m,
    ):
        conflict = {
            "imported_average_pace_seconds_per_100m": (
                incoming.average_pace_seconds_per_100m or 0
            ),
            "canonical_average_pace_seconds_per_100m": (
                derived_details.average_pace_seconds_per_100m or 0
            ),
        }
    if conflict:
        warnings_value = incoming.source_metadata.get("normalization_warnings")
        if not isinstance(warnings_value, list):
            warnings_value = []
            incoming.source_metadata["normalization_warnings"] = warnings_value
        cast(list[object], warnings_value).append(
            {"code": "pace_or_speed_conflict", **conflict}
        )


def finalize_source_metadata(incoming: ActivityImportData) -> None:
    """Serialize normalized provider evidence without confidence metadata."""

    values = dict(incoming.source_metadata)
    values.update(
        {
            key: json_safe(value)
            for key, value in (
                ("started_at", incoming.started_at),
                ("ended_at", incoming.ended_at),
                ("timezone", incoming.timezone),
                ("duration_seconds", incoming.duration_seconds),
                ("moving_duration_seconds", incoming.moving_duration_seconds),
                ("distance_meters", incoming.distance_meters),
                ("elevation_gain_meters", incoming.elevation_gain_meters),
                ("elevation_loss_meters", incoming.elevation_loss_meters),
                ("calories_kcal", incoming.calories_kcal),
                ("average_heart_rate", incoming.average_heart_rate),
                ("max_heart_rate", incoming.max_heart_rate),
                ("average_cadence", incoming.average_cadence),
                ("max_cadence", incoming.max_cadence),
                ("average_speed_kph", incoming.average_speed_kph),
                ("max_speed_kph", incoming.max_speed_kph),
                (
                    "average_pace_seconds_per_km",
                    incoming.average_pace_seconds_per_km,
                ),
                (
                    "average_pace_seconds_per_100m",
                    incoming.average_pace_seconds_per_100m,
                ),
                ("route_points", list(incoming.route_points)),
                ("unsupported_metrics", incoming.unsupported_metrics),
                ("title", incoming.title),
                ("notes", incoming.notes),
            )
            if value not in (None, [], (), {})
        }
    )
    incoming.source_metadata = values


def running_type(raw: str) -> RunningType:
    if "trail" in raw:
        return RunningType.TRAIL
    if "track" in raw:
        return RunningType.TRACK
    if any(token in raw for token in ("treadmill", "virtual", "indoor")):
        return RunningType.TREADMILL
    return RunningType.OUTDOOR


def cycling_type(raw: str) -> CyclingType:
    if any(token in raw for token in ("stationary", "virtual", "indoor", "trainer")):
        return CyclingType.STATIONARY
    if any(token in raw for token in ("mountainbike", "mountain bike", "mtb")):
        return CyclingType.MTB
    if "gravel" in raw:
        return CyclingType.GRAVEL
    if any(token in raw for token in ("road", "ride", "cycling", "biking")):
        return CyclingType.ROAD
    return CyclingType.OTHER


def hiking_type(raw: str) -> HikingType:
    if "snowshoe" in raw:
        return HikingType.SNOWSHOEING
    if any(token in raw for token in ("mountaineer", "alpin")):
        return HikingType.MOUNTAINEERING
    if "trek" in raw:
        return HikingType.TREKKING
    if any(token in raw for token in ("hike", "hiking", "walking")):
        return HikingType.HIKING
    return HikingType.OTHER


def strength_type(raw: str) -> StrengthType:
    if any(token in raw for token in ("calisthen", "bodyweight")):
        return StrengthType.CALISTHENICS
    if any(token in raw for token in ("gym", "weight", "strength", "crossfit")):
        return StrengthType.GYM
    return StrengthType.OTHER


def swimming_environment(
    value: object,
    *,
    raw_sport: str,
) -> SwimmingEnvironment | None:
    if isinstance(value, SwimmingEnvironment):
        return value
    if isinstance(value, str):
        normalized = value.strip().upper().replace(" ", "_")
        try:
            return SwimmingEnvironment(normalized)
        except ValueError:
            pass
    raw = raw_sport.casefold()
    if any(token in raw for token in ("openwater", "open water")):
        return SwimmingEnvironment.OPEN_WATER
    if any(token in raw for token in ("pool", "lap swimming", "lapswimming")):
        return SwimmingEnvironment.POOL
    return None


def human_activity_name(raw: str) -> str:
    cleaned = raw.strip()
    for prefix in (
        "HKWorkoutActivityType",
        "HKWorkoutActivity",
        "WorkoutActivityType",
    ):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix) :]
            break
    cleaned = re.sub(r"[_-]+", " ", cleaned)
    cleaned = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", cleaned)
    cleaned = " ".join(cleaned.split())
    if not cleaned or cleaned.casefold() == "unknown":
        return "Other activity"
    return cleaned[0].upper() + cleaned[1:]


def workout_title(discipline: Discipline, provider: str) -> str:
    labels = {
        Discipline.RUNNING: "run",
        Discipline.CYCLING: "ride",
        Discipline.SWIMMING: "swim",
        Discipline.HIKING: "hike",
        Discipline.STRENGTH: "strength workout",
        Discipline.OTHER: "workout",
    }
    return f"{provider} {labels[discipline]}"


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        # SQLite drops offsets on round-trip; portable-test values are UTC.
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def json_safe(value: object) -> object:
    if isinstance(value, datetime):
        return as_utc(value).isoformat()
    if hasattr(value, "value") and isinstance(value.value, str):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return value


__all__ = [
    "as_utc",
    "exact_activity_external_id",
    "finalize_source_metadata",
    "human_activity_name",
    "json_safe",
    "normalize_import",
    "record_canonical_conflicts",
    "swimming_environment",
    "validate_import",
    "workout_title",
]
