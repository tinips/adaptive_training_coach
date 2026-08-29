"""iPhone HealthKit POC payload to canonical activity import mapping."""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.enums import (
    ActivitySource,
    CyclingType,
    Discipline,
    RunningType,
    StrengthType,
    SwimmingEnvironment,
)
from app.schemas.mobile_sync import HealthKitWorkoutPayload
from app.services.activities.contracts import ActivityImportData
from app.services.activities.normalization import workout_title


def from_healthkit_workout(payload: HealthKitWorkoutPayload) -> ActivityImportData:
    """Map the minimum iPhone POC payload without requesting extra health data."""

    raw_activity_type = payload.activity_type
    mapping = _discipline_for_activity_type(raw_activity_type)
    return ActivityImportData(
        source=ActivitySource.APPLE_HEALTH,
        external_id=f"healthkit:{payload.workout_uuid}",
        discipline=mapping.discipline,
        raw_sport=raw_activity_type,
        title=workout_title(mapping.discipline, "HealthKit"),
        started_at=payload.started_at,
        ended_at=payload.ended_at,
        duration_seconds=payload.duration_seconds,
        moving_duration_seconds=payload.moving_duration_seconds,
        distance_meters=payload.distance_meters,
        elevation_gain_meters=payload.elevation_gain_meters,
        elevation_loss_meters=payload.elevation_loss_meters,
        calories_kcal=payload.calories_kcal,
        average_heart_rate=payload.average_heart_rate,
        max_heart_rate=payload.max_heart_rate,
        average_cadence=payload.average_cadence,
        max_cadence=payload.max_cadence,
        running_type=mapping.running_type,
        cycling_type=mapping.cycling_type,
        swimming_environment=mapping.swimming_environment,
        strength_type=mapping.strength_type,
        source_metadata={
            "ingestion_channel": "HEALTHKIT_IOS_POC",
            "healthkit_activity_type": raw_activity_type,
        },
    )


@dataclass(frozen=True, slots=True)
class _ActivityMapping:
    discipline: Discipline
    running_type: RunningType | None = None
    cycling_type: CyclingType | None = None
    swimming_environment: SwimmingEnvironment | None = None
    strength_type: StrengthType | None = None


def _discipline_for_activity_type(activity_type: str) -> _ActivityMapping:
    """Map documented semantic iOS keys, conservatively retaining unknown types."""

    key = _normalise_activity_type(activity_type)
    if key in {"running", "trackrunning", "trailrunning"}:
        return _ActivityMapping(
            discipline=Discipline.RUNNING,
            running_type=(
                RunningType.TRAIL if key == "trailrunning" else RunningType.OUTDOOR
            ),
        )
    if key in {"treadmillrunning", "indoorrunning"}:
        return _ActivityMapping(
            discipline=Discipline.RUNNING,
            running_type=RunningType.TREADMILL,
        )
    if key in {"cycling", "roadcycling", "handcycling"}:
        return _ActivityMapping(
            discipline=Discipline.CYCLING,
            cycling_type=CyclingType.ROAD,
        )
    if key in {"indoorcycling", "stationarycycling"}:
        return _ActivityMapping(
            discipline=Discipline.CYCLING,
            cycling_type=CyclingType.STATIONARY,
        )
    if key == "hiking":
        return _ActivityMapping(discipline=Discipline.HIKING)
    if key in {"swimming", "poolswimming"}:
        # Pool length is deliberately outside the minimal client payload. Keep
        # the environment unknown rather than claiming an unsupported pool.
        return _ActivityMapping(
            discipline=Discipline.SWIMMING,
            swimming_environment=SwimmingEnvironment.UNKNOWN,
        )
    if key == "openwaterswimming":
        return _ActivityMapping(
            discipline=Discipline.SWIMMING,
            swimming_environment=SwimmingEnvironment.OPEN_WATER,
        )
    if key in {
        "traditionalstrengthtraining",
        "functionalstrengthtraining",
        "coretraining",
        "crossfit",
    }:
        return _ActivityMapping(
            discipline=Discipline.STRENGTH,
            strength_type=StrengthType.GYM,
        )
    if key in {"bodyweighttraining", "calisthenics"}:
        return _ActivityMapping(
            discipline=Discipline.STRENGTH,
            strength_type=StrengthType.CALISTHENICS,
        )
    return _ActivityMapping(discipline=Discipline.OTHER)


def _normalise_activity_type(value: str) -> str:
    """Accept semantic iOS keys and the familiar HKWorkoutActivityType form."""

    cleaned = value.strip().casefold()
    for prefix in (
        "hkworkoutactivitytype.",
        "hkworkoutactivitytype",
        "workoutactivitytype.",
    ):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix) :]
            break
    return "".join(character for character in cleaned if character.isalnum())


__all__ = ["from_healthkit_workout"]
