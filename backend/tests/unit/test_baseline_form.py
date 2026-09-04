from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from app.domain.enums import Discipline
from app.schemas.availability import ConfirmedWeeklyAvailability
from app.services.onboarding.baseline_form import (
    build_baseline,
    desired_sessions_fit_availability,
    fields_for_disciplines,
    parse_answer,
)
from app.services.weekly_planning.evidence import build_plan_readiness


def test_running_form_captures_consistency_and_optional_metrics() -> None:
    values = {
        "running.typical_weekly_sessions": parse_answer(
            key="running.typical_weekly_sessions", text="3"
        ),
        "running.typical_weekly_duration_minutes": parse_answer(
            key="running.typical_weekly_duration_minutes", text="150"
        ),
        "running.longest_recent_run_minutes": parse_answer(
            key="running.longest_recent_run_minutes", text="65"
        ),
        "running.recent_race_result": parse_answer(
            key="running.recent_race_result", text="10 km, 48:30"
        ),
    }

    baseline = build_baseline(values)

    assert baseline.running is not None
    assert baseline.running.typical_weekly_duration_minutes == 150
    assert baseline.running.longest_recent_run_minutes == 65
    assert baseline.running.recent_race_result is not None
    assert baseline.running.recent_race_result.duration_seconds == 2910


def test_triathlon_form_includes_every_relevant_discipline() -> None:
    fields = fields_for_disciplines(
        (Discipline.RUNNING, Discipline.CYCLING, Discipline.SWIMMING),
        include_triathlon=True,
    )

    assert "running.longest_recent_run_minutes" in fields
    assert "cycling.riding_confidence" in fields
    assert "swimming.swimming_environment" in fields
    assert "triathlon.open_water_confidence" in fields
    assert "preferences.coaching_style" in fields
    assert "preferences.desired_weekly_sessions.SWIMMING" in fields
    assert len(fields) == 23


def test_self_reported_baseline_allows_a_conservative_first_plan() -> None:
    readiness = build_plan_readiness(
        week_start=date(2026, 9, 7),
        window_started_at=datetime(2026, 8, 8, tzinfo=UTC),
        window_ended_at=datetime(2026, 9, 7, tzinfo=UTC),
        calculations={Discipline.RUNNING: None},
        self_reported_disciplines=frozenset({Discipline.RUNNING}),
    )

    assert readiness.ready is True
    assert readiness.disciplines[0].state.value == "SELF_REPORTED"


def test_desired_sessions_must_fit_each_discipline_and_total_availability() -> None:
    days = {
        day: {"available": False, "disciplines": [], "time_windows": []}
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
    days["tuesday"] = {
        "available": True,
        "disciplines": ["swimming"],
        "time_windows": [{"duration_minutes": 60}],
    }

    fits, detail = desired_sessions_fit_availability(
        {Discipline.SWIMMING: 3}, ConfirmedWeeklyAvailability(days=days)
    )

    assert fits is False
    assert detail is not None


def test_two_hour_weekend_windows_allow_two_different_sessions() -> None:
    days = {
        day: {
            "available": True,
            "disciplines": ["running", "cycling", "strength_training"],
            "time_windows": [{"duration_minutes": 60}],
        }
        for day in ("monday", "tuesday", "wednesday", "thursday", "friday")
    }
    days.update(
        {
            day: {
                "available": True,
                "disciplines": [
                    "running",
                    "cycling",
                    "swimming",
                    "strength_training",
                ],
                "time_windows": [{"duration_minutes": 120}],
            }
            for day in ("saturday", "sunday")
        }
    )

    fits, detail = desired_sessions_fit_availability(
        {
            Discipline.RUNNING: 2,
            Discipline.CYCLING: 2,
            Discipline.SWIMMING: 2,
            Discipline.STRENGTH: 2,
        },
        ConfirmedWeeklyAvailability(days=days),
    )

    assert fits is True
    assert detail is None


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("cycling.riding_environment", "mountainous"),
        ("swimming.pool_length_meters", "33"),
        ("running.typical_weekly_sessions", "15"),
        ("running.recent_race_result", "25:00"),
    ],
)
def test_baseline_rejects_invalid_answers(key: str, value: str) -> None:
    with pytest.raises(ValueError):
        parse_answer(key=key, text=value)
