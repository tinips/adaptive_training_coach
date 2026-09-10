"""Weekly aggregate and signal: pin every named denominator and rule boundary.

Test-first step 7 of docs/briefs/backlog/first-week-evaluator.md.

Reproduces the design doc's `3/4`, `131/130`, and `131/175` worked-example
numbers. Its `166 min` total-actual-volume figure is stale (pre-2026-09-08;
the design doc's own text says the current correct number is `131 min`,
matched sessions only -- see docs/decisions/locked.md and the design doc's
own correction note), so `131 min` is pinned here instead, not `166`. Its
`2/3` intent-adherence figure is also stale: it predates the 2026-09-10
"Signal scoring..." revision that excludes strength (always NOT_COMPARABLE
intent, matched-not-flagged) from the comparable-effort denominator: under
the current model this specific example computes 1/2, not 2/3, and this
file pins the current, correct value rather than the superseded one.
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest

from app.domain.enums import (
    Discipline,
    PlannedSessionLinkStatus,
    SessionIntentVerdict,
    SessionOutputVerdict,
    VolumeRangeStatus,
    WeeklySignal,
)
from app.schemas.weekly_evaluation import PerSessionInsight
from app.services.weekly_evaluation.aggregation import aggregate_week

PLAN_ID = uuid.uuid4()


def _insight(
    *,
    discipline: Discipline = Discipline.RUNNING,
    status: PlannedSessionLinkStatus = PlannedSessionLinkStatus.MATCHED,
    planned_duration_seconds: int | None = 2400,
    actual_duration_seconds: int | None = 2400,
    intent_verdict: SessionIntentVerdict = SessionIntentVerdict.AS_PRESCRIBED,
    output_verdict: SessionOutputVerdict = SessionOutputVerdict.WITHIN_EXPECTED_OUTPUT,
    output_metric: str | None = "PACE_SECONDS_PER_KM",
    actual_output_value: float | None = 300.0,
    actual_average_hr_bpm: float | None = 120.0,
    planned_distance_range_meters: tuple[float, float] | None = None,
    actual_distance_meters: float | None = None,
    variance_flag: bool = False,
    positive_efficiency_evidence: bool = False,
) -> PerSessionInsight:
    intent_point = (
        {"EASIER_THAN_EXPECTED": -1, "AS_PRESCRIBED": 0, "OVERCOOKED": 1}[
            intent_verdict.value
        ]
        if intent_verdict != SessionIntentVerdict.NOT_COMPARABLE
        else None
    )
    output_point = (
        {
            "BELOW_EXPECTED_OUTPUT": 1,
            "WITHIN_EXPECTED_OUTPUT": 0,
            "ABOVE_EXPECTED_OUTPUT": -1,
        }[output_verdict.value]
        if output_verdict != SessionOutputVerdict.UNKNOWN
        else None
    )
    point_value = (
        intent_point + output_point
        if intent_point is not None and output_point is not None
        else None
    )
    return PerSessionInsight(
        plan_session_id=uuid.uuid4(),
        workout_id=uuid.uuid4() if status == PlannedSessionLinkStatus.MATCHED else None,
        status=status,
        discipline=discipline,
        planned_duration_seconds=planned_duration_seconds,
        actual_duration_seconds=(
            actual_duration_seconds
            if status == PlannedSessionLinkStatus.MATCHED
            else None
        ),
        planned_distance_range_meters=planned_distance_range_meters,
        actual_distance_meters=actual_distance_meters,
        output_metric=output_metric,  # type: ignore[arg-type]
        actual_output_value=actual_output_value,
        output_verdict=output_verdict,
        actual_average_hr_bpm=actual_average_hr_bpm,
        intent_verdict=intent_verdict,
        intent_point_value=intent_point,
        output_point_value=output_point,
        point_value=point_value,
        variance_flag=variance_flag,
        positive_efficiency_evidence=positive_efficiency_evidence,
    )


class TestWorkedWeeklyExample:
    """docs/design/first-week-evaluator.md, "Worked weekly example"."""

    def _week(self):
        easy_run = _insight(
            planned_duration_seconds=40 * 60,
            actual_duration_seconds=40 * 60,
            intent_verdict=SessionIntentVerdict.AS_PRESCRIBED,
            output_verdict=SessionOutputVerdict.WITHIN_EXPECTED_OUTPUT,
        )
        moderate_ride = _insight(
            discipline=Discipline.CYCLING,
            planned_duration_seconds=60 * 60,
            actual_duration_seconds=63 * 60,
            intent_verdict=SessionIntentVerdict.OVERCOOKED,
            output_verdict=SessionOutputVerdict.WITHIN_EXPECTED_OUTPUT,
            output_metric="POWER_WATTS",
            actual_output_value=210.0,
        )
        easy_strength = _insight(
            discipline=Discipline.STRENGTH,
            planned_duration_seconds=30 * 60,
            actual_duration_seconds=28 * 60,
            intent_verdict=SessionIntentVerdict.NOT_COMPARABLE,
            output_verdict=SessionOutputVerdict.UNKNOWN,
            output_metric=None,
            actual_output_value=None,
            actual_average_hr_bpm=None,
        )
        steady_run_missed = _insight(
            status=PlannedSessionLinkStatus.MISSED,
            planned_duration_seconds=45 * 60,
        )
        return aggregate_week(
            plan_id=PLAN_ID,
            plan_revision=1,
            week_start=date(2026, 9, 14),
            timezone="UTC",
            insights=(easy_run, moderate_ride, easy_strength, steady_run_missed),
        )

    def test_session_completion_is_three_of_four(self) -> None:
        week = self._week()
        assert week.matched_count == 3
        assert week.missed_count == 1
        assert week.session_completion_percent == pytest.approx(75.0)

    def test_matched_duration_ratio_is_131_over_130(self) -> None:
        week = self._week()
        assert week.matched_duration_percent == pytest.approx(131 / 130 * 100, rel=1e-3)

    def test_planned_volume_completion_is_131_over_175(self) -> None:
        week = self._week()
        assert week.planned_volume_completion_percent == pytest.approx(
            131 / 175 * 100, rel=1e-3
        )

    def test_total_actual_weekly_volume_is_131_minutes_not_the_stale_166(
        self,
    ) -> None:
        week = self._week()
        assert week.all_actual_duration_seconds == 131 * 60

    def test_strength_is_excluded_from_intensity_intent_adherence(self) -> None:
        week = self._week()
        # Comparable-effort denominator is {easy_run, moderate_ride} only;
        # strength is always NOT_COMPARABLE. 1 of 2 (easy_run) was
        # AS_PRESCRIBED. (Superseded 2/3 figure predates this exclusion.)
        assert week.intensity_intent_adherence_percent == pytest.approx(50.0)


class TestDensityBands:
    def test_negative_density_still_lands_on_track_or_thriving(self) -> None:
        insights = (
            _insight(
                intent_verdict=SessionIntentVerdict.EASIER_THAN_EXPECTED,
                output_verdict=SessionOutputVerdict.WITHIN_EXPECTED_OUTPUT,
            ),
        )
        week = aggregate_week(
            plan_id=PLAN_ID,
            plan_revision=1,
            week_start=date(2026, 9, 14),
            timezone="UTC",
            insights=insights,
        )
        assert week.density == pytest.approx(-1.0)
        assert week.signal in (WeeklySignal.ON_TRACK, WeeklySignal.THRIVING)

    def test_density_at_or_above_0_6_is_back_off(self) -> None:
        insights = tuple(
            _insight(
                intent_verdict=SessionIntentVerdict.OVERCOOKED,
                output_verdict=SessionOutputVerdict.BELOW_EXPECTED_OUTPUT,
            )
            for _ in range(3)
        )
        week = aggregate_week(
            plan_id=PLAN_ID,
            plan_revision=1,
            week_start=date(2026, 9, 14),
            timezone="UTC",
            insights=insights,
        )
        assert week.density == pytest.approx(2.0)
        assert week.signal is WeeklySignal.BACK_OFF

    def test_density_between_0_3_and_0_6_is_watch_effort(self) -> None:
        insights = (
            _insight(
                intent_verdict=SessionIntentVerdict.OVERCOOKED,
                output_verdict=SessionOutputVerdict.WITHIN_EXPECTED_OUTPUT,
            ),
            _insight(
                intent_verdict=SessionIntentVerdict.AS_PRESCRIBED,
                output_verdict=SessionOutputVerdict.WITHIN_EXPECTED_OUTPUT,
            ),
        )
        week = aggregate_week(
            plan_id=PLAN_ID,
            plan_revision=1,
            week_start=date(2026, 9, 14),
            timezone="UTC",
            insights=insights,
        )
        assert week.density == pytest.approx(0.5)
        assert week.signal is WeeklySignal.WATCH_EFFORT


class TestVarianceFlagNoteTrigger:
    def test_note_appears_at_exactly_two(self) -> None:
        insights = tuple(
            _insight(
                intent_verdict=SessionIntentVerdict.OVERCOOKED,
                output_verdict=SessionOutputVerdict.ABOVE_EXPECTED_OUTPUT,
                variance_flag=True,
            )
            for _ in range(2)
        )
        week = aggregate_week(
            plan_id=PLAN_ID,
            plan_revision=1,
            week_start=date(2026, 9, 14),
            timezone="UTC",
            insights=insights,
        )
        assert week.variance_flag_count == 2
        assert week.variance_flag_note is not None
        assert "2 workouts" in week.variance_flag_note

    def test_no_note_below_two(self) -> None:
        insights = (
            _insight(
                intent_verdict=SessionIntentVerdict.OVERCOOKED,
                output_verdict=SessionOutputVerdict.ABOVE_EXPECTED_OUTPUT,
                variance_flag=True,
            ),
        )
        week = aggregate_week(
            plan_id=PLAN_ID,
            plan_revision=1,
            week_start=date(2026, 9, 14),
            timezone="UTC",
            insights=insights,
        )
        assert week.variance_flag_count == 1
        assert week.variance_flag_note is None


class TestWeeklyVolumeRangeStatus:
    def test_within_range(self) -> None:
        insights = (
            _insight(
                planned_distance_range_meters=(8000.0, 9000.0),
                actual_distance_meters=8500.0,
            ),
        )
        week = aggregate_week(
            plan_id=PLAN_ID,
            plan_revision=1,
            week_start=date(2026, 9, 14),
            timezone="UTC",
            insights=insights,
        )
        assert week.blended_volume_range_status.status is VolumeRangeStatus.WITHIN_RANGE
        assert week.blended_volume_range_status.percent is None

    def test_below_range_reports_percent_of_minimum(self) -> None:
        insights = (
            _insight(
                planned_distance_range_meters=(8000.0, 9000.0),
                actual_distance_meters=4000.0,
            ),
        )
        week = aggregate_week(
            plan_id=PLAN_ID,
            plan_revision=1,
            week_start=date(2026, 9, 14),
            timezone="UTC",
            insights=insights,
        )
        assert week.blended_volume_range_status.status is VolumeRangeStatus.BELOW_RANGE
        assert week.blended_volume_range_status.percent == pytest.approx(50.0)

    def test_above_range_reports_percent_of_maximum(self) -> None:
        insights = (
            _insight(
                planned_distance_range_meters=(8000.0, 9000.0),
                actual_distance_meters=18000.0,
            ),
        )
        week = aggregate_week(
            plan_id=PLAN_ID,
            plan_revision=1,
            week_start=date(2026, 9, 14),
            timezone="UTC",
            insights=insights,
        )
        assert week.blended_volume_range_status.status is VolumeRangeStatus.ABOVE_RANGE
        assert week.blended_volume_range_status.percent == pytest.approx(200.0)

    def test_computed_per_discipline_and_once_more_blended(self) -> None:
        run = _insight(
            discipline=Discipline.RUNNING,
            planned_distance_range_meters=(8000.0, 9000.0),
            actual_distance_meters=8500.0,
        )
        ride = _insight(
            discipline=Discipline.CYCLING,
            output_metric="POWER_WATTS",
            planned_distance_range_meters=(20000.0, 25000.0),
            actual_distance_meters=10000.0,  # well under range
        )
        week = aggregate_week(
            plan_id=PLAN_ID,
            plan_revision=1,
            week_start=date(2026, 9, 14),
            timezone="UTC",
            insights=(run, ride),
        )
        assert (
            week.volume_range_status_by_discipline[Discipline.RUNNING].status
            is VolumeRangeStatus.WITHIN_RANGE
        )
        assert (
            week.volume_range_status_by_discipline[Discipline.CYCLING].status
            is VolumeRangeStatus.BELOW_RANGE
        )
        # Blended sums both disciplines' ranges/actuals into one status.
        assert week.blended_volume_range_status.range_min == pytest.approx(28000.0)
        assert week.blended_volume_range_status.actual == pytest.approx(18500.0)

    def test_strength_never_contributes_to_volume_status(self) -> None:
        strength = _insight(
            discipline=Discipline.STRENGTH,
            output_metric=None,
            actual_output_value=None,
            actual_average_hr_bpm=None,
            planned_distance_range_meters=None,
            actual_distance_meters=None,
        )
        week = aggregate_week(
            plan_id=PLAN_ID,
            plan_revision=1,
            week_start=date(2026, 9, 14),
            timezone="UTC",
            insights=(strength,),
        )
        assert Discipline.STRENGTH not in week.volume_range_status_by_discipline
        assert week.blended_volume_range_status.status is VolumeRangeStatus.UNKNOWN

    def test_no_distance_target_that_week_is_unknown_not_zero(self) -> None:
        insights = (
            _insight(planned_distance_range_meters=None, actual_distance_meters=None),
        )
        week = aggregate_week(
            plan_id=PLAN_ID,
            plan_revision=1,
            week_start=date(2026, 9, 14),
            timezone="UTC",
            insights=insights,
        )
        assert week.blended_volume_range_status.status is VolumeRangeStatus.UNKNOWN


class TestThrivingGate:
    def _good_week_insights(self):
        return (
            _insight(
                intent_verdict=SessionIntentVerdict.EASIER_THAN_EXPECTED,
                output_verdict=SessionOutputVerdict.ABOVE_EXPECTED_OUTPUT,
                planned_distance_range_meters=(8000.0, 9000.0),
                actual_distance_meters=8500.0,
                positive_efficiency_evidence=True,
            ),
        )

    def test_all_conditions_met_reaches_thriving(self) -> None:
        week = aggregate_week(
            plan_id=PLAN_ID,
            plan_revision=1,
            week_start=date(2026, 9, 14),
            timezone="UTC",
            insights=self._good_week_insights(),
        )
        assert week.thriving_gate.met is True
        assert week.signal is WeeklySignal.THRIVING

    def test_any_overcooked_session_blocks_thriving(self) -> None:
        insights = (
            *self._good_week_insights(),
            _insight(
                intent_verdict=SessionIntentVerdict.OVERCOOKED,
                output_verdict=SessionOutputVerdict.WITHIN_EXPECTED_OUTPUT,
            ),
        )
        week = aggregate_week(
            plan_id=PLAN_ID,
            plan_revision=1,
            week_start=date(2026, 9, 14),
            timezone="UTC",
            insights=insights,
        )
        assert week.thriving_gate.zero_overcooked is False
        assert week.thriving_gate.met is False
        assert week.signal is WeeklySignal.ON_TRACK

    def test_any_below_expected_output_blocks_thriving(self) -> None:
        insights = (
            *self._good_week_insights(),
            _insight(
                intent_verdict=SessionIntentVerdict.AS_PRESCRIBED,
                output_verdict=SessionOutputVerdict.BELOW_EXPECTED_OUTPUT,
            ),
        )
        week = aggregate_week(
            plan_id=PLAN_ID,
            plan_revision=1,
            week_start=date(2026, 9, 14),
            timezone="UTC",
            insights=insights,
        )
        assert week.thriving_gate.zero_below_expected_output is False
        assert week.thriving_gate.met is False

    def test_a_variance_flag_blocks_thriving(self) -> None:
        insights = (
            *self._good_week_insights(),
            _insight(
                intent_verdict=SessionIntentVerdict.OVERCOOKED,
                output_verdict=SessionOutputVerdict.ABOVE_EXPECTED_OUTPUT,
                variance_flag=True,
            ),
        )
        week = aggregate_week(
            plan_id=PLAN_ID,
            plan_revision=1,
            week_start=date(2026, 9, 14),
            timezone="UTC",
            insights=insights,
        )
        assert week.thriving_gate.zero_variance_flag is False
        assert week.thriving_gate.met is False

    def test_below_range_volume_blocks_thriving(self) -> None:
        insights = (
            _insight(
                intent_verdict=SessionIntentVerdict.EASIER_THAN_EXPECTED,
                output_verdict=SessionOutputVerdict.ABOVE_EXPECTED_OUTPUT,
                planned_distance_range_meters=(8000.0, 9000.0),
                actual_distance_meters=1000.0,  # far under range
                positive_efficiency_evidence=True,
            ),
        )
        week = aggregate_week(
            plan_id=PLAN_ID,
            plan_revision=1,
            week_start=date(2026, 9, 14),
            timezone="UTC",
            insights=insights,
        )
        assert week.thriving_gate.volume_not_below_range is False
        assert week.thriving_gate.met is False

    def test_no_positive_efficiency_session_blocks_thriving(self) -> None:
        insights = (
            _insight(
                intent_verdict=SessionIntentVerdict.EASIER_THAN_EXPECTED,
                output_verdict=SessionOutputVerdict.WITHIN_EXPECTED_OUTPUT,
                positive_efficiency_evidence=False,
            ),
        )
        week = aggregate_week(
            plan_id=PLAN_ID,
            plan_revision=1,
            week_start=date(2026, 9, 14),
            timezone="UTC",
            insights=insights,
        )
        assert week.thriving_gate.has_positive_efficiency_session is False
        assert week.thriving_gate.met is False
        assert week.signal is WeeklySignal.ON_TRACK


class TestSignalPrecedence:
    def test_insufficient_evidence_overrides_everything_else(self) -> None:
        insights = (
            _insight(
                status=PlannedSessionLinkStatus.MISSED, planned_duration_seconds=2400
            ),
            _insight(
                status=PlannedSessionLinkStatus.MISSED, planned_duration_seconds=2400
            ),
            _insight(
                status=PlannedSessionLinkStatus.MISSED, planned_duration_seconds=2400
            ),
            _insight(),  # 1 matched of 4 planned = 25%, below the 75% bar
        )
        week = aggregate_week(
            plan_id=PLAN_ID,
            plan_revision=1,
            week_start=date(2026, 9, 14),
            timezone="UTC",
            insights=insights,
        )
        assert week.signal is WeeklySignal.INSUFFICIENT_EVIDENCE

    def test_zero_planned_sessions_is_insufficient_evidence_not_a_crash(self) -> None:
        week = aggregate_week(
            plan_id=PLAN_ID,
            plan_revision=1,
            week_start=date(2026, 9, 14),
            timezone="UTC",
            insights=(),
        )
        assert week.signal is WeeklySignal.INSUFFICIENT_EVIDENCE
        assert week.session_completion_percent is None

    def test_a_missed_session_alone_never_forces_back_off(self) -> None:
        insights = (
            _insight(
                intent_verdict=SessionIntentVerdict.AS_PRESCRIBED,
                output_verdict=SessionOutputVerdict.WITHIN_EXPECTED_OUTPUT,
            ),
            _insight(
                intent_verdict=SessionIntentVerdict.AS_PRESCRIBED,
                output_verdict=SessionOutputVerdict.WITHIN_EXPECTED_OUTPUT,
            ),
            _insight(
                intent_verdict=SessionIntentVerdict.AS_PRESCRIBED,
                output_verdict=SessionOutputVerdict.WITHIN_EXPECTED_OUTPUT,
            ),
            _insight(status=PlannedSessionLinkStatus.MISSED),
        )
        week = aggregate_week(
            plan_id=PLAN_ID,
            plan_revision=1,
            week_start=date(2026, 9, 14),
            timezone="UTC",
            insights=insights,
        )
        assert week.signal != WeeklySignal.BACK_OFF


class TestEfficiencyFactorByDiscipline:
    def test_computed_per_discipline_never_blended(self) -> None:
        run = _insight(
            discipline=Discipline.RUNNING,
            output_metric="PACE_SECONDS_PER_KM",
            actual_output_value=300.0,  # -> 12 km/h
            actual_average_hr_bpm=120.0,
        )
        ride = _insight(
            discipline=Discipline.CYCLING,
            output_metric="POWER_WATTS",
            actual_output_value=200.0,
            actual_average_hr_bpm=140.0,
        )
        week = aggregate_week(
            plan_id=PLAN_ID,
            plan_revision=1,
            week_start=date(2026, 9, 14),
            timezone="UTC",
            insights=(run, ride),
        )
        assert week.efficiency_factor_by_discipline[
            Discipline.RUNNING
        ] == pytest.approx(12.0 / 120.0)
        assert week.efficiency_factor_by_discipline[
            Discipline.CYCLING
        ] == pytest.approx(200.0 / 140.0)
