"""Per-session deterministic comparison: intent, output, score, efficiency.

Pure function, no I/O, no LLM call. Sourced from docs/decisions/locked.md,
"First-week evaluator" and "Signal scoring, THRIVING, and volume status", and
docs/design/first-week-evaluator.md, "Per-session comparison".

Elevation adjustment (docs/decisions/locked.md, "Elevation adjustment
(running and cycling)") is intentionally out of scope for this pass: it is
not part of docs/briefs/backlog/first-week-evaluator.md's test-first order
or acceptance criteria, and the design doc's own worked examples use flat
routes. Raw actual pace/speed is used throughout.
"""

from __future__ import annotations

from app.domain.enums import (
    Discipline,
    PlannedSessionLinkStatus,
    SessionIntentVerdict,
    SessionOutputVerdict,
)
from app.schemas.weekly_evaluation import (
    EvaluatorWorkoutEvidence,
    PerSessionInsight,
    ResolvedHeartRate,
)
from app.schemas.weekly_plans import PlanSession
from app.services.athlete_zones import ReferenceHeartRateZones
from app.services.weekly_evaluation import constants as c
from app.services.weekly_evaluation.hr_bands import (
    ReferenceHrBandName,
    resolve_reference_hr_band,
)

_VARIANCE_FLAG_CELLS = {
    (SessionIntentVerdict.OVERCOOKED, SessionOutputVerdict.ABOVE_EXPECTED_OUTPUT),
    (
        SessionIntentVerdict.EASIER_THAN_EXPECTED,
        SessionOutputVerdict.BELOW_EXPECTED_OUTPUT,
    ),
}

_POSITIVE_EFFICIENCY_COMBOS = {
    (SessionIntentVerdict.AS_PRESCRIBED, SessionOutputVerdict.ABOVE_EXPECTED_OUTPUT),
    (
        SessionIntentVerdict.EASIER_THAN_EXPECTED,
        SessionOutputVerdict.ABOVE_EXPECTED_OUTPUT,
    ),
    (
        SessionIntentVerdict.EASIER_THAN_EXPECTED,
        SessionOutputVerdict.WITHIN_EXPECTED_OUTPUT,
    ),
}

_PACE_OR_POWER_METRICS = {
    "PACE_SECONDS_PER_KM",
    "SWIM_PACE_SECONDS_PER_100M",
    "POWER_WATTS",
}


def _actual_duration_seconds(actual: EvaluatorWorkoutEvidence) -> int:
    if (
        actual.duration_source == "MOVING"
        and actual.moving_duration_seconds is not None
    ):
        return actual.moving_duration_seconds
    return actual.duration_seconds


def _sampled_average_hr(actual: EvaluatorWorkoutEvidence) -> float | None:
    observations = actual.heart_rate_observations
    if not observations:
        return None
    duration = _actual_duration_seconds(actual)
    if duration <= 0:
        return None
    covered = sum(
        (item.ended_at - item.started_at).total_seconds() for item in observations
    )
    if covered / duration < c.SAMPLED_HR_MIN_DURATION_COVERAGE:
        return None
    total_weight = sum(
        (item.ended_at - item.started_at).total_seconds() for item in observations
    )
    if total_weight <= 0:
        return None
    weighted_sum = sum(
        item.beats_per_minute * (item.ended_at - item.started_at).total_seconds()
        for item in observations
    )
    return weighted_sum / total_weight


def resolve_effective_average_hr(actual: EvaluatorWorkoutEvidence) -> ResolvedHeartRate:
    """Stored summary precedes an adequately-covered sampled average.

    docs/decisions/locked.md, "Signal scoring...": "a stored average-HR
    summary takes precedence. Reliable samples covering at least 80% of
    selected workout duration may supply an average when no summary is
    available. If a stored and sampled average differ by more than 10 bpm,
    emit SOURCE_CONFLICT and make the HR verdict NOT_COMPARABLE."
    """

    summary = actual.average_heart_rate_bpm
    sampled = _sampled_average_hr(actual)
    if summary is not None and sampled is not None:
        if abs(summary - sampled) > c.HR_SOURCE_CONFLICT_THRESHOLD_BPM:
            return ResolvedHeartRate(
                average_bpm=None, quality_flags=("SOURCE_CONFLICT",)
            )
        return ResolvedHeartRate(average_bpm=summary)
    if summary is not None:
        return ResolvedHeartRate(average_bpm=summary)
    if sampled is not None:
        return ResolvedHeartRate(average_bpm=sampled)
    return ResolvedHeartRate(average_bpm=None)


def _output_verdict(
    *, metric: str, target_range: tuple[float, float] | None, actual_value: float | None
) -> SessionOutputVerdict:
    if actual_value is None or target_range is None:
        return SessionOutputVerdict.UNKNOWN
    lower, upper = target_range
    if metric in ("PACE_SECONDS_PER_KM", "SWIM_PACE_SECONDS_PER_100M"):
        # Lower value (seconds per unit) is faster, i.e. better output.
        if actual_value > upper:
            return SessionOutputVerdict.BELOW_EXPECTED_OUTPUT
        if actual_value < lower:
            return SessionOutputVerdict.ABOVE_EXPECTED_OUTPUT
        return SessionOutputVerdict.WITHIN_EXPECTED_OUTPUT
    if metric == "POWER_WATTS":
        # Higher watts is harder, i.e. better output.
        if actual_value < lower:
            return SessionOutputVerdict.BELOW_EXPECTED_OUTPUT
        if actual_value > upper:
            return SessionOutputVerdict.ABOVE_EXPECTED_OUTPUT
        return SessionOutputVerdict.WITHIN_EXPECTED_OUTPUT
    return (
        SessionOutputVerdict.UNKNOWN
    )  # RPE fallback: no objective output verdict in v1.


def _actual_speed_or_power(
    *, discipline: Discipline, actual: EvaluatorWorkoutEvidence
) -> float | None:
    if discipline is Discipline.RUNNING:
        pace = actual.canonical_pace_seconds_per_km
        return 3600.0 / pace if pace else None
    if discipline is Discipline.SWIMMING:
        pace = actual.canonical_pace_seconds_per_100m
        return 100.0 / pace if pace else None  # meters/second
    if discipline is Discipline.CYCLING:
        return actual.average_power_watts
    return None


def _planned_speed_or_power_bounds(
    *, discipline: Discipline, target_range: tuple[float, float]
) -> tuple[float, float] | None:
    """(minimum, maximum) planned speed-or-power, in `_actual_speed_or_power`'s unit."""

    lower, upper = target_range
    if discipline is Discipline.RUNNING:
        return 3600.0 / upper, 3600.0 / lower  # slower pace -> lower speed
    if discipline is Discipline.SWIMMING:
        return 100.0 / upper, 100.0 / lower
    if discipline is Discipline.CYCLING:
        return lower, upper
    return None


def compare_session(
    *,
    planned: PlanSession,
    actual: EvaluatorWorkoutEvidence | None,
    reference_hr_zones: ReferenceHeartRateZones,
) -> PerSessionInsight:
    planned_duration_seconds = (
        planned.targets.duration_minutes * 60
        if planned.targets.duration_minutes is not None
        else None
    )
    planned_distance_range = getattr(planned.targets, "distance_range_meters", None)

    if actual is None:
        return PerSessionInsight(
            plan_session_id=planned.id,
            workout_id=None,
            status=PlannedSessionLinkStatus.MISSED,
            discipline=planned.discipline,
            planned_duration_seconds=planned_duration_seconds,
            planned_distance_range_meters=planned_distance_range,
        )

    actual_duration_seconds = _actual_duration_seconds(actual)
    duration_delta_seconds = (
        actual_duration_seconds - planned_duration_seconds
        if planned_duration_seconds is not None
        else None
    )
    duration_delta_percent = (
        duration_delta_seconds / planned_duration_seconds * 100
        if planned_duration_seconds and duration_delta_seconds is not None
        else None
    )

    distance_delta_meters: float | None = None
    distance_delta_percent: float | None = None
    if planned_distance_range is not None and actual.distance_meters is not None:
        midpoint = (planned_distance_range[0] + planned_distance_range[1]) / 2
        distance_delta_meters = actual.distance_meters - midpoint
        if midpoint:
            distance_delta_percent = distance_delta_meters / midpoint * 100

    metric = planned.intensity.metric
    output_metric = metric if metric in _PACE_OR_POWER_METRICS else None
    actual_output_value: float | None = None
    if metric == "PACE_SECONDS_PER_KM":
        actual_output_value = actual.canonical_pace_seconds_per_km
    elif metric == "SWIM_PACE_SECONDS_PER_100M":
        actual_output_value = actual.canonical_pace_seconds_per_100m
    elif metric == "POWER_WATTS":
        actual_output_value = actual.average_power_watts

    is_strength = planned.discipline is Discipline.STRENGTH
    output_verdict = (
        SessionOutputVerdict.UNKNOWN
        if is_strength
        else _output_verdict(
            metric=metric,
            target_range=planned.intensity.target_range,
            actual_value=actual_output_value,
        )
    )
    if is_strength:
        output_metric = None
        actual_output_value = None

    resolved_hr = resolve_effective_average_hr(actual)

    band_name: ReferenceHrBandName | None = None
    band_range: tuple[float, float] | None = None
    quality_flags: list[str] = list(resolved_hr.quality_flags) + list(
        actual.quality_flags
    )

    if is_strength:
        # locked.md, "First-week evaluator": strength is duration-only,
        # matched not flagged -- it never receives an intent verdict.
        intent_verdict = SessionIntentVerdict.NOT_COMPARABLE
    else:
        band = resolve_reference_hr_band(
            rpe_range=planned.intensity.rpe_range, zones=reference_hr_zones
        )
        if band is None:
            intent_verdict = SessionIntentVerdict.NOT_COMPARABLE
            quality_flags.append("AMBIGUOUS_RPE_BAND")
        else:
            band_name, band_range = band
            if resolved_hr.average_bpm is None:
                intent_verdict = SessionIntentVerdict.NOT_COMPARABLE
                if "SOURCE_CONFLICT" not in quality_flags:
                    quality_flags.append("MISSING_HR")
            else:
                lower, upper = band_range
                allowance = c.HR_REFERENCE_BAND_ALLOWANCE_BPM
                if resolved_hr.average_bpm > upper + allowance:
                    intent_verdict = SessionIntentVerdict.OVERCOOKED
                elif resolved_hr.average_bpm < lower - allowance:
                    intent_verdict = SessionIntentVerdict.EASIER_THAN_EXPECTED
                else:
                    intent_verdict = SessionIntentVerdict.AS_PRESCRIBED

    if output_metric is not None and actual_output_value is None and not is_strength:
        quality_flags.append("MISSING_OUTPUT_METRIC")

    intent_point_value: int | None = None
    output_point_value: int | None = None
    point_value: int | None = None
    variance_flag = False
    if (
        intent_verdict != SessionIntentVerdict.NOT_COMPARABLE
        and output_verdict != SessionOutputVerdict.UNKNOWN
    ):
        intent_point_value = c.INTENT_POINT_VALUE[intent_verdict]
        output_point_value = c.OUTPUT_POINT_VALUE[output_verdict]
        point_value = intent_point_value + output_point_value
        variance_flag = (intent_verdict, output_verdict) in _VARIANCE_FLAG_CELLS

    ratio_value: float | None = None
    ratio_min: float | None = None
    ratio_max: float | None = None
    positive_efficiency_evidence = False
    if (
        planned.discipline
        in (Discipline.RUNNING, Discipline.SWIMMING, Discipline.CYCLING)
        and output_metric is not None
        and band_range is not None
        and resolved_hr.average_bpm is not None
        and not resolved_hr.quality_flags
    ):
        speed_or_power = _actual_speed_or_power(
            discipline=planned.discipline, actual=actual
        )
        bounds = _planned_speed_or_power_bounds(
            discipline=planned.discipline, target_range=planned.intensity.target_range
        )
        if speed_or_power is not None and bounds is not None:
            ratio_value = speed_or_power / resolved_hr.average_bpm
            ratio_min = bounds[0] / band_range[1]
            ratio_max = bounds[1] / band_range[0]
            positive_efficiency_evidence = (
                ratio_value > ratio_max
                and (intent_verdict, output_verdict) in _POSITIVE_EFFICIENCY_COMBOS
            )

    return PerSessionInsight(
        plan_session_id=planned.id,
        workout_id=actual.workout_id,
        status=PlannedSessionLinkStatus.MATCHED,
        discipline=planned.discipline,
        planned_duration_seconds=planned_duration_seconds,
        actual_duration_seconds=actual_duration_seconds,
        duration_delta_seconds=duration_delta_seconds,
        duration_delta_percent=duration_delta_percent,
        duration_source=actual.duration_source,
        planned_distance_range_meters=planned_distance_range,
        actual_distance_meters=actual.distance_meters,
        distance_delta_meters=distance_delta_meters,
        distance_delta_percent=distance_delta_percent,
        output_metric=output_metric,
        planned_output_range=(
            planned.intensity.target_range if output_metric is not None else None
        ),
        actual_output_value=actual_output_value,
        output_verdict=output_verdict,
        reference_hr_band=band_name,
        reference_hr_range_bpm=band_range,
        actual_average_hr_bpm=resolved_hr.average_bpm,
        intent_verdict=intent_verdict,
        intent_point_value=intent_point_value,
        output_point_value=output_point_value,
        point_value=point_value,
        variance_flag=variance_flag,
        ratio_value=ratio_value,
        ratio_min=ratio_min,
        ratio_max=ratio_max,
        positive_efficiency_evidence=positive_efficiency_evidence,
        quality_flags=tuple(quality_flags),
    )
