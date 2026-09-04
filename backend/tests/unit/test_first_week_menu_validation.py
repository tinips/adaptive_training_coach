"""First-week menu constraints stay code-owned rather than prompt-only."""

from __future__ import annotations

from datetime import UTC, date, datetime

from app.domain.enums import CoachingStyle, Discipline, DisciplineEvidenceState
from app.schemas.baseline import (
    AthleteBaselineData,
    RunningBaseline,
    TrainingPreferences,
)
from app.schemas.weekly_plans import (
    FirstWeekPlanPrescription,
    PlanReadiness,
    PlanReadinessDiscipline,
)
from app.services.weekly_planning.validation import (
    make_first_week_plan,
    validate_first_week_plan,
)
from app.services.weekly_planning.zones import resolve_first_week_zones


def test_menu_requires_requested_frequency_and_rpe_without_a_threshold() -> None:
    week_start = date(2026, 9, 7)
    baseline = AthleteBaselineData(
        running=RunningBaseline(
            typical_weekly_sessions=2,
            typical_weekly_duration_minutes=100,
            longest_recent_run_minutes=60,
        ),
        preferences=TrainingPreferences(
            coaching_style=CoachingStyle.NORMAL,
            desired_weekly_sessions={Discipline.RUNNING: 2},
        ),
    )
    plan = make_first_week_plan(
        FirstWeekPlanPrescription.model_validate(
            {
                "week_start": week_start,
                "sessions": [
                    {
                        "discipline": "RUNNING",
                        "purpose": "Build easy consistency.",
                        "intensity": {
                            "metric": "POWER_WATTS",
                            "target_range": [120, 160],
                            "rpe_range": [3, 4],
                            "guidance": "Use watch power.",
                        },
                        "objective": "Run comfortably.",
                        "targets": {"duration_minutes": 45},
                        "execution": "Keep it comfortable.",
                    }
                ],
            }
        )
    )
    readiness = PlanReadiness(
        week_start=week_start,
        analysis_started_at=datetime(2026, 8, 8, tzinfo=UTC),
        analysis_ended_at=datetime(2026, 9, 7, tzinfo=UTC),
        disciplines=(
            PlanReadinessDiscipline(
                discipline=Discipline.RUNNING,
                session_count=0,
                active_day_count=0,
                state=DisciplineEvidenceState.SELF_REPORTED,
            ),
        ),
        total_session_count=0,
        total_active_day_count=0,
        ready=True,
    )

    outcome = validate_first_week_plan(
        plan,
        readiness=readiness,
        baseline=baseline,
        availability=None,
        preferences=baseline.preferences,
        zones=resolve_first_week_zones(
            baseline=baseline,
            calculations={},
            disciplines=(Discipline.RUNNING,),
        ),
    )

    assert {violation.code for violation in outcome.violations} == {
        "FIRST_WEEK_RPE_REQUIRED",
        "SESSION_COUNT_UNDERSHOOT",
    }
