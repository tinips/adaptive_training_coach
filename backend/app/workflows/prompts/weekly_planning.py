"""Versioned prompt contract for the persisted weekly training planner."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Final

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

WEEKLY_PLANNER_PROMPT_VERSION: Final = 1

_WEEKLY_PLANNER_SYSTEM_PROMPT: Final = """You are an endurance coach creating one
safe, concise weekly plan.
Return only the required structured schema for the requested Monday-to-Sunday week.
Use the athlete's target disciplines, recent aggregated evidence, immutable baselines,
availability, available equipment/access, and stated training limitations. Do not make
medical claims. Every day must be present. Rest days have no sessions and a brief rest
note. Training sessions must have an existing discipline, clear objective, duration,
intensity, and a concise structure. Do not invent measurements not in the context."""


def build_weekly_planner_messages(
    context: Mapping[str, object],
) -> list[BaseMessage]:
    """Build the only prompt sent for a weekly-plan generation request.

    Callers own the transient context. This function does not log, persist, or
    transform raw availability and health-limitations text.
    """

    return [
        SystemMessage(_WEEKLY_PLANNER_SYSTEM_PROMPT),
        HumanMessage(
            json.dumps(
                dict(context),
                sort_keys=True,
                separators=(",", ":"),
            )
        ),
    ]
