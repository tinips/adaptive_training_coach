"""Regression coverage for deterministic first-week intensity resolution."""

from __future__ import annotations

from datetime import UTC, datetime

from app.domain.enums import Discipline
from app.schemas.baseline import (
    AthleteBaselineData,
    CyclingBaseline,
    RecentRaceResult,
    RunningBaseline,
)
from app.schemas.fitness import BaselineCalculation
from app.services.weekly_planning.zones import resolve_first_week_zones


def test_resolver_prefers_known_discipline_thresholds_and_is_explicit_about_rpe() -> (
    None
):
    baseline = AthleteBaselineData(
        running=RunningBaseline(
            typical_weekly_sessions=2,
            typical_weekly_duration_minutes=100,
            longest_recent_run_minutes=60,
            recent_race_result=RecentRaceResult(distance_km=10, duration_seconds=3_000),
        ),
        cycling=CyclingBaseline(
            typical_weekly_sessions=2,
            typical_weekly_duration_minutes=120,
            longest_recent_ride_minutes=70,
            riding_environment="BOTH",
            riding_confidence="CONFIDENT",
            recent_ftp_watts=240,
        ),
    )

    zones = resolve_first_week_zones(
        baseline=baseline,
        calculations={},
        disciplines=(Discipline.RUNNING, Discipline.CYCLING, Discipline.SWIMMING),
    )

    assert zones[Discipline.CYCLING].metric == "POWER_WATTS"
    assert zones[Discipline.CYCLING].easy == (132, 180)
    assert zones[Discipline.RUNNING].metric == "PACE_SECONDS_PER_KM"
    assert zones[Discipline.SWIMMING].mode == "RPE_FALLBACK"
    assert "not pace, power, or heart-rate" in zones[Discipline.SWIMMING].guidance


def test_resolver_never_selects_heart_rate_even_with_reliable_observed_hr() -> None:
    calculation = BaselineCalculation(
        discipline=Discipline.RUNNING,
        analysis_started_at=datetime(2026, 8, 1, tzinfo=UTC),
        analysis_ended_at=datetime(2026, 9, 1, tzinfo=UTC),
        calculated_at=datetime(2026, 9, 1, tzinfo=UTC),
        session_count=1,
        active_day_count=1,
        total_duration_seconds=1800,
        distance_session_count=1,
        longest_duration_seconds=1800,
        reliable_hr_sample_count=1,
        reliable_max_hr_bpm=172.0,
        confidence=0.5,
        discipline_metrics_jsonb={},
        evidence_summary_jsonb={},
        quality_flags_jsonb=[],
        source_workout_through_at=datetime(2026, 9, 1, tzinfo=UTC),
        input_updated_through_at=datetime(2026, 9, 1, tzinfo=UTC),
        input_digest="0" * 64,
        calculation_version=1,
    )

    zones = resolve_first_week_zones(
        baseline=None,
        calculations={Discipline.RUNNING: calculation},
        disciplines=(Discipline.RUNNING,),
    )

    assert zones[Discipline.RUNNING].mode == "RPE_FALLBACK"
    assert zones[Discipline.RUNNING].metric == "RPE"
