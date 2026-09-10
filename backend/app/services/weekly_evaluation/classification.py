"""Missed-session classification and the capture-confirm eligibility rule.

docs/decisions/locked.md, "First-week evaluator": missed sessions are
excluded, never inferred by date/greedy matching; a planned session with no
accepted link is MISSED. `EXTRA` is deferred (every logged workout must link
to a planned session before it can be confirmed) and `CANCELLED_AGREED` is
future-only, never inferred from an absent workout -- neither value is
reachable from this module, by construction (PlannedSessionLinkStatus has
only MATCHED and MISSED).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from app.schemas.weekly_plans import FirstWeekPlan
from app.services.weekly_evaluation.eligibility import is_within_plan_week
from app.services.weekly_planning.constants import FIRST_WEEK_PLAN_SCHEMA_VERSION


def classify_planned_sessions(
    *, plan: FirstWeekPlan, linked_workout_by_session: dict[UUID, UUID]
) -> dict[UUID, UUID | None]:
    """Every planned session id mapped to its linked workout id, or None.

    `None` means MISSED. Membership is decided purely by
    `linked_workout_by_session` (athlete-confirmed links); no date or
    discipline inference ever produces a match here.
    """

    return {
        session.id: linked_workout_by_session.get(session.id)
        for session in plan.sessions
    }


def has_linkable_plan_for_workout(
    *,
    plan: FirstWeekPlan | None,
    plan_schema_version: int,
    workout_started_at: datetime,
    timezone: str | None,
) -> bool:
    """The deterministic core of the capture-confirm block.

    docs/decisions/locked.md, "First-week evaluator" (revised 2026-09-08):
    "every logged workout must be linked to a planned session before it can
    be confirmed... The capture flow blocks confirming a workout with no
    planned session available to link it to." True only when an eligible
    (schema-v5+, so it carries stable session ids) first-week plan exists
    whose athlete-local Monday-Sunday week contains the workout. This is the
    predicate the capture flow's confirm action should consult; wiring it
    into that flow is deferred to the delivery milestone (brief steps
    10-12), not part of this deterministic-core pass.
    """

    if plan is None or plan_schema_version < FIRST_WEEK_PLAN_SCHEMA_VERSION:
        return False
    return is_within_plan_week(
        started_at=workout_started_at, timezone=timezone, week_start=plan.week_start
    )
