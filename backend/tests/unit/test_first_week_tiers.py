"""Deterministic preparation-tier regression coverage."""

from __future__ import annotations

from datetime import UTC, date, datetime

from app.domain.enums import Discipline, DisciplineEvidenceState
from app.schemas.baseline import (
    AthleteBaselineData,
    CyclingBaseline,
    RunningBaseline,
    SwimmingBaseline,
)
from app.schemas.weekly_plans import PlanReadiness, PlanReadinessDiscipline
from app.services.weekly_planning.tiers import (
    resolve_first_week_athlete_endurance_context,
    resolve_first_week_tiers,
)


def test_tiers_use_stated_volume_and_evidence() -> None:
    readiness = PlanReadiness(
        week_start=date(2026, 9, 7),
        analysis_started_at=datetime(2026, 8, 8, tzinfo=UTC),
        analysis_ended_at=datetime(2026, 9, 7, tzinfo=UTC),
        disciplines=(
            PlanReadinessDiscipline(
                discipline=Discipline.RUNNING,
                session_count=4,
                active_day_count=3,
                state=DisciplineEvidenceState.WELL_EVIDENCED,
            ),
            PlanReadinessDiscipline(
                discipline=Discipline.CYCLING,
                session_count=0,
                active_day_count=0,
                state=DisciplineEvidenceState.NONE,
            ),
        ),
        total_session_count=4,
        total_active_day_count=3,
        ready=True,
    )
    baseline = AthleteBaselineData(
        running=RunningBaseline(
            typical_weekly_sessions=4,
            typical_weekly_duration_minutes=260,
            longest_recent_run_minutes=100,
        )
    )

    tiers = resolve_first_week_tiers(
        baseline=baseline,
        readiness=readiness,
        disciplines=(Discipline.RUNNING, Discipline.CYCLING),
    )

    assert tiers == {
        Discipline.RUNNING: "WELL_TRAINED",
        Discipline.CYCLING: "UNPREPARED",
    }


def test_multisport_context_does_not_promote_running_specific_tier() -> None:
    readiness = PlanReadiness(
        week_start=date(2026, 9, 7),
        analysis_started_at=datetime(2026, 8, 8, tzinfo=UTC),
        analysis_ended_at=datetime(2026, 9, 7, tzinfo=UTC),
        disciplines=(
            PlanReadinessDiscipline(
                discipline=Discipline.RUNNING,
                session_count=0,
                active_day_count=0,
                state=DisciplineEvidenceState.NONE,
            ),
            PlanReadinessDiscipline(
                discipline=Discipline.CYCLING,
                session_count=0,
                active_day_count=0,
                state=DisciplineEvidenceState.NONE,
            ),
            PlanReadinessDiscipline(
                discipline=Discipline.SWIMMING,
                session_count=0,
                active_day_count=0,
                state=DisciplineEvidenceState.NONE,
            ),
        ),
        total_session_count=0,
        total_active_day_count=0,
        ready=True,
    )
    baseline = AthleteBaselineData(
        running=RunningBaseline(
            typical_weekly_sessions=1,
            typical_weekly_duration_minutes=45,
            longest_recent_run_minutes=45,
        ),
        cycling=CyclingBaseline(
            typical_weekly_sessions=2,
            typical_weekly_duration_minutes=150,
            longest_recent_ride_minutes=75,
            riding_environment="BOTH",
            riding_confidence="CONFIDENT",
        ),
        swimming=SwimmingBaseline(
            typical_weekly_sessions=2,
            typical_weekly_duration_minutes=120,
            longest_continuous_swim_meters=1_000,
            swimming_environment="POOL",
        ),
    )

    tiers = resolve_first_week_tiers(
        baseline=baseline,
        readiness=readiness,
        disciplines=(Discipline.RUNNING, Discipline.CYCLING, Discipline.SWIMMING),
    )
    context = resolve_first_week_athlete_endurance_context(
        baseline=baseline,
        readiness=readiness,
    )

    assert tiers[Discipline.RUNNING] == "DEVELOPING"
    assert context.level == "ENDURANCE_TRAINED"
    assert context.max_controlled_sessions == 2
