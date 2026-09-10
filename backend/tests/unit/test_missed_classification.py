"""Missed classification and the capture-confirm eligibility predicate.

Test-first step 6 of docs/briefs/backlog/first-week-evaluator.md.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from app.domain.enums import Discipline
from app.schemas.weekly_plans import FirstWeekEnduranceSession, FirstWeekPlan
from app.services.weekly_evaluation.classification import (
    classify_planned_sessions,
    has_linkable_plan_for_workout,
)
from app.services.weekly_planning.constants import FIRST_WEEK_PLAN_SCHEMA_VERSION


def _session() -> FirstWeekEnduranceSession:
    return FirstWeekEnduranceSession(
        discipline=Discipline.RUNNING,
        purpose="Build a consistent aerobic habit.",
        intensity={
            "metric": "PACE_SECONDS_PER_KM",
            "target_range": [300.0, 330.0],
            "rpe_range": [3, 4],
            "guidance": "Steady, controlled effort.",
        },
        objective="Cover the distance at the prescribed pace.",
        targets={"distance_range_meters": (8000.0, 9000.0)},
        execution="Keep the effort controlled throughout.",
    )


def _plan(sessions: list[FirstWeekEnduranceSession]) -> FirstWeekPlan:
    counts: dict[Discipline, int] = {}
    minutes: dict[Discipline, int] = {}
    for session in sessions:
        counts[session.discipline] = counts.get(session.discipline, 0) + 1
        minutes[session.discipline] = minutes.get(session.discipline, 0) + (
            session.targets.duration_minutes or 0
        )
    return FirstWeekPlan(
        week_start=date(2026, 9, 14),
        sessions=tuple(sessions),
        guardrails=["Stop if you feel sharp pain."],
        logging_instructions=["Log every session you complete."],
        sessions_per_discipline=counts,
        total_minutes_per_discipline=minutes,
    )


class TestClassifyPlannedSessions:
    def test_a_linked_session_is_matched_to_its_workout(self) -> None:
        session = _session()
        plan = _plan([session])
        workout_id = uuid.uuid4()

        result = classify_planned_sessions(
            plan=plan, linked_workout_by_session={session.id: workout_id}
        )

        assert result[session.id] == workout_id

    def test_an_unlinked_session_is_missed(self) -> None:
        session = _session()
        plan = _plan([session])

        result = classify_planned_sessions(plan=plan, linked_workout_by_session={})

        assert result[session.id] is None

    def test_classification_never_infers_a_match_by_date_or_discipline(self) -> None:
        """Only the explicit link map decides matching; nothing else."""

        matched_session = _session()
        unmatched_session = _session()  # same discipline, same everything
        plan = _plan([matched_session, unmatched_session])
        workout_id = uuid.uuid4()

        result = classify_planned_sessions(
            plan=plan, linked_workout_by_session={matched_session.id: workout_id}
        )

        assert result[matched_session.id] == workout_id
        assert result[unmatched_session.id] is None

    def test_missed_sessions_carry_no_fabricated_workout_reference(self) -> None:
        """A missed session's value is exactly None, never a zero-like sentinel."""

        session = _session()
        plan = _plan([session])

        result = classify_planned_sessions(plan=plan, linked_workout_by_session={})

        assert result[session.id] is None
        assert list(result.values()) == [None]


class TestHasLinkablePlanForWorkout:
    def test_no_plan_blocks_confirmation(self) -> None:
        assert (
            has_linkable_plan_for_workout(
                plan=None,
                plan_schema_version=0,
                workout_started_at=datetime(2026, 9, 15, tzinfo=UTC),
                timezone="UTC",
            )
            is False
        )

    def test_a_legacy_schema_plan_blocks_confirmation(self) -> None:
        plan = _plan([_session()])

        assert (
            has_linkable_plan_for_workout(
                plan=plan,
                plan_schema_version=FIRST_WEEK_PLAN_SCHEMA_VERSION - 1,
                workout_started_at=datetime(2026, 9, 15, tzinfo=UTC),
                timezone="UTC",
            )
            is False
        )

    def test_a_workout_inside_the_plan_week_is_confirmable(self) -> None:
        plan = _plan([_session()])

        assert (
            has_linkable_plan_for_workout(
                plan=plan,
                plan_schema_version=FIRST_WEEK_PLAN_SCHEMA_VERSION,
                workout_started_at=datetime(2026, 9, 15, tzinfo=UTC),
                timezone="UTC",
            )
            is True
        )

    def test_a_workout_outside_the_plan_week_blocks_confirmation(self) -> None:
        plan = _plan([_session()])

        assert (
            has_linkable_plan_for_workout(
                plan=plan,
                plan_schema_version=FIRST_WEEK_PLAN_SCHEMA_VERSION,
                workout_started_at=datetime(2026, 9, 22, tzinfo=UTC),
                timezone="UTC",
            )
            is False
        )
