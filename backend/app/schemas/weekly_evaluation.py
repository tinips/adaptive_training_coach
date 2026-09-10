"""Typed contracts for the deterministic first-week evaluator.

See docs/briefs/backlog/first-week-evaluator.md, "Evaluator evidence
projection" and "Proposed PerSessionInsight contract" in
docs/design/first-week-evaluator.md. Deliberately not a reuse of
FitnessWorkoutEvidence (app/schemas/fitness.py): that projection omits
cycling power/speed and numeric summary HR, both required here.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.domain.enums import (
    ActivitySource,
    Discipline,
    HeartRateTemporalQuality,
    PlannedSessionLinkStatus,
    SessionIntentVerdict,
    SessionOutputVerdict,
    VolumeRangeStatus,
    WeeklySignal,
)


class _EvaluationSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EvaluatorHeartRateObservation(_EvaluationSchema):
    """One retained HR sample, mirroring app.schemas.fitness.HeartRateEvidence."""

    started_at: datetime
    ended_at: datetime
    beats_per_minute: float
    temporal_quality: HeartRateTemporalQuality


OutputMetric = Literal[
    "PACE_SECONDS_PER_KM", "SWIM_PACE_SECONDS_PER_100M", "POWER_WATTS"
]


class EvaluatorWorkoutEvidence(_EvaluationSchema):
    """Read-only actual-workout facts available to the evaluator.

    Source-selection rules (docs/design/first-week-evaluator.md, "Per-session
    comparison"): canonical moving duration precedes elapsed; a stored
    summary average HR precedes a sampled average; missing data is None, not
    zero. Every value here is already the code-selected one; provenance is
    carried on ``duration_source``.
    """

    workout_id: UUID
    discipline: Discipline
    source: ActivitySource
    started_at: datetime

    duration_seconds: int  # elapsed (Workout.duration_seconds), always present
    moving_duration_seconds: int | None = None
    duration_source: Literal["MOVING", "ELAPSED"]

    distance_meters: float | None = None

    # Discipline-specific canonical objective metrics. At most one pace field
    # and/or the power field is populated, matching the workout's discipline.
    canonical_pace_seconds_per_km: float | None = None
    canonical_pace_seconds_per_100m: float | None = None
    average_speed_kph: float | None = None
    average_power_watts: int | None = None
    max_power_watts: int | None = None

    average_heart_rate_bpm: float | None = None
    max_heart_rate_bpm: float | None = None
    heart_rate_observations: tuple[EvaluatorHeartRateObservation, ...] = ()

    quality_flags: tuple[str, ...] = ()


class ResolvedHeartRate(_EvaluationSchema):
    """The single effective average HR used for one session's comparison."""

    average_bpm: float | None
    quality_flags: tuple[str, ...] = ()


class PerSessionInsight(_EvaluationSchema):
    """One matched-or-missed planned session's deterministic comparison.

    Field groups follow docs/design/first-week-evaluator.md, "Proposed
    PerSessionInsight contract".
    """

    plan_session_id: UUID
    workout_id: UUID | None
    status: PlannedSessionLinkStatus
    discipline: Discipline

    # Duration
    planned_duration_seconds: int | None = None
    actual_duration_seconds: int | None = None
    duration_delta_seconds: int | None = None
    duration_delta_percent: float | None = None
    duration_source: Literal["MOVING", "ELAPSED"] | None = None

    # Distance
    planned_distance_range_meters: tuple[float, float] | None = None
    actual_distance_meters: float | None = None
    distance_delta_meters: float | None = None
    distance_delta_percent: float | None = None

    # Primary output (pace/power)
    output_metric: OutputMetric | None = None
    planned_output_range: tuple[float, float] | None = None
    actual_output_value: float | None = None
    output_verdict: SessionOutputVerdict = SessionOutputVerdict.UNKNOWN

    # Effort / intent
    reference_hr_band: Literal["EASY", "MODERATE", "HARD"] | None = None
    reference_hr_range_bpm: tuple[float, float] | None = None
    actual_average_hr_bpm: float | None = None
    intent_verdict: SessionIntentVerdict = SessionIntentVerdict.NOT_COMPARABLE

    # Score (locked.md, "Per-session score" and "Variance flag")
    intent_point_value: int | None = None
    output_point_value: int | None = None
    point_value: int | None = None
    variance_flag: bool = False

    # Ratio check / positive efficiency evidence (locked.md, "Ratio check...")
    ratio_value: float | None = None
    ratio_min: float | None = None
    ratio_max: float | None = None
    positive_efficiency_evidence: bool = False

    quality_flags: tuple[str, ...] = ()


class WeeklyVolumeRangeResult(_EvaluationSchema):
    """Summed actual volume against the summed planned distance range.

    docs/decisions/locked.md, "Weekly volume range status". `percent` is
    only populated for BELOW_RANGE (`actual/range_min*100`) or ABOVE_RANGE
    (`actual/range_max*100`); WITHIN_RANGE is reported flat, no percent.
    UNKNOWN means no discipline in scope carried a real distance target
    that week (nothing to compare).
    """

    status: VolumeRangeStatus
    percent: float | None = None
    range_min: float | None = None
    range_max: float | None = None
    actual: float | None = None


class ThrivingGateResult(_EvaluationSchema):
    """Each of the five THRIVING conditions, individually, plus the verdict.

    docs/decisions/locked.md, "THRIVING gate": any one condition failing
    alone keeps the week at ON_TRACK.
    """

    met: bool
    zero_overcooked: bool
    zero_below_expected_output: bool
    zero_variance_flag: bool
    volume_not_below_range: bool
    has_positive_efficiency_session: bool


class WeeklyEvaluation(_EvaluationSchema):
    """The evaluator's full weekly output: facts, quality flags, and signal.

    Name and physical schema are PROPOSED in the design doc; the facts are
    DESIGNED. See docs/design/first-week-evaluator.md, "Proposed
    WeeklyEvaluation contract".
    """

    plan_id: UUID
    plan_revision: int
    week_start: date
    timezone: str | None
    evaluator_version: int

    per_session_insights: tuple[PerSessionInsight, ...]

    matched_count: int
    missed_count: int
    session_completion_percent: float | None

    matched_metric_coverage_percent: float | None
    intensity_intent_adherence_percent: float | None

    matched_duration_percent: float | None
    planned_volume_completion_percent: float | None
    all_actual_duration_seconds: int

    density: float | None
    comparable_session_count: int

    variance_flag_count: int
    variance_flag_note: str | None

    volume_range_status_by_discipline: dict[Discipline, WeeklyVolumeRangeResult]
    blended_volume_range_status: WeeklyVolumeRangeResult

    thriving_gate: ThrivingGateResult
    efficiency_factor_by_discipline: dict[Discipline, float | None]

    signal: WeeklySignal
    signal_reasons: tuple[str, ...]
