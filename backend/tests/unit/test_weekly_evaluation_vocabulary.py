"""Pin the first-week evaluator's vocabulary and provisional constants.

Sourced from docs/decisions/locked.md, "First-week evaluator" and "Signal
scoring, THRIVING, and volume status". These tests exist so the vocabulary
and every calibration number are encoded once, deliberately, rather than
drifting into inline literals scattered across the evaluator modules.
"""

from __future__ import annotations

import pytest

from app.domain.enums import (
    PlannedSessionLinkStatus,
    SessionIntentVerdict,
    SessionOutputVerdict,
    VolumeRangeStatus,
    WeeklySignal,
)
from app.services.weekly_evaluation import constants as c


class TestLinkStatusVocabulary:
    def test_only_matched_and_missed_exist_in_v1(self) -> None:
        # EXTRA and CANCELLED_AGREED are deferred; see locked.md 2026-09-08 revision.
        assert {member.value for member in PlannedSessionLinkStatus} == {
            "MATCHED",
            "MISSED",
        }


class TestIntentAndOutputVocabulary:
    def test_intent_verdicts(self) -> None:
        assert {member.value for member in SessionIntentVerdict} == {
            "AS_PRESCRIBED",
            "OVERCOOKED",
            "EASIER_THAN_EXPECTED",
            "NOT_COMPARABLE",
        }

    def test_output_verdicts(self) -> None:
        assert {member.value for member in SessionOutputVerdict} == {
            "BELOW_EXPECTED_OUTPUT",
            "WITHIN_EXPECTED_OUTPUT",
            "ABOVE_EXPECTED_OUTPUT",
            "UNKNOWN",
        }


class TestSignalVocabulary:
    def test_signal_values(self) -> None:
        # THRIVING renamed from ABSORBED_WELL 2026-09-10; same five-value set.
        assert {member.value for member in WeeklySignal} == {
            "THRIVING",
            "ON_TRACK",
            "WATCH_EFFORT",
            "BACK_OFF",
            "INSUFFICIENT_EVIDENCE",
        }


class TestVolumeRangeStatusVocabulary:
    def test_volume_range_status_values(self) -> None:
        assert {member.value for member in VolumeRangeStatus} == {
            "WITHIN_RANGE",
            "BELOW_RANGE",
            "ABOVE_RANGE",
            "UNKNOWN",
        }


@pytest.mark.parametrize(
    ("intent_value", "output_value", "expected_point_value"),
    [
        ("EASIER_THAN_EXPECTED", "BELOW_EXPECTED_OUTPUT", 0),
        ("EASIER_THAN_EXPECTED", "WITHIN_EXPECTED_OUTPUT", -1),
        ("EASIER_THAN_EXPECTED", "ABOVE_EXPECTED_OUTPUT", -2),
        ("AS_PRESCRIBED", "BELOW_EXPECTED_OUTPUT", 1),
        ("AS_PRESCRIBED", "WITHIN_EXPECTED_OUTPUT", 0),
        ("AS_PRESCRIBED", "ABOVE_EXPECTED_OUTPUT", -1),
        ("OVERCOOKED", "BELOW_EXPECTED_OUTPUT", 2),
        ("OVERCOOKED", "WITHIN_EXPECTED_OUTPUT", 1),
        ("OVERCOOKED", "ABOVE_EXPECTED_OUTPUT", 0),
    ],
)
def test_point_value_table_matches_locked_intent_plus_output(
    intent_value: str, output_value: str, expected_point_value: int
) -> None:
    assert (
        c.INTENT_POINT_VALUE[intent_value] + c.OUTPUT_POINT_VALUE[output_value]
        == expected_point_value
    )


class TestProvisionalConstants:
    def test_density_band_cut_lines(self) -> None:
        assert c.DENSITY_WATCH_EFFORT_MIN == pytest.approx(0.3)
        assert c.DENSITY_BACK_OFF_MIN == pytest.approx(0.6)

    def test_variance_flag_note_trigger_count(self) -> None:
        assert c.VARIANCE_FLAG_NOTE_MIN_COUNT == 2

    def test_thriving_min_positive_efficiency_sessions(self) -> None:
        assert c.THRIVING_MIN_POSITIVE_EFFICIENCY_SESSIONS == 1

    def test_rpe_range_to_hr_band_boundaries(self) -> None:
        assert c.RPE_BAND_EASY_RANGE == (1, 4)
        assert c.RPE_BAND_MODERATE_RANGE == (5, 6)
        assert c.RPE_BAND_HARD_RANGE == (7, 10)

    def test_hr_reference_band_allowance_bpm(self) -> None:
        assert c.HR_REFERENCE_BAND_ALLOWANCE_BPM == 5

    def test_hr_source_conflict_threshold_bpm(self) -> None:
        assert c.HR_SOURCE_CONFLICT_THRESHOLD_BPM == 10

    def test_sampled_hr_minimum_duration_coverage(self) -> None:
        assert c.SAMPLED_HR_MIN_DURATION_COVERAGE == pytest.approx(0.8)

    def test_duration_tolerance_percent(self) -> None:
        assert c.DURATION_TOLERANCE_PERCENT == pytest.approx(0.10)

    def test_named_metric_minimum_allowances(self) -> None:
        assert c.RUNNING_PACE_MIN_ALLOWANCE_SECONDS_PER_KM == 10.0
        assert c.CYCLING_POWER_MIN_ALLOWANCE_WATTS == 10.0
        assert c.SWIM_PACE_MIN_ALLOWANCE_SECONDS_PER_100M == 3.0
        assert c.RUNNING_DISTANCE_MIN_ALLOWANCE_METERS == 250.0
        assert c.CYCLING_DISTANCE_MIN_ALLOWANCE_METERS == 1000.0
        assert c.SWIM_DISTANCE_MIN_ALLOWANCE_METERS == 50.0
