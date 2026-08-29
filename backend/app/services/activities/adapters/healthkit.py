"""iPhone HealthKit POC payload to canonical activity import mapping."""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.enums import (
    ActivitySource,
    CyclingType,
    Discipline,
    HeartRateTemporalQuality,
    RunningType,
    StrengthType,
    SwimmingEnvironment,
)
from app.schemas.mobile_sync import HealthKitWorkoutPayload
from app.services.activities.contracts import (
    ActivityImportData,
    HeartRateObservationData,
)
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
        source_metadata=_source_metadata(payload),
        heart_rate_observations=_heart_rate_observations(payload),
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


def _source_metadata(payload: HealthKitWorkoutPayload) -> dict[str, object]:
    metadata: dict[str, object] = {
        "ingestion_channel": "HEALTHKIT_IOS_POC",
        "healthkit_activity_type": payload.activity_type,
    }
    if payload.source_name is not None:
        metadata["healthkit_source_name"] = payload.source_name
    if payload.all_statistics:
        metadata["healthkit_all_statistics"] = {
            quantity_type: statistics.model_dump(mode="json", exclude_none=True)
            for quantity_type, statistics in payload.all_statistics.items()
        }
    if payload.raw_quantity_samples:
        metadata["healthkit_raw_quantity_samples"] = [
            sample.model_dump(mode="json") for sample in payload.raw_quantity_samples
        ]
    return metadata


def _heart_rate_observations(
    payload: HealthKitWorkoutPayload,
) -> tuple[HeartRateObservationData, ...]:
    observations: list[HeartRateObservationData] = []
    for sample in payload.raw_quantity_samples:
        if (
            sample.quantity_type != "HKQuantityTypeIdentifierHeartRate"
            or sample.heart_rate_bpm is None
        ):
            continue
        interval_seconds = (sample.ended_at - sample.started_at).total_seconds()
        quality = (
            HeartRateTemporalQuality.UNKNOWN
            if interval_seconds < 0
            else HeartRateTemporalQuality.EXACT_SAMPLE
            if interval_seconds == 0
            else HeartRateTemporalQuality.SHORT_INTERVAL
            if interval_seconds <= 60
            else HeartRateTemporalQuality.COARSE_INTERVAL
        )
        observations.append(
            HeartRateObservationData(
                source=ActivitySource.APPLE_HEALTH,
                source_record_key=f"healthkit:{sample.sample_uuid}",
                source_name=sample.source_name,
                started_at=sample.started_at,
                ended_at=sample.ended_at,
                beats_per_minute=sample.heart_rate_bpm,
                temporal_quality=quality,
            )
        )
    return tuple(observations)


__all__ = ["from_healthkit_workout"]
