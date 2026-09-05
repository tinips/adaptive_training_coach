"""Heart rate is verified against, never prescribed, in every planner.

CLAUDE.md states the rule for the first-week AND the ongoing planner. The
ongoing planner enforces it twice: the model-facing prescription schema has no
heart-rate metric, and a shared validator guard catches code-built or
previously stored plans that still use the wider persisted intensity type.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from app.domain.enums import Discipline, DisciplineEvidenceState
from app.schemas.weekly_plans import (
    FirstWeekPlanPrescription,
    PlanDay,
    PlanReadiness,
    PlanReadinessDiscipline,
    PlanSession,
    SessionPrescription,
    WeeklyPlan,
    WeeklyPlanPrescription,
)
from app.services.weekly_planning.validation import (
    make_first_week_plan,
    validate_first_week_plan,
    validate_plan,
)

WEEK_START = date(2026, 9, 7)

_HEART_RATE_INTENSITY: dict[str, object] = {
    "metric": "HEART_RATE_BPM",
    "target_range": [130, 145],
    "rpe_range": [3, 4],
    "guidance": "Hold this heart rate.",
}


def _session_payload(intensity: dict[str, object]) -> dict[str, object]:
    return {
        "discipline": "RUNNING",
        "purpose": "Build a consistent training habit.",
        "intensity": intensity,
        "objective": "Run comfortably.",
        "targets": {"duration_minutes": 40},
        "execution": "Keep the effort easy and conversational.",
    }


def _readiness() -> PlanReadiness:
    return PlanReadiness(
        week_start=WEEK_START,
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


def _codes(violations: object) -> set[str]:
    return {violation.code for violation in violations}  # type: ignore[attr-defined]


def test_the_ongoing_coach_model_is_never_offered_a_heart_rate_metric() -> None:
    schema = json.dumps(WeeklyPlanPrescription.model_json_schema())

    assert "HEART_RATE_BPM" not in schema
    assert "PACE_SECONDS_PER_KM" in schema  # the prescribable metrics survive


def test_an_ongoing_prescription_rejects_a_heart_rate_target() -> None:
    with pytest.raises(ValidationError):
        SessionPrescription.model_validate(_session_payload(_HEART_RATE_INTENSITY))

    with pytest.raises(ValidationError):
        WeeklyPlanPrescription.model_validate(
            {
                "week_start": WEEK_START,
                "sessions": [_session_payload(_HEART_RATE_INTENSITY)],
            }
        )


def test_stored_plans_written_before_the_narrowing_still_load() -> None:
    """The persisted type stays wide so existing rows keep deserializing."""

    session = PlanSession.model_validate(_session_payload(_HEART_RATE_INTENSITY))

    assert session.intensity.metric == "HEART_RATE_BPM"


def test_the_ongoing_validator_flags_a_heart_rate_prescription() -> None:
    plan = WeeklyPlan(
        week_start=WEEK_START,
        days=tuple(
            PlanDay(
                date=date.fromordinal(WEEK_START.toordinal() + offset),
                sessions=(
                    PlanSession.model_validate(_session_payload(_HEART_RATE_INTENSITY)),
                ),
            )
            if offset == 0
            else PlanDay(
                date=date.fromordinal(WEEK_START.toordinal() + offset),
                rest_note="Rest and recover.",
            )
            for offset in range(7)
        ),
    )

    outcome = validate_plan(
        plan,
        readiness=_readiness(),
        baseline=None,
        availability=None,
        preferences=None,
    )

    assert "HEART_RATE_PRESCRIBED" in _codes(outcome.violations)


def test_the_first_week_validator_uses_the_same_guard() -> None:
    plan = make_first_week_plan(
        FirstWeekPlanPrescription.model_validate(
            {
                "week_start": WEEK_START,
                "sessions": [_session_payload(_HEART_RATE_INTENSITY)],
            }
        )
    )

    outcome = validate_first_week_plan(
        plan,
        readiness=_readiness(),
        baseline=None,
        availability=None,
        preferences=None,
        zones={},
    )

    assert "HEART_RATE_PRESCRIBED" in _codes(outcome.violations)
