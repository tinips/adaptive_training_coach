"""Contract tests for the versioned weekly-planner prompt."""

from __future__ import annotations

import json
from datetime import date

from langchain_core.messages import HumanMessage, SystemMessage

from app.schemas.availability import ConfirmedWeeklyAvailability
from app.workflows.prompts.weekly_planning import (
    FIRST_WEEK_PLANNER_PROMPT_VERSION,
    WEEKLY_PLANNER_PROMPT_VERSION,
    build_first_week_planner_messages,
    build_weekly_planner_messages,
    render_availability_constraints,
)


def test_weekly_planner_prompt_is_versioned_and_serializes_exact_context() -> None:
    context: dict[str, object] = {
        "week_start": "2026-08-24",
        "confirmed_availability": {
            "schema_version": 2,
            "status": "confirmed",
            "days": {"tuesday": {"available": True}},
        },
        "recent_evidence": {"RUNNING": {"session_count": 3}},
    }

    messages = build_weekly_planner_messages(context)

    assert WEEKLY_PLANNER_PROMPT_VERSION == 10
    assert len(messages) == 2
    assert isinstance(messages[0], SystemMessage)
    assert "session-prescription schema" in str(messages[0].content)
    assert isinstance(messages[1], HumanMessage)
    assert json.loads(str(messages[1].content)) == context


def test_system_prompt_explains_every_evidence_state() -> None:
    from app.domain.enums import DisciplineEvidenceState
    from app.workflows.prompts.weekly_planning import (
        WEEKLY_PLANNER_PROMPT_VERSION,
        build_weekly_planner_messages,
    )

    assert WEEKLY_PLANNER_PROMPT_VERSION == 10
    system = str(build_weekly_planner_messages({"week_start": "2026-08-31"})[0].content)
    for state in DisciplineEvidenceState:
        assert state.value in system
    assert "purpose, targets, intensity, objective, execution, priority" in system
    assert "desired_weekly_sessions" in system
    assert "stated recent volume is zero" in system
    assert "availability_constraints" in system
    assert "athlete_profile" in system
    assert "triathlon experience" in system


def test_availability_constraints_are_explicit_about_day_and_discipline() -> None:
    availability = ConfirmedWeeklyAvailability.model_validate(
        {
            "schema_version": 2,
            "status": "confirmed",
            "days": {
                day: {
                    "available": True,
                    "disciplines": (
                        ["running", "cycling", "swimming"]
                        if day in {"saturday", "sunday"}
                        else ["running", "cycling"]
                    ),
                    "time_windows": [{"time_of_day": None, "duration_minutes": 60}],
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
            },
        }
    )

    constraints = render_availability_constraints(availability, date(2026, 9, 7))

    assert constraints is not None
    assert "Friday 2026-09-11: running, cycling" in constraints
    assert (
        "Swimming is permitted only on Saturday 2026-09-12, Sunday 2026-09-13."
        in constraints
    )


def test_first_week_prompt_is_a_probe_and_has_no_goal_payload() -> None:
    context = {
        "planner_mode": "FIRST_WEEK",
        "week_start": "2026-08-31",
        "planned_disciplines": ["RUNNING"],
    }

    messages = build_first_week_planner_messages(context)

    assert FIRST_WEEK_PLANNER_PROMPT_VERSION == 7
    assert "not an event-preparation week" in str(messages[0].content)
    assert "purpose, structured intensity" in str(messages[0].content)
    assert "athlete chooses" in str(messages[0].content)
    assert "RPE_FALLBACK" in str(messages[0].content)
    assert json.loads(str(messages[1].content)) == context
