"""Pure, deterministic calculation of 14-day discipline baseline evidence."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.domain.enums import (
    ActivitySource,
    Discipline,
    HeartRateTemporalQuality,
    SwimmingEnvironment,
)
from app.schemas.fitness import BaselineCalculation, FitnessWorkoutEvidence

CALCULATION_VERSION = 1

_RELIABLE_HR_QUALITIES = {
    HeartRateTemporalQuality.EXACT_SAMPLE,
    HeartRateTemporalQuality.SHORT_INTERVAL,
}
_DUPLICATE_SOURCES = {ActivitySource.APPLE_HEALTH, ActivitySource.TCX}


def calculate_baseline_window(
    *,
    discipline: Discipline,
    workouts: Sequence[FitnessWorkoutEvidence],
    window_started_at: datetime,
    window_ended_at: datetime,
    calculated_at: datetime,
) -> BaselineCalculation | None:
    """Calculate baseline evidence, returning ``None`` without usable sessions.

    Apple Health/TCX pairs are excluded only when their start, duration, and
    known distance agree tightly. Their original rows remain durable and the
    exclusion is retained in the evidence summary and quality flags.
    """

    window_started_at = _as_utc(window_started_at)
    window_ended_at = _as_utc(window_ended_at)
    calculated_at = _as_utc(calculated_at)
    in_window = tuple(
        workout
        for workout in workouts
        if workout.discipline is discipline
        and window_started_at <= _as_utc(workout.started_at) <= window_ended_at
    )
    included, excluded = _exclude_probable_cross_source_duplicates(in_window)
    if not included:
        return None

    digest = _input_digest(
        discipline=discipline,
        workouts=in_window,
        excluded_workout_ids={workout.workout_id for workout in excluded},
    )
    distance_workouts = tuple(
        workout for workout in included if workout.distance_meters is not None
    )
    calories = tuple(
        workout.calories_kcal
        for workout in included
        if workout.calories_kcal is not None
    )
    reliable_hr = tuple(
        observation.beats_per_minute
        for workout in included
        for observation in workout.heart_rate_observations
        if observation.temporal_quality in _RELIABLE_HR_QUALITIES
    )
    sessions_with_reliable_hr = sum(
        any(
            observation.temporal_quality in _RELIABLE_HR_QUALITIES
            for observation in workout.heart_rate_observations
        )
        for workout in included
    )
    has_coarse_hr = any(
        workout.coarse_heart_rate_present
        or any(
            observation.temporal_quality not in _RELIABLE_HR_QUALITIES
            for observation in workout.heart_rate_observations
        )
        for workout in included
    )
    quality_flags = _quality_flags(
        discipline=discipline,
        workouts=included,
        has_reliable_hr=bool(reliable_hr),
        has_coarse_hr=has_coarse_hr,
        excluded=excluded,
    )
    session_count = len(included)
    active_day_count = len({_as_utc(item.started_at).date() for item in included})
    distance_session_count = len(distance_workouts)
    return BaselineCalculation(
        discipline=discipline,
        analysis_started_at=window_started_at,
        analysis_ended_at=window_ended_at,
        calculated_at=calculated_at,
        session_count=session_count,
        active_day_count=active_day_count,
        total_duration_seconds=sum(item.duration_seconds for item in included),
        known_distance_meters=(
            sum(item.distance_meters or 0 for item in distance_workouts)
            if distance_workouts
            else None
        ),
        distance_session_count=distance_session_count,
        longest_duration_seconds=max(item.duration_seconds for item in included),
        longest_distance_meters=(
            max(item.distance_meters or 0 for item in distance_workouts)
            if distance_workouts
            else None
        ),
        total_calories_kcal=sum(calories) if calories else None,
        reliable_hr_sample_count=len(reliable_hr),
        reliable_average_hr_bpm=(sum(reliable_hr) / len(reliable_hr))
        if reliable_hr
        else None,
        reliable_max_hr_bpm=max(reliable_hr) if reliable_hr else None,
        confidence=_confidence(
            session_count=session_count,
            active_day_count=active_day_count,
            distance_session_count=distance_session_count,
            reliable_hr_sample_count=len(reliable_hr),
            quality_flags=quality_flags,
        ),
        discipline_metrics_jsonb=_discipline_metrics(discipline, included),
        evidence_summary_jsonb={
            "source_counts": dict(
                sorted(Counter(item.source.value for item in included).items())
            ),
            "workouts_excluded_as_possible_duplicates": len(excluded),
            "sessions_with_distance": distance_session_count,
            "sessions_with_reliable_hr": sessions_with_reliable_hr,
        },
        quality_flags_jsonb=quality_flags,
        source_workout_through_at=max(_as_utc(item.started_at) for item in included),
        input_updated_through_at=max(
            _as_utc(item.fitness_input_updated_at) for item in in_window
        ),
        input_digest=digest,
        calculation_version=CALCULATION_VERSION,
    )


def _exclude_probable_cross_source_duplicates(
    workouts: Sequence[FitnessWorkoutEvidence],
) -> tuple[tuple[FitnessWorkoutEvidence, ...], tuple[FitnessWorkoutEvidence, ...]]:
    """Pick one representative from each very likely Apple/TCX duplicate set."""

    ordered = tuple(sorted(workouts, key=_workout_sort_key))
    excluded_ids: set[UUID] = set()
    for index, workout in enumerate(ordered):
        if workout.workout_id in excluded_ids:
            continue
        matches = [workout]
        for candidate in ordered[index + 1 :]:
            if candidate.workout_id in excluded_ids:
                continue
            if _as_utc(candidate.started_at) - _as_utc(workout.started_at) > _minutes(
                5
            ):
                break
            if _probably_same_cross_source_session(workout, candidate):
                matches.append(candidate)
        if len(matches) > 1:
            kept = min(matches, key=_duplicate_preference_key)
            excluded_ids.update(
                item.workout_id
                for item in matches
                if item.workout_id != kept.workout_id
            )
    return (
        tuple(item for item in ordered if item.workout_id not in excluded_ids),
        tuple(item for item in ordered if item.workout_id in excluded_ids),
    )


def _probably_same_cross_source_session(
    first: FitnessWorkoutEvidence,
    second: FitnessWorkoutEvidence,
) -> bool:
    if (
        first.source not in _DUPLICATE_SOURCES
        or second.source not in _DUPLICATE_SOURCES
        or first.source is second.source
        or first.discipline is not second.discipline
    ):
        return False
    started_delta = abs(
        (_as_utc(first.started_at) - _as_utc(second.started_at)).total_seconds()
    )
    if started_delta > 5 * 60:
        return False
    duration_delta = abs(first.duration_seconds - second.duration_seconds)
    if duration_delta > max(
        120, max(first.duration_seconds, second.duration_seconds) * 0.1
    ):
        return False
    if first.distance_meters is None or second.distance_meters is None:
        return duration_delta <= 60
    distance_delta = abs(first.distance_meters - second.distance_meters)
    return distance_delta <= max(
        200, max(first.distance_meters, second.distance_meters) * 0.1
    )


def _duplicate_preference_key(workout: FitnessWorkoutEvidence) -> tuple[int, int, str]:
    source_rank = 0 if workout.source is ActivitySource.TCX else 1
    distance_rank = 0 if workout.distance_meters is not None else 1
    return source_rank, distance_rank, str(workout.workout_id)


def _quality_flags(
    *,
    discipline: Discipline,
    workouts: Sequence[FitnessWorkoutEvidence],
    has_reliable_hr: bool,
    has_coarse_hr: bool,
    excluded: Sequence[FitnessWorkoutEvidence],
) -> list[str]:
    flags: set[str] = set()
    if any(item.distance_meters is None for item in workouts):
        flags.add("MISSING_DISTANCE")
    if discipline in {
        Discipline.RUNNING,
        Discipline.CYCLING,
        Discipline.HIKING,
    } and any(
        item.distance_meters is not None and item.moving_duration_seconds is None
        for item in workouts
    ):
        flags.add("MISSING_MOVING_DURATION")
    if has_coarse_hr and not has_reliable_hr:
        flags.add("COARSE_HR_ONLY")
    if excluded:
        flags.add("POSSIBLE_CROSS_SOURCE_DUPLICATES_EXCLUDED")
    if discipline is Discipline.OTHER:
        flags.add("OTHER_DISCIPLINE_UNSPECIFIED")
    return sorted(flags)


def _discipline_metrics(
    discipline: Discipline,
    workouts: Sequence[FitnessWorkoutEvidence],
) -> dict[str, object]:
    if discipline is Discipline.RUNNING:
        return _endurance_metrics(
            workouts,
            subtype_key="running_type_counts",
            cadence_key="average_cadence_spm",
        )
    if discipline is Discipline.CYCLING:
        return _cycling_metrics(workouts)
    if discipline is Discipline.HIKING:
        return _endurance_metrics(
            workouts, subtype_key="hiking_type_counts", cadence_key=None
        )
    if discipline is Discipline.SWIMMING:
        return _swimming_metrics(workouts)
    if discipline is Discipline.STRENGTH:
        return _strength_metrics(workouts)
    return {"modality_specified": False}


def _endurance_metrics(
    workouts: Sequence[FitnessWorkoutEvidence],
    *,
    subtype_key: str,
    cadence_key: str | None,
) -> dict[str, object]:
    distance_workouts = tuple(
        item for item in workouts if item.distance_meters is not None
    )
    moving_workouts = tuple(
        item for item in distance_workouts if item.moving_duration_seconds is not None
    )
    total_distance = sum(item.distance_meters or 0 for item in distance_workouts)
    moving_distance = sum(item.distance_meters or 0 for item in moving_workouts)
    subtype_counts = Counter(item.subtype for item in workouts if item.subtype)
    metrics: dict[str, object] = {
        subtype_key: dict(sorted(subtype_counts.items())),
        "moving_pace_seconds_per_km": (
            sum(item.moving_duration_seconds or 0 for item in moving_workouts)
            * 1000
            / moving_distance
            if moving_distance > 0
            else None
        ),
        "elapsed_pace_seconds_per_km": (
            sum(item.duration_seconds for item in distance_workouts)
            * 1000
            / total_distance
            if total_distance > 0
            else None
        ),
        "elevation_gain_meters": _sum_or_none(
            item.elevation_gain_meters for item in workouts
        ),
    }
    if cadence_key is not None:
        metrics[cadence_key] = _weighted_average(
            (item.average_cadence, item.duration_seconds) for item in workouts
        )
    return metrics


def _cycling_metrics(workouts: Sequence[FitnessWorkoutEvidence]) -> dict[str, object]:
    distance_workouts = tuple(
        item for item in workouts if item.distance_meters is not None
    )
    moving_workouts = tuple(
        item for item in distance_workouts if item.moving_duration_seconds is not None
    )
    total_distance = sum(item.distance_meters or 0 for item in distance_workouts)
    moving_distance = sum(item.distance_meters or 0 for item in moving_workouts)
    moving_duration = sum(item.moving_duration_seconds or 0 for item in moving_workouts)
    elapsed_duration = sum(item.duration_seconds for item in distance_workouts)
    subtype_counts = Counter(item.subtype for item in workouts if item.subtype)
    return {
        "cycling_type_counts": dict(sorted(subtype_counts.items())),
        "moving_speed_kph": (
            moving_distance * 3.6 / moving_duration if moving_duration > 0 else None
        ),
        "elapsed_speed_kph": (
            total_distance * 3.6 / elapsed_duration if elapsed_duration > 0 else None
        ),
        "elevation_gain_meters": _sum_or_none(
            item.elevation_gain_meters for item in workouts
        ),
        "average_cadence_rpm": _weighted_average(
            (item.average_cadence, item.duration_seconds) for item in workouts
        ),
        "power_watts": None,
    }


def _swimming_metrics(
    workouts: Sequence[FitnessWorkoutEvidence],
) -> dict[str, object]:
    environment_to_key = {
        SwimmingEnvironment.POOL: "pool",
        SwimmingEnvironment.OPEN_WATER: "open_water",
        SwimmingEnvironment.UNKNOWN: "unknown",
        None: "unknown",
    }
    result: dict[str, object] = {}
    for key in ("pool", "open_water", "unknown"):
        members = tuple(
            item
            for item in workouts
            if environment_to_key[item.swimming_environment] == key
        )
        result[key] = {
            "session_count": len(members),
            "duration_seconds": sum(item.duration_seconds for item in members),
            "distance_meters": _sum_or_none(item.distance_meters for item in members),
        }
    return result


def _strength_metrics(workouts: Sequence[FitnessWorkoutEvidence]) -> dict[str, object]:
    exercise_counts = tuple(item.structured_exercise_count or 0 for item in workouts)
    subtype_counts = Counter(item.subtype for item in workouts if item.subtype)
    covered = sum(count > 0 for count in exercise_counts)
    return {
        "strength_type_counts": dict(sorted(subtype_counts.items())),
        "structured_exercise_session_count": covered,
        "structured_exercise_count": sum(exercise_counts),
        "structured_exercise_coverage": covered / len(workouts) if workouts else 0,
    }


def _confidence(
    *,
    session_count: int,
    active_day_count: int,
    distance_session_count: int,
    reliable_hr_sample_count: int,
    quality_flags: Sequence[str],
) -> float:
    confidence = 0.2
    confidence += min(session_count, 5) * 0.09
    confidence += min(active_day_count, 5) * 0.06
    confidence += 0.12 * (distance_session_count / session_count)
    if reliable_hr_sample_count:
        confidence += 0.08
    if "MISSING_DISTANCE" in quality_flags:
        confidence -= 0.04
    if "COARSE_HR_ONLY" in quality_flags:
        confidence -= 0.03
    return round(min(1.0, max(0.0, confidence)), 4)


def _input_digest(
    *,
    discipline: Discipline,
    workouts: Sequence[FitnessWorkoutEvidence],
    excluded_workout_ids: set[UUID],
) -> str:
    """Hash fitness facts without persisting titles, notes, routes, or exercises."""

    payload = {
        "discipline": discipline.value,
        "workouts": [
            {
                "id": str(workout.workout_id),
                "source": workout.source.value,
                "started_at": _as_utc(workout.started_at).isoformat(),
                "duration_seconds": workout.duration_seconds,
                "distance_meters": workout.distance_meters,
                "moving_duration_seconds": workout.moving_duration_seconds,
                "calories_kcal": workout.calories_kcal,
                "subtype": workout.subtype,
                "swimming_environment": (
                    workout.swimming_environment.value
                    if workout.swimming_environment is not None
                    else None
                ),
                "elevation_gain_meters": workout.elevation_gain_meters,
                "average_cadence": workout.average_cadence,
                "structured_exercise_count": workout.structured_exercise_count,
                "coarse_heart_rate_present": workout.coarse_heart_rate_present,
                "fitness_input_updated_at": _as_utc(
                    workout.fitness_input_updated_at
                ).isoformat(),
                "excluded_as_duplicate": workout.workout_id in excluded_workout_ids,
                "heart_rate": [
                    {
                        "started_at": _as_utc(observation.started_at).isoformat(),
                        "ended_at": _as_utc(observation.ended_at).isoformat(),
                        "beats_per_minute": observation.beats_per_minute,
                        "quality": observation.temporal_quality.value,
                    }
                    for observation in sorted(
                        workout.heart_rate_observations,
                        key=lambda item: (
                            _as_utc(item.started_at),
                            item.beats_per_minute,
                        ),
                    )
                ],
            }
            for workout in sorted(workouts, key=_workout_sort_key)
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _workout_sort_key(workout: FitnessWorkoutEvidence) -> tuple[datetime, str]:
    return _as_utc(workout.started_at), str(workout.workout_id)


def _minutes(value: int) -> timedelta:
    return timedelta(minutes=value)


def _sum_or_none(values: Iterable[float | None]) -> float | None:
    known = tuple(value for value in values if value is not None)
    return sum(known) if known else None


def _weighted_average(values: Iterable[tuple[float | None, int]]) -> float | None:
    known = tuple((value, weight) for value, weight in values if value is not None)
    total_weight = sum(weight for _, weight in known)
    if total_weight <= 0:
        return None
    return sum((value or 0) * weight for value, weight in known) / total_weight
