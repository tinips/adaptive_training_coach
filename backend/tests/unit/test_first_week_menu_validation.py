"""First-week menu constraints stay code-owned rather than prompt-only."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

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
    PlanSession,
)
from app.services.weekly_planning.service import (
    _build_first_week_fallback,
    _PlanningInput,
)
from app.services.weekly_planning.validation import (
    _strength_violations,
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


def test_menu_rejects_duplicate_sessions() -> None:
    week_start = date(2026, 9, 7)
    prescription = {
        "week_start": week_start,
        "sessions": [
            {
                "discipline": "RUNNING",
                "purpose": "Easy run.",
                "intensity": {
                    "metric": "RPE",
                    "target_range": [2, 3],
                    "rpe_range": [2, 3],
                    "guidance": "Easy.",
                },
                "objective": "Run easily.",
                "targets": {"duration_minutes": 30},
                "execution": "Keep it easy.",
            }
        ]
        * 2,
    }
    plan = make_first_week_plan(FirstWeekPlanPrescription.model_validate(prescription))
    readiness = PlanReadiness(
        week_start=week_start,
        analysis_started_at=datetime(2026, 8, 8, tzinfo=UTC),
        analysis_ended_at=datetime(2026, 9, 7, tzinfo=UTC),
        disciplines=(
            PlanReadinessDiscipline(
                discipline=Discipline.RUNNING,
                session_count=2,
                active_day_count=2,
                state=DisciplineEvidenceState.THIN,
            ),
        ),
        total_session_count=2,
        total_active_day_count=2,
        ready=True,
    )

    outcome = validate_first_week_plan(
        plan,
        readiness=readiness,
        baseline=None,
        availability=None,
        preferences=None,
        zones={},
    )

    assert [item.code for item in outcome.violations] == [
        "FIRST_WEEK_DUPLICATE_SESSION"
    ]


def test_menu_rejects_a_purpose_with_multiple_sentences() -> None:
    week_start = date(2026, 9, 7)
    plan = make_first_week_plan(
        FirstWeekPlanPrescription.model_validate(
            {
                "week_start": week_start,
                "sessions": [
                    {
                        "discipline": "RUNNING",
                        "purpose": "Build easy consistency. Keep the effort relaxed.",
                        "intensity": {
                            "metric": "RPE",
                            "target_range": [2, 3],
                            "rpe_range": [2, 3],
                            "guidance": "Easy.",
                        },
                        "objective": "Run easily.",
                        "targets": {"duration_minutes": 30},
                        "execution": "Keep it easy.",
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
                session_count=1,
                active_day_count=1,
                state=DisciplineEvidenceState.THIN,
            ),
        ),
        total_session_count=1,
        total_active_day_count=1,
        ready=True,
    )

    outcome = validate_first_week_plan(
        plan,
        readiness=readiness,
        baseline=None,
        availability=None,
        preferences=None,
        zones={},
    )

    assert [item.code for item in outcome.violations] == [
        "FIRST_WEEK_PURPOSE_NOT_CONCISE"
    ]


def test_strength_targets_are_rejected_by_the_first_week_schema() -> None:
    week_start = date(2026, 9, 7)
    payload = {
        "week_start": week_start,
        "sessions": [
            {
                "discipline": "STRENGTH",
                "purpose": "Build controlled movement familiarity.",
                "intensity": {
                    "metric": "RPE",
                    "target_range": [3, 4],
                    "rpe_range": [3, 4],
                    "guidance": "Easy.",
                },
                "objective": "Move with control.",
                "targets": {"duration_minutes": 30, "rpe": 4},
                "execution": "Complete 3 sets with light loads.",
            }
        ],
    }

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        FirstWeekPlanPrescription.model_validate(payload)


def test_strength_validator_remains_a_safety_net_for_legacy_sessions() -> None:
    unsafe = PlanSession.model_validate(
        {
            "discipline": "STRENGTH",
            "purpose": "Build controlled movement familiarity.",
            "intensity": {
                "metric": "RPE",
                "target_range": [3, 4],
                "rpe_range": [3, 4],
                "guidance": "Easy.",
            },
            "objective": "Move with control.",
            "targets": {"duration_minutes": 30, "rpe": 4},
            "execution": "Use light loads with controlled form.",
        }
    )

    assert [violation.code for violation in _strength_violations(None, unsafe)] == [
        "STRENGTH_OVER_SPECIFIED"
    ]


def test_first_week_fallback_honors_requested_strength_frequency_and_is_valid() -> None:
    week_start = date(2026, 9, 7)
    preferences = TrainingPreferences(
        coaching_style=CoachingStyle.NORMAL,
        desired_weekly_sessions={Discipline.STRENGTH: 2},
    )
    prepared = _PlanningInput(
        athlete_id=uuid.uuid4(),
        week_start=week_start,
        readiness=_strength_readiness(week_start),
        baseline=None,
        availability=None,
        preferences=preferences,
        target_disciplines=(Discipline.STRENGTH,),
        prompt_context={},
        evidence_snapshot={},
        input_digest="test",
        zones={},
        tiers={},
    )

    fallback = _build_first_week_fallback(prepared)

    assert len(fallback.sessions) == 2
    assert all(
        session.targets.model_dump(exclude_none=True) == {"duration_minutes": 15}
        for session in fallback.sessions
    )
    assert validate_first_week_plan(
        fallback,
        readiness=prepared.readiness,
        baseline=None,
        availability=None,
        preferences=preferences,
        zones={},
    ).ok


def _strength_readiness(week_start: date) -> PlanReadiness:
    return PlanReadiness(
        week_start=week_start,
        analysis_started_at=datetime(2026, 8, 8, tzinfo=UTC),
        analysis_ended_at=datetime(2026, 9, 7, tzinfo=UTC),
        disciplines=(
            PlanReadinessDiscipline(
                discipline=Discipline.STRENGTH,
                session_count=2,
                active_day_count=2,
                state=DisciplineEvidenceState.SELF_REPORTED,
            ),
        ),
        total_session_count=2,
        total_active_day_count=2,
        ready=True,
    )
