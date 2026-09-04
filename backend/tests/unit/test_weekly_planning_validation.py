"""Pure first-week validation and fallback regression coverage."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from app.domain.enums import CoachingStyle, Discipline, DisciplineEvidenceState
from app.schemas.availability import ConfirmedWeeklyAvailability
from app.schemas.baseline import (
    AthleteBaselineData,
    CyclingBaseline,
    RunningBaseline,
    SwimmingBaseline,
    TrainingPreferences,
)
from app.schemas.weekly_plans import (
    PlanDay,
    PlanReadiness,
    PlanReadinessDiscipline,
    PlanSession,
    WeeklyPlan,
)
from app.services.weekly_planning.constants import (
    UNTRAINED_SWIM_MAX_SESSIONS,
    UNTRAINED_SWIM_SESSION_MAX_MINUTES,
)
from app.services.weekly_planning.validation import (
    build_fallback_week,
    repair_plan,
    validate_plan,
)

WEEK_START = date(2026, 9, 7)


def _intensity(level: str) -> dict[str, object]:
    rpe_range = {
        "EASY": (2, 3),
        "MODERATE": (4, 6),
        "HARD": (7, 9),
    }[level]
    return {
        "metric": "RPE",
        "target_range": rpe_range,
        "rpe_range": rpe_range,
        "guidance": f"{level.title()} effort.",
    }


def _session(
    discipline: Discipline,
    *,
    duration: int = 30,
    intensity: str = "EASY",
    targets: dict[str, object] | None = None,
    execution: str = "Keep the effort easy and conversational.",
) -> PlanSession:
    return PlanSession(
        discipline=discipline,
        purpose="Build a consistent training habit.",
        intensity=_intensity(intensity),
        objective="Build a consistent training habit.",
        targets={"duration_minutes": duration, **(targets or {})},
        execution=execution,
    )


def test_session_requires_a_purpose_and_structured_intensity_target() -> None:
    with pytest.raises(ValidationError):
        PlanSession.model_validate(
            {
                "discipline": "RUNNING",
                "intensity": "EASY",
                "objective": "Build a consistent training habit.",
                "targets": {"duration_minutes": 30},
                "execution": "Keep the effort easy and conversational.",
            }
        )

    session = _session(Discipline.RUNNING)
    assert session.purpose == "Build a consistent training habit."
    assert session.intensity.metric == "RPE"
    assert session.intensity.target_range == (2, 3)


def _plan(*sessions_by_offset: tuple[int, PlanSession]) -> WeeklyPlan:
    grouped: dict[int, list[PlanSession]] = {}
    for offset, session in sessions_by_offset:
        grouped.setdefault(offset, []).append(session)
    return WeeklyPlan(
        week_start=WEEK_START,
        days=tuple(
            PlanDay(
                date=date.fromordinal(WEEK_START.toordinal() + offset),
                sessions=tuple(grouped[offset]),
            )
            if offset in grouped
            else PlanDay(
                date=date.fromordinal(WEEK_START.toordinal() + offset),
                rest_note="Rest and recover.",
            )
            for offset in range(7)
        ),
    )


def _readiness(*disciplines: Discipline) -> PlanReadiness:
    return PlanReadiness(
        week_start=WEEK_START,
        analysis_started_at=datetime(2026, 8, 8, tzinfo=UTC),
        analysis_ended_at=datetime(2026, 9, 7, tzinfo=UTC),
        disciplines=tuple(
            PlanReadinessDiscipline(
                discipline=discipline,
                session_count=0,
                active_day_count=0,
                state=DisciplineEvidenceState.SELF_REPORTED,
            )
            for discipline in disciplines
        ),
        total_session_count=0,
        total_active_day_count=0,
        ready=True,
    )


def _baseline(*, ftp: int | None = 230) -> AthleteBaselineData:
    return AthleteBaselineData(
        running=RunningBaseline(
            typical_weekly_sessions=0,
            typical_weekly_duration_minutes=0,
            longest_recent_run_minutes=0,
        ),
        cycling=CyclingBaseline(
            typical_weekly_sessions=2,
            typical_weekly_duration_minutes=100,
            longest_recent_ride_minutes=60,
            riding_environment="BOTH",
            riding_confidence="CONFIDENT",
            recent_ftp_watts=ftp,
        ),
        swimming=SwimmingBaseline(
            typical_weekly_sessions=0,
            typical_weekly_duration_minutes=0,
            longest_continuous_swim_meters=0,
            swimming_environment="POOL",
        ),
        preferences=TrainingPreferences(
            coaching_style=CoachingStyle.NORMAL,
            desired_weekly_sessions={
                Discipline.RUNNING: 1,
                Discipline.CYCLING: 2,
                Discipline.SWIMMING: 3,
            },
        ),
    )


def test_supported_ftp_power_target_does_not_produce_a_violation() -> None:
    plan = _plan(
        (0, _session(Discipline.CYCLING, targets={"average_power_watts": 160}))
    )

    outcome = validate_plan(
        plan,
        readiness=_readiness(Discipline.CYCLING),
        baseline=_baseline(),
        availability=None,
        preferences=_baseline().preferences,
    )

    assert "UNSUPPORTED_TARGET" not in {
        violation.code for violation in outcome.violations
    }


def test_availability_rejects_multiple_sessions_over_the_daily_time_limit() -> None:
    availability = ConfirmedWeeklyAvailability.model_validate(
        {
            "days": {
                day: {
                    "available": True,
                    "disciplines": ["running", "cycling"],
                    "time_windows": [{"duration_minutes": 60}],
                }
                for day in (
                    "monday",
                    "tuesday",
                    "wednesday",
                    "thursday",
                    "friday",
                    "saturday",
                    "sunday",
                )
            }
        }
    )
    plan = _plan(
        (0, _session(Discipline.RUNNING, duration=40)),
        (0, _session(Discipline.CYCLING, duration=30)),
    )

    outcome = validate_plan(
        plan,
        readiness=_readiness(Discipline.RUNNING, Discipline.CYCLING),
        baseline=_baseline(),
        availability=availability,
        preferences=_baseline().preferences,
    )

    assert any(
        violation.code == "AVAILABILITY_CONFLICT"
        and violation.day == WEEK_START
        and violation.detail == "daily session total exceeds confirmed availability"
        for violation in outcome.violations
    )


def test_untrained_swim_is_repaired_to_the_fixed_technique_shape() -> None:
    plan = _plan(
        (0, _session(Discipline.SWIMMING, duration=60, intensity="HARD")),
        (2, _session(Discipline.SWIMMING, duration=45)),
        (4, _session(Discipline.SWIMMING, duration=40)),
    )
    baseline = _baseline()
    readiness = _readiness(Discipline.SWIMMING)

    outcome = validate_plan(
        plan,
        readiness=readiness,
        baseline=baseline,
        availability=None,
        preferences=baseline.preferences,
    )
    repaired = repair_plan(plan, outcome.violations, baseline=baseline)

    swims = [
        session
        for day in repaired.days
        for session in day.sessions
        if session.discipline is Discipline.SWIMMING
    ]
    assert len(swims) == UNTRAINED_SWIM_MAX_SESSIONS
    assert all(session.intensity.rpe_range == (2, 3) for session in swims)
    assert all(
        session.targets.duration_minutes <= UNTRAINED_SWIM_SESSION_MAX_MINUTES
        for session in swims
    )


def test_strength_day_breaks_the_endurance_only_seven_day_load_check() -> None:
    balanced = _plan(
        *((offset, _session(Discipline.RUNNING)) for offset in range(6)),
        (6, _session(Discipline.STRENGTH)),
    )
    all_endurance = _plan(
        *((offset, _session(Discipline.RUNNING)) for offset in range(7))
    )
    readiness = _readiness(Discipline.RUNNING, Discipline.STRENGTH)

    balanced_codes = {
        violation.code
        for violation in validate_plan(
            balanced,
            readiness=readiness,
            baseline=_baseline(),
            availability=None,
            preferences=_baseline().preferences,
        ).violations
    }
    endurance_codes = {
        violation.code
        for violation in validate_plan(
            all_endurance,
            readiness=readiness,
            baseline=_baseline(),
            availability=None,
            preferences=_baseline().preferences,
        ).violations
    }

    assert "EXCESSIVE_CONSECUTIVE_LOAD" not in balanced_codes
    assert "EXCESSIVE_CONSECUTIVE_LOAD" in endurance_codes


def test_fallback_keeps_untrained_swimming_easy_and_capped() -> None:
    baseline = _baseline()

    fallback = build_fallback_week(
        WEEK_START,
        baseline=baseline,
        availability=None,
        preferences=baseline.preferences,
        disciplines=(Discipline.RUNNING, Discipline.SWIMMING),
    )

    swims = [
        session
        for day in fallback.days
        for session in day.sessions
        if session.discipline is Discipline.SWIMMING
    ]
    assert swims
    assert len(swims) <= UNTRAINED_SWIM_MAX_SESSIONS
    assert all(session.intensity.rpe_range == (2, 3) for session in swims)
    assert all(
        session.targets.duration_minutes <= UNTRAINED_SWIM_SESSION_MAX_MINUTES
        for session in swims
    )


def test_fallback_honors_requested_strength_frequency_without_a_baseline() -> None:
    baseline = _baseline()
    preferences = TrainingPreferences(
        coaching_style=CoachingStyle.NORMAL,
        desired_weekly_sessions={Discipline.STRENGTH: 2},
    )
    availability = ConfirmedWeeklyAvailability.model_validate(
        {
            "days": {
                day: {
                    "available": True,
                    "disciplines": ["strength_training"],
                    "time_windows": [
                        {
                            "duration_minutes": (
                                120 if day in {"saturday", "sunday"} else 60
                            )
                        }
                    ],
                }
                for day in (
                    "monday",
                    "tuesday",
                    "wednesday",
                    "thursday",
                    "friday",
                    "saturday",
                    "sunday",
                )
            }
        }
    )

    fallback = build_fallback_week(
        WEEK_START,
        baseline=baseline,
        availability=availability,
        preferences=preferences,
        disciplines=(Discipline.STRENGTH,),
    )

    strength_sessions = [
        session
        for day in fallback.days
        for session in day.sessions
        if session.discipline is Discipline.STRENGTH
    ]
    assert len(strength_sessions) == 2
    assert all(session.targets.duration_minutes == 60 for session in strength_sessions)
    assert [
        day.date
        for day in fallback.days
        if day.sessions and day.sessions[0].discipline is Discipline.STRENGTH
    ] == [date(2026, 9, 7), date(2026, 9, 8)]


def test_fallback_spreads_strength_before_using_another_busy_weekend_day() -> None:
    baseline = _baseline().model_copy(
        update={
            "running": RunningBaseline(
                typical_weekly_sessions=2,
                typical_weekly_duration_minutes=100,
                longest_recent_run_minutes=60,
            )
        }
    )
    preferences = TrainingPreferences(
        coaching_style=CoachingStyle.NORMAL,
        desired_weekly_sessions={
            Discipline.CYCLING: 2,
            Discipline.RUNNING: 2,
            Discipline.STRENGTH: 2,
            Discipline.SWIMMING: 2,
        },
    )
    availability = ConfirmedWeeklyAvailability.model_validate(
        {
            "days": {
                day: {
                    "available": True,
                    "disciplines": [
                        "running",
                        "cycling",
                        "swimming",
                        "strength_training",
                    ],
                    "time_windows": [
                        {
                            "duration_minutes": 120
                            if day in {"saturday", "sunday"}
                            else 60
                        }
                    ],
                }
                for day in (
                    "monday",
                    "tuesday",
                    "wednesday",
                    "thursday",
                    "friday",
                    "saturday",
                    "sunday",
                )
            }
        }
    )

    fallback = build_fallback_week(
        WEEK_START,
        baseline=baseline,
        availability=availability,
        preferences=preferences,
        disciplines=(
            Discipline.CYCLING,
            Discipline.RUNNING,
            Discipline.STRENGTH,
            Discipline.SWIMMING,
        ),
    )

    strength_dates = [
        day.date
        for day in fallback.days
        if any(session.discipline is Discipline.STRENGTH for session in day.sessions)
    ]
    assert strength_dates == [date(2026, 9, 11), date(2026, 9, 12)]
