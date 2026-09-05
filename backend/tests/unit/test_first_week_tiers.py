"""Deterministic preparation-tier regression coverage."""

from __future__ import annotations

from datetime import UTC, date, datetime

from app.domain.enums import Discipline, DisciplineEvidenceState
from app.schemas.baseline import AthleteBaselineData, RunningBaseline
from app.schemas.weekly_plans import PlanReadiness, PlanReadinessDiscipline
from app.services.weekly_planning.tiers import resolve_first_week_tiers


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
