"""Contract tests for the versioned weekly-planner prompt."""

from __future__ import annotations

import json

from langchain_core.messages import HumanMessage, SystemMessage

from app.workflows.prompts.weekly_planning import (
    WEEKLY_PLANNER_PROMPT_VERSION,
    build_weekly_planner_messages,
)


def test_weekly_planner_prompt_is_versioned_and_serializes_exact_context() -> None:
    context: dict[str, object] = {
        "week_start": "2026-08-24",
        "availability": "Tuesday evening only",
        "recent_evidence": {"RUNNING": {"session_count": 3}},
    }

    messages = build_weekly_planner_messages(context)

    assert WEEKLY_PLANNER_PROMPT_VERSION == 4
    assert len(messages) == 2
    assert isinstance(messages[0], SystemMessage)
    assert "Monday-to-Sunday week" in str(messages[0].content)
    assert isinstance(messages[1], HumanMessage)
    assert json.loads(str(messages[1].content)) == context


def test_system_prompt_explains_every_evidence_state() -> None:
    from app.domain.enums import DisciplineEvidenceState
    from app.workflows.prompts.weekly_planning import (
        WEEKLY_PLANNER_PROMPT_VERSION,
        build_weekly_planner_messages,
    )

    assert WEEKLY_PLANNER_PROMPT_VERSION == 4
    system = str(build_weekly_planner_messages({"week_start": "2026-08-31"})[0].content)
    for state in DisciplineEvidenceState:
        assert state.value in system
    assert "RPE 3-4/10" in system
    assert "generic fixed heart-rate cap" in system
    assert "stated recent volume is zero" in system
