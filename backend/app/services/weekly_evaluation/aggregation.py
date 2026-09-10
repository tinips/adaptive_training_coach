"""Weekly aggregate: named denominators, density, signal, THRIVING, volume.

Pure function, no I/O, no LLM call. Sourced from docs/decisions/locked.md,
"Signal scoring, THRIVING, and volume status", and
docs/design/first-week-evaluator.md, "Weekly aggregate".
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from app.domain.enums import (
    Discipline,
    PlannedSessionLinkStatus,
    SessionIntentVerdict,
    SessionOutputVerdict,
    VolumeRangeStatus,
    WeeklySignal,
)
from app.schemas.weekly_evaluation import (
    PerSessionInsight,
    ThrivingGateResult,
    WeeklyEvaluation,
    WeeklyVolumeRangeResult,
)
from app.services.weekly_evaluation import constants as c

_VOLUME_DISCIPLINES = (Discipline.RUNNING, Discipline.CYCLING, Discipline.SWIMMING)


def _speed_or_power(insight: PerSessionInsight) -> float | None:
    """Reconstruct the same speed-or-power basis compare_session used.

    Deliberately independent of PerSessionInsight.ratio_value, which is only
    populated when a reference-HR band resolves; the efficiency factor below
    needs no reference band at all, only the raw actual output and HR.
    """

    if insight.actual_output_value is None:
        return None
    if insight.output_metric == "PACE_SECONDS_PER_KM":
        return 3600.0 / insight.actual_output_value
    if insight.output_metric == "SWIM_PACE_SECONDS_PER_100M":
        return 100.0 / insight.actual_output_value
    if insight.output_metric == "POWER_WATTS":
        return insight.actual_output_value
    return None


def _efficiency_ratio(insight: PerSessionInsight) -> float | None:
    speed_or_power = _speed_or_power(insight)
    if speed_or_power is None or not insight.actual_average_hr_bpm:
        return None
    return speed_or_power / insight.actual_average_hr_bpm


def _volume_range_result(
    *, matched: list[PerSessionInsight], disciplines: tuple[Discipline, ...]
) -> WeeklyVolumeRangeResult:
    range_min = 0.0
    range_max = 0.0
    actual = 0.0
    contributed = False
    for insight in matched:
        if insight.discipline not in disciplines:
            continue
        if insight.planned_distance_range_meters is None:
            continue
        contributed = True
        lower, upper = insight.planned_distance_range_meters
        range_min += lower
        range_max += upper
        actual += insight.actual_distance_meters or 0.0

    if not contributed:
        return WeeklyVolumeRangeResult(status=VolumeRangeStatus.UNKNOWN)

    if actual < range_min:
        percent = actual / range_min * 100 if range_min else None
        status = VolumeRangeStatus.BELOW_RANGE
    elif actual > range_max:
        percent = actual / range_max * 100 if range_max else None
        status = VolumeRangeStatus.ABOVE_RANGE
    else:
        percent = None
        status = VolumeRangeStatus.WITHIN_RANGE

    return WeeklyVolumeRangeResult(
        status=status,
        percent=percent,
        range_min=range_min,
        range_max=range_max,
        actual=actual,
    )


def aggregate_week(
    *,
    plan_id: UUID,
    plan_revision: int,
    week_start: date,
    timezone: str | None,
    insights: tuple[PerSessionInsight, ...],
) -> WeeklyEvaluation:
    matched = [i for i in insights if i.status is PlannedSessionLinkStatus.MATCHED]
    missed = [i for i in insights if i.status is PlannedSessionLinkStatus.MISSED]
    matched_count = len(matched)
    missed_count = len(missed)

    planned_session_denominator = matched_count + missed_count
    session_completion_percent = (
        matched_count / planned_session_denominator * 100
        if planned_session_denominator
        else None
    )

    matched_with_comparable_metric = [
        i for i in matched if i.output_verdict is not SessionOutputVerdict.UNKNOWN
    ]
    matched_metric_coverage_percent = (
        len(matched_with_comparable_metric) / matched_count * 100
        if matched_count
        else None
    )

    matched_with_comparable_effort = [
        i
        for i in matched
        if i.intent_verdict is not SessionIntentVerdict.NOT_COMPARABLE
    ]
    intensity_intent_adherence_percent = (
        sum(
            1
            for i in matched_with_comparable_effort
            if i.intent_verdict is SessionIntentVerdict.AS_PRESCRIBED
        )
        / len(matched_with_comparable_effort)
        * 100
        if matched_with_comparable_effort
        else None
    )

    matched_planned_duration = sum(i.planned_duration_seconds or 0 for i in matched)
    matched_actual_duration = sum(i.actual_duration_seconds or 0 for i in matched)
    matched_duration_percent = (
        matched_actual_duration / matched_planned_duration * 100
        if matched_planned_duration
        else None
    )

    all_planned_duration = sum(i.planned_duration_seconds or 0 for i in insights)
    planned_volume_completion_percent = (
        matched_actual_duration / all_planned_duration * 100
        if all_planned_duration
        else None
    )

    all_actual_duration_seconds = matched_actual_duration  # EXTRA always 0 in v1

    comparable = [i for i in matched if i.point_value is not None]
    comparable_session_count = len(comparable)
    density = (
        sum(i.point_value for i in comparable if i.point_value is not None)
        / comparable_session_count
        if comparable_session_count
        else None
    )

    variance_flag_count = sum(1 for i in matched if i.variance_flag)
    variance_flag_note = (
        f"Watch out: you completed {variance_flag_count} workouts where the "
        "intended intensity did not match the output. This makes it harder "
        "for the planner to accurately adapt your training."
        if variance_flag_count >= c.VARIANCE_FLAG_NOTE_MIN_COUNT
        else None
    )

    volume_range_status_by_discipline = {
        discipline: _volume_range_result(matched=matched, disciplines=(discipline,))
        for discipline in _VOLUME_DISCIPLINES
        if any(
            i.discipline is discipline and i.planned_distance_range_meters is not None
            for i in matched
        )
    }
    blended_volume_range_status = _volume_range_result(
        matched=matched, disciplines=_VOLUME_DISCIPLINES
    )

    zero_overcooked = not any(
        i.intent_verdict is SessionIntentVerdict.OVERCOOKED for i in matched
    )
    zero_below_expected_output = not any(
        i.output_verdict is SessionOutputVerdict.BELOW_EXPECTED_OUTPUT for i in matched
    )
    zero_variance_flag = variance_flag_count == 0
    volume_not_below_range = (
        blended_volume_range_status.status is not VolumeRangeStatus.BELOW_RANGE
    )
    positive_efficiency_count = sum(
        1 for i in matched if i.positive_efficiency_evidence
    )
    has_positive_efficiency_session = (
        positive_efficiency_count >= c.THRIVING_MIN_POSITIVE_EFFICIENCY_SESSIONS
    )
    thriving_gate = ThrivingGateResult(
        met=(
            zero_overcooked
            and zero_below_expected_output
            and zero_variance_flag
            and volume_not_below_range
            and has_positive_efficiency_session
        ),
        zero_overcooked=zero_overcooked,
        zero_below_expected_output=zero_below_expected_output,
        zero_variance_flag=zero_variance_flag,
        volume_not_below_range=volume_not_below_range,
        has_positive_efficiency_session=has_positive_efficiency_session,
    )

    efficiency_factor_by_discipline: dict[Discipline, float | None] = {}
    for discipline in _VOLUME_DISCIPLINES:
        values = [
            ratio
            for i in matched
            if i.discipline is discipline
            and (ratio := _efficiency_ratio(i)) is not None
        ]
        if any(i.discipline is discipline for i in matched):
            efficiency_factor_by_discipline[discipline] = (
                sum(values) / len(values) if values else None
            )

    signal, signal_reasons = _resolve_signal(
        session_completion_percent=session_completion_percent,
        comparable_session_count=comparable_session_count,
        density=density,
        thriving_gate=thriving_gate,
    )

    return WeeklyEvaluation(
        plan_id=plan_id,
        plan_revision=plan_revision,
        week_start=week_start,
        timezone=timezone,
        evaluator_version=c.EVALUATOR_RULE_VERSION,
        per_session_insights=insights,
        matched_count=matched_count,
        missed_count=missed_count,
        session_completion_percent=session_completion_percent,
        matched_metric_coverage_percent=matched_metric_coverage_percent,
        intensity_intent_adherence_percent=intensity_intent_adherence_percent,
        matched_duration_percent=matched_duration_percent,
        planned_volume_completion_percent=planned_volume_completion_percent,
        all_actual_duration_seconds=all_actual_duration_seconds,
        density=density,
        comparable_session_count=comparable_session_count,
        variance_flag_count=variance_flag_count,
        variance_flag_note=variance_flag_note,
        volume_range_status_by_discipline=volume_range_status_by_discipline,
        blended_volume_range_status=blended_volume_range_status,
        thriving_gate=thriving_gate,
        efficiency_factor_by_discipline=efficiency_factor_by_discipline,
        signal=signal,
        signal_reasons=signal_reasons,
    )


def _resolve_signal(
    *,
    session_completion_percent: float | None,
    comparable_session_count: int,
    density: float | None,
    thriving_gate: ThrivingGateResult,
) -> tuple[WeeklySignal, tuple[str, ...]]:
    if (
        session_completion_percent is None
        or session_completion_percent < c.MIN_EVIDENCE_COMPLETION_PERCENT
        or comparable_session_count < c.MIN_EVIDENCE_COMPARABLE_SESSIONS
    ):
        return WeeklySignal.INSUFFICIENT_EVIDENCE, (
            "session_completion_percent="
            f"{session_completion_percent!r} below the "
            f"{c.MIN_EVIDENCE_COMPLETION_PERCENT}% minimum-evidence bar, or "
            f"comparable_session_count={comparable_session_count} below the "
            f"minimum of {c.MIN_EVIDENCE_COMPARABLE_SESSIONS}",
        )

    assert density is not None  # comparable_session_count >= 1 guarantees this
    if density >= c.DENSITY_BACK_OFF_MIN:
        return WeeklySignal.BACK_OFF, (
            f"density={density:.2f} >= {c.DENSITY_BACK_OFF_MIN}",
        )
    if density >= c.DENSITY_WATCH_EFFORT_MIN:
        return WeeklySignal.WATCH_EFFORT, (
            f"density={density:.2f} >= {c.DENSITY_WATCH_EFFORT_MIN}",
        )
    if thriving_gate.met:
        return WeeklySignal.THRIVING, (
            f"density={density:.2f} < {c.DENSITY_WATCH_EFFORT_MIN}",
            "THRIVING gate met",
        )
    return WeeklySignal.ON_TRACK, (
        f"density={density:.2f} < {c.DENSITY_WATCH_EFFORT_MIN}",
        "THRIVING gate not met",
    )
