"""Versioned prompt contract for the persisted weekly training planner."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Final

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

WEEKLY_PLANNER_PROMPT_VERSION: Final = 3

_WEEKLY_PLANNER_SYSTEM_PROMPT: Final = """You are an endurance coach creating one
safe, concise weekly plan.
Return only the required structured schema for the requested Monday-to-Sunday week.
Use the athlete's target disciplines, recent aggregated evidence, immutable baselines,
availability, available equipment/access, and stated training limitations. Do not make
medical claims. Every day must be present. Rest days have no sessions and a brief rest
note. Training sessions must have an existing discipline, clear objective, duration,
intensity, and a concise structure. Do not invent measurements not in the context.

evidence_state tells you how much recent history exists for each target discipline.
Respect it:
WELL_EVIDENCED: enough recent history to plan normally for this discipline.
THIN: very little recent history. Give it one short, easy, clearly introductory
session. Do not prescribe HARD intensity for it.
NONE: no recent history at all. The athlete's goal still requires this discipline, so
include one short, easy, introductory session. Do not prescribe HARD intensity for it.

Plan only the disciplines present in evidence_state. An athlete may have one target
discipline or several.

Each entry in target_contexts carries a role. TARGET means the discipline the
athlete's event is in; it gets the bulk of the week. SUPPORTING means a discipline
that exists to support the target, such as strength work to maintain muscle. Give a
supporting discipline one or two short sessions and never let it displace target
training."""


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
