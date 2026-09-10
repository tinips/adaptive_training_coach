"""Per-session pure comparison: pin the design doc's worked examples.

Test-first step 5 of docs/briefs/backlog/first-week-evaluator.md. Design
examples 1 and 2 in docs/design/first-week-evaluator.md carry a 2026-09-10
revision note explicitly warning not to treat them as automatically
qualifying for positive efficiency evidence without a real HR number to
check the ratio -- this file pins examples 3 and 4 (unaffected) and adds its
own fully-specified ratio-check cases instead of guessing at 1/2's HR.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from app.domain.enums import (
    ActivitySource,
    Discipline,
    HeartRateTemporalQuality,
    PlannedSessionLinkStatus,
    SessionIntentVerdict,
    SessionOutputVerdict,
)
from app.schemas.weekly_evaluation import (
    EvaluatorHeartRateObservation,
    EvaluatorWorkoutEvidence,
)
from app.schemas.weekly_plans import FirstWeekEnduranceSession, FirstWeekStrengthSession
from app.services.athlete_zones import resolve_reference_hr_zones
from app.services.weekly_evaluation.comparison import compare_session

# Design doc's illustrative 40-year-old athlete (Tanaka: 208 - 0.7*40 = 180 bpm).
ZONES = resolve_reference_hr_zones(birth_year=1986, current_year=2026)


def _running_session(
    *,
    pace_range: tuple[float, float] = (277.0, 315.0),
    rpe_range: tuple[int, int] = (2, 3),
    distance_range: tuple[float, float] = (8000.0, 8000.0),
    purpose: str = "Easy aerobic run.",
    execution: str = "Keep it conversational.",
    guidance: str = "Stay relaxed.",
) -> FirstWeekEnduranceSession:
    return FirstWeekEnduranceSession(
        discipline=Discipline.RUNNING,
        purpose=purpose,
        intensity={
            "metric": "PACE_SECONDS_PER_KM",
            "target_range": list(pace_range),
            "rpe_range": list(rpe_range),
            "guidance": guidance,
        },
        objective="Cover the distance at the prescribed easy pace.",
        targets={"distance_range_meters": distance_range},
        execution=execution,
    )


def _swim_session(
    *,
    pace_range: tuple[float, float] = (100.0, 110.0),
    rpe_range: tuple[int, int] = (2, 3),
    distance_range: tuple[float, float] = (1500.0, 1500.0),
) -> FirstWeekEnduranceSession:
    return FirstWeekEnduranceSession(
        discipline=Discipline.SWIMMING,
        purpose="Steady swim.",
        intensity={
            "metric": "SWIM_PACE_SECONDS_PER_100M",
            "target_range": list(pace_range),
            "rpe_range": list(rpe_range),
            "guidance": "Controlled, repeatable pace.",
        },
        objective="Cover the distance at the prescribed pace.",
        targets={"distance_range_meters": distance_range},
        execution="Hold a controlled pace.",
    )


def _cycling_session(
    *,
    power_range: tuple[float, float] = (200.0, 220.0),
    rpe_range: tuple[int, int] = (5, 6),
    duration_minutes: int = 60,
) -> FirstWeekEnduranceSession:
    return FirstWeekEnduranceSession(
        discipline=Discipline.CYCLING,
        purpose="Sweet-spot ride.",
        intensity={
            "metric": "POWER_WATTS",
            "target_range": list(power_range),
            "rpe_range": list(rpe_range),
            "guidance": "Steady sweet-spot effort.",
        },
        objective="Hold the prescribed power.",
        targets={"duration_minutes": duration_minutes},
        execution="Steady power, controlled cadence.",
    )


def _rpe_fallback_running_session(
    *, rpe_range: tuple[int, int] = (2, 3), duration_minutes: int = 40
) -> FirstWeekEnduranceSession:
    rpe_floats = [float(v) for v in rpe_range]
    return FirstWeekEnduranceSession(
        discipline=Discipline.RUNNING,
        purpose="Easy effort run, no pace data yet.",
        intensity={
            "metric": "RPE",
            "target_range": rpe_floats,
            "rpe_range": list(rpe_range),
            "guidance": "Run by feel.",
        },
        objective="Move easy, by feel.",
        targets={"duration_minutes": duration_minutes},
        execution="Run easy by feel.",
    )


def _strength_session(
    *, rpe_range: tuple[int, int] = (2, 3)
) -> FirstWeekStrengthSession:
    return FirstWeekStrengthSession(
        discipline=Discipline.STRENGTH,
        purpose="Supportive full-body strength.",
        intensity={
            "metric": "RPE",
            "target_range": [float(v) for v in rpe_range],
            "rpe_range": list(rpe_range),
            "guidance": "Controlled tempo throughout.",
        },
        objective="Move well under light, controlled load.",
        targets={"duration_minutes": 30},
        execution="Full-body circuit, controlled tempo.",
    )


def _running_evidence(
    *,
    duration_seconds: int,
    distance_meters: float,
    average_heart_rate_bpm: float | None,
) -> EvaluatorWorkoutEvidence:
    pace = duration_seconds / (distance_meters / 1000)
    return EvaluatorWorkoutEvidence(
        workout_id=uuid.uuid4(),
        discipline=Discipline.RUNNING,
        source=ActivitySource.MANUAL,
        started_at=datetime(2026, 9, 15, tzinfo=UTC),
        duration_seconds=duration_seconds,
        moving_duration_seconds=duration_seconds,
        duration_source="MOVING",
        distance_meters=distance_meters,
        canonical_pace_seconds_per_km=pace,
        average_heart_rate_bpm=average_heart_rate_bpm,
    )


class TestDesignExample3Overcooked:
    """40-min easy run plan; actual 40 min / 9.0 km / avg HR 152 bpm."""

    def test_output_is_above_expected_and_intent_is_overcooked(self) -> None:
        planned = _running_session()
        actual = _running_evidence(
            duration_seconds=2400, distance_meters=9000.0, average_heart_rate_bpm=152.0
        )

        insight = compare_session(
            planned=planned, actual=actual, reference_hr_zones=ZONES
        )

        assert insight.output_verdict is SessionOutputVerdict.ABOVE_EXPECTED_OUTPUT
        assert insight.intent_verdict is SessionIntentVerdict.OVERCOOKED
        assert insight.reference_hr_band == "EASY"

    def test_overcooked_above_expected_output_carries_the_variance_flag(self) -> None:
        planned = _running_session()
        actual = _running_evidence(
            duration_seconds=2400, distance_meters=9000.0, average_heart_rate_bpm=152.0
        )

        insight = compare_session(
            planned=planned, actual=actual, reference_hr_zones=ZONES
        )

        assert insight.variance_flag is True
        # OVERCOOKED (+1) + ABOVE_EXPECTED_OUTPUT (-1) = 0, by construction.
        assert insight.point_value == 0

    def test_extra_distance_from_overcooked_effort_is_not_positive_efficiency(
        self,
    ) -> None:
        planned = _running_session()
        actual = _running_evidence(
            duration_seconds=2400, distance_meters=9000.0, average_heart_rate_bpm=152.0
        )

        insight = compare_session(
            planned=planned, actual=actual, reference_hr_zones=ZONES
        )

        assert insight.positive_efficiency_evidence is False


class TestDesignExample4PaceOnTargetHrHigh:
    """Same plan; actual 40 min / 8.0 km / avg HR 149 bpm."""

    def test_output_within_expected_and_intent_overcooked(self) -> None:
        planned = _running_session()
        actual = _running_evidence(
            duration_seconds=2400, distance_meters=8000.0, average_heart_rate_bpm=149.0
        )

        insight = compare_session(
            planned=planned, actual=actual, reference_hr_zones=ZONES
        )

        assert insight.output_verdict is SessionOutputVerdict.WITHIN_EXPECTED_OUTPUT
        assert insight.intent_verdict is SessionIntentVerdict.OVERCOOKED

    def test_output_within_and_intent_overcooked_does_not_carry_variance_flag(
        self,
    ) -> None:
        planned = _running_session()
        actual = _running_evidence(
            duration_seconds=2400, distance_meters=8000.0, average_heart_rate_bpm=149.0
        )

        insight = compare_session(
            planned=planned, actual=actual, reference_hr_zones=ZONES
        )

        assert insight.variance_flag is False
        # OVERCOOKED (+1) + WITHIN_EXPECTED_OUTPUT (0) = +1.
        assert insight.point_value == 1


class TestOutputVerdictDirectionality:
    @pytest.mark.parametrize(
        ("actual_pace", "expected"),
        [
            (320.0, SessionOutputVerdict.BELOW_EXPECTED_OUTPUT),  # slower than upper
            (300.0, SessionOutputVerdict.WITHIN_EXPECTED_OUTPUT),
            (260.0, SessionOutputVerdict.ABOVE_EXPECTED_OUTPUT),  # faster than lower
        ],
    )
    def test_running_pace_direction(
        self, actual_pace: float, expected: SessionOutputVerdict
    ) -> None:
        planned = _running_session(pace_range=(277.0, 315.0))
        actual = _running_evidence(
            duration_seconds=int(actual_pace * 8),
            distance_meters=8000.0,
            average_heart_rate_bpm=120.0,
        )
        # Force the exact pace regardless of rounding from duration/distance.
        actual = actual.model_copy(
            update={"canonical_pace_seconds_per_km": actual_pace}
        )

        insight = compare_session(
            planned=planned, actual=actual, reference_hr_zones=ZONES
        )

        assert insight.output_verdict is expected

    @pytest.mark.parametrize(
        ("actual_power", "expected"),
        [
            (190.0, SessionOutputVerdict.BELOW_EXPECTED_OUTPUT),
            (210.0, SessionOutputVerdict.WITHIN_EXPECTED_OUTPUT),
            (230.0, SessionOutputVerdict.ABOVE_EXPECTED_OUTPUT),
        ],
    )
    def test_cycling_power_direction(
        self, actual_power: float, expected: SessionOutputVerdict
    ) -> None:
        planned = _cycling_session(power_range=(200.0, 220.0))
        actual = EvaluatorWorkoutEvidence(
            workout_id=uuid.uuid4(),
            discipline=Discipline.CYCLING,
            source=ActivitySource.MANUAL,
            started_at=datetime(2026, 9, 15, tzinfo=UTC),
            duration_seconds=3600,
            moving_duration_seconds=3600,
            duration_source="MOVING",
            average_power_watts=actual_power,
            average_heart_rate_bpm=150.0,
        )

        insight = compare_session(
            planned=planned, actual=actual, reference_hr_zones=ZONES
        )

        assert insight.output_verdict is expected

    @pytest.mark.parametrize(
        ("actual_pace", "expected"),
        [
            (115.0, SessionOutputVerdict.BELOW_EXPECTED_OUTPUT),
            (105.0, SessionOutputVerdict.WITHIN_EXPECTED_OUTPUT),
            (95.0, SessionOutputVerdict.ABOVE_EXPECTED_OUTPUT),
        ],
    )
    def test_swim_pace_direction(
        self, actual_pace: float, expected: SessionOutputVerdict
    ) -> None:
        planned = _swim_session(pace_range=(100.0, 110.0))
        actual = EvaluatorWorkoutEvidence(
            workout_id=uuid.uuid4(),
            discipline=Discipline.SWIMMING,
            source=ActivitySource.MANUAL,
            started_at=datetime(2026, 9, 15, tzinfo=UTC),
            duration_seconds=1800,
            moving_duration_seconds=1800,
            duration_source="MOVING",
            distance_meters=1500.0,
            canonical_pace_seconds_per_100m=actual_pace,
            average_heart_rate_bpm=120.0,
        )

        insight = compare_session(
            planned=planned, actual=actual, reference_hr_zones=ZONES
        )

        assert insight.output_verdict is expected


class TestHrIntentBandMapping:
    def test_rpe_range_crossing_bands_is_not_comparable(self) -> None:
        planned = _running_session(rpe_range=(4, 5))  # crosses easy/moderate
        actual = _running_evidence(
            duration_seconds=2400, distance_meters=8000.0, average_heart_rate_bpm=120.0
        )

        insight = compare_session(
            planned=planned, actual=actual, reference_hr_zones=ZONES
        )

        assert insight.intent_verdict is SessionIntentVerdict.NOT_COMPARABLE
        assert insight.reference_hr_band is None
        assert "AMBIGUOUS_RPE_BAND" in insight.quality_flags

    def test_missing_actual_hr_is_not_comparable(self) -> None:
        planned = _running_session()
        actual = _running_evidence(
            duration_seconds=2400, distance_meters=8000.0, average_heart_rate_bpm=None
        )

        insight = compare_session(
            planned=planned, actual=actual, reference_hr_zones=ZONES
        )

        assert insight.intent_verdict is SessionIntentVerdict.NOT_COMPARABLE
        assert insight.reference_hr_band == "EASY"  # band resolves; HR value doesn't
        assert "MISSING_HR" in insight.quality_flags

    def test_hr_inside_band_is_as_prescribed(self) -> None:
        planned = _running_session()  # easy band
        actual = _running_evidence(
            duration_seconds=2400, distance_meters=8000.0, average_heart_rate_bpm=120.0
        )

        insight = compare_session(
            planned=planned, actual=actual, reference_hr_zones=ZONES
        )

        assert insight.intent_verdict is SessionIntentVerdict.AS_PRESCRIBED

    def test_prose_is_never_parsed_for_intensity(self) -> None:
        a = _running_session(purpose="Wildly different purpose text!!")
        b = _running_session(
            purpose="Different again", execution="Totally different execution text"
        )
        actual = _running_evidence(
            duration_seconds=2400, distance_meters=8000.0, average_heart_rate_bpm=120.0
        )

        insight_a = compare_session(planned=a, actual=actual, reference_hr_zones=ZONES)
        insight_b = compare_session(planned=b, actual=actual, reference_hr_zones=ZONES)

        assert insight_a.intent_verdict == insight_b.intent_verdict
        assert insight_a.output_verdict == insight_b.output_verdict
        assert insight_a.point_value == insight_b.point_value


class TestSourceConflict:
    def test_summary_and_sampled_hr_disagreeing_beyond_threshold_is_not_comparable(
        self,
    ) -> None:
        planned = _running_session()
        actual = _running_evidence(
            duration_seconds=2400, distance_meters=8000.0, average_heart_rate_bpm=120.0
        ).model_copy(
            update={
                "heart_rate_observations": (
                    EvaluatorHeartRateObservation(
                        started_at=datetime(2026, 9, 15, tzinfo=UTC),
                        ended_at=datetime(2026, 9, 15, 0, 40, tzinfo=UTC),
                        beats_per_minute=140.0,  # 20 bpm off the 120 summary
                        temporal_quality=HeartRateTemporalQuality.EXACT_SAMPLE,
                    ),
                )
            }
        )

        insight = compare_session(
            planned=planned, actual=actual, reference_hr_zones=ZONES
        )

        assert insight.intent_verdict is SessionIntentVerdict.NOT_COMPARABLE
        assert "SOURCE_CONFLICT" in insight.quality_flags
        assert insight.actual_average_hr_bpm is None
        assert insight.positive_efficiency_evidence is False


class TestRatioCheckAndPositiveEfficiencyEvidence:
    def test_running_qualifies_when_ratio_clears_the_interval(self) -> None:
        planned = _running_session(pace_range=(277.0, 315.0), rpe_range=(2, 3))
        # 9.5km/2400s -> pace 252.6 s/km (above expected), HR 100 (easy_lo=108,
        # allowance 5 -> below 103 is EASIER_THAN_EXPECTED).
        actual = _running_evidence(
            duration_seconds=2400, distance_meters=9500.0, average_heart_rate_bpm=100.0
        )

        insight = compare_session(
            planned=planned, actual=actual, reference_hr_zones=ZONES
        )

        assert insight.intent_verdict is SessionIntentVerdict.EASIER_THAN_EXPECTED
        assert insight.output_verdict is SessionOutputVerdict.ABOVE_EXPECTED_OUTPUT
        assert insight.ratio_value is not None
        assert insight.ratio_max is not None
        assert insight.ratio_value > insight.ratio_max
        assert insight.positive_efficiency_evidence is True

    def test_running_does_not_qualify_when_ratio_stays_inside_the_interval(
        self,
    ) -> None:
        planned = _running_session(pace_range=(277.0, 315.0), rpe_range=(2, 3))
        # 8.0km/2400s -> pace 300 s/km (within range), HR 102 (just below the
        # easy-band-minus-allowance boundary of 103, so still
        # EASIER_THAN_EXPECTED) but the ratio itself stays under ratio_max.
        actual = _running_evidence(
            duration_seconds=2400, distance_meters=8000.0, average_heart_rate_bpm=102.0
        )

        insight = compare_session(
            planned=planned, actual=actual, reference_hr_zones=ZONES
        )

        assert insight.intent_verdict is SessionIntentVerdict.EASIER_THAN_EXPECTED
        assert insight.output_verdict is SessionOutputVerdict.WITHIN_EXPECTED_OUTPUT
        assert insight.ratio_value is not None
        assert insight.ratio_max is not None
        assert insight.ratio_value <= insight.ratio_max
        assert insight.positive_efficiency_evidence is False

    def test_cycling_qualifies_on_power_over_hr(self) -> None:
        planned = _cycling_session(power_range=(150.0, 170.0), rpe_range=(2, 3))
        actual = EvaluatorWorkoutEvidence(
            workout_id=uuid.uuid4(),
            discipline=Discipline.CYCLING,
            source=ActivitySource.MANUAL,
            started_at=datetime(2026, 9, 15, tzinfo=UTC),
            duration_seconds=3600,
            moving_duration_seconds=3600,
            duration_source="MOVING",
            average_power_watts=190.0,  # above the prescribed range
            average_heart_rate_bpm=100.0,  # well below the easy band
        )

        insight = compare_session(
            planned=planned, actual=actual, reference_hr_zones=ZONES
        )

        assert insight.intent_verdict is SessionIntentVerdict.EASIER_THAN_EXPECTED
        assert insight.output_verdict is SessionOutputVerdict.ABOVE_EXPECTED_OUTPUT
        assert insight.positive_efficiency_evidence is True

    def test_overcooked_above_expected_output_never_qualifies_despite_high_ratio(
        self,
    ) -> None:
        # Example-3 shape: OVERCOOKED + ABOVE_EXPECTED_OUTPUT is a variance
        # flag cell, not one of the three positive-efficiency combinations.
        planned = _running_session()
        actual = _running_evidence(
            duration_seconds=2400, distance_meters=9000.0, average_heart_rate_bpm=152.0
        )

        insight = compare_session(
            planned=planned, actual=actual, reference_hr_zones=ZONES
        )

        assert insight.positive_efficiency_evidence is False


class TestRpeFallbackSession:
    def test_output_verdict_is_always_unknown(self) -> None:
        planned = _rpe_fallback_running_session(rpe_range=(2, 3))
        actual = _running_evidence(
            duration_seconds=2400, distance_meters=8000.0, average_heart_rate_bpm=120.0
        )

        insight = compare_session(
            planned=planned, actual=actual, reference_hr_zones=ZONES
        )

        assert insight.output_verdict is SessionOutputVerdict.UNKNOWN
        assert insight.output_metric is None

    def test_hr_still_drives_the_intent_verdict(self) -> None:
        planned = _rpe_fallback_running_session(rpe_range=(2, 3))
        actual = _running_evidence(
            duration_seconds=2400, distance_meters=8000.0, average_heart_rate_bpm=152.0
        )

        insight = compare_session(
            planned=planned, actual=actual, reference_hr_zones=ZONES
        )

        assert insight.intent_verdict is SessionIntentVerdict.OVERCOOKED


class TestStrengthSession:
    def test_strength_is_matched_but_never_flagged(self) -> None:
        planned = _strength_session()
        actual = EvaluatorWorkoutEvidence(
            workout_id=uuid.uuid4(),
            discipline=Discipline.STRENGTH,
            source=ActivitySource.MANUAL,
            started_at=datetime(2026, 9, 15, tzinfo=UTC),
            duration_seconds=1700,
            duration_source="ELAPSED",
        )

        insight = compare_session(
            planned=planned, actual=actual, reference_hr_zones=ZONES
        )

        assert insight.status is PlannedSessionLinkStatus.MATCHED
        assert insight.intent_verdict is SessionIntentVerdict.NOT_COMPARABLE
        assert insight.output_verdict is SessionOutputVerdict.UNKNOWN
        assert insight.point_value is None  # excluded from scoring entirely


class TestMissedSession:
    def test_missed_session_has_no_actual_facts(self) -> None:
        planned = _running_session()

        insight = compare_session(
            planned=planned, actual=None, reference_hr_zones=ZONES
        )

        assert insight.status is PlannedSessionLinkStatus.MISSED
        assert insight.workout_id is None
        assert insight.actual_duration_seconds is None
        assert insight.point_value is None
        assert insight.planned_duration_seconds is not None  # still informative


class TestDurationAndDistanceDeltas:
    def test_duration_and_distance_deltas_are_signed(self) -> None:
        planned = _running_session(distance_range=(8000.0, 8000.0))
        actual = _running_evidence(
            duration_seconds=2500, distance_meters=8300.0, average_heart_rate_bpm=120.0
        )

        insight = compare_session(
            planned=planned, actual=actual, reference_hr_zones=ZONES
        )

        assert insight.duration_delta_seconds == 2500 - insight.planned_duration_seconds
        assert insight.distance_delta_meters == pytest.approx(300.0)
        assert insight.distance_delta_percent == pytest.approx(300.0 / 8000 * 100)
