"""The persisted outcome exposes what the (separate) fitness-state brief needs.

Test-first step 9 of docs/briefs/backlog/first-week-evaluator.md: "Test the
persisted outcome exposes all versioned evidence references required by the
separate fitness-state input contract; do not write a state row here." No
fitness-state table, model, or repository is created by this test or this
brief; docs/briefs/backlog/fitness-state.md owns that milestone. This test
only proves the read-side handoff surface is complete and versioned.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import date

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.db.base import Base
from app.domain.enums import (
    Discipline,
    PlannedSessionLinkStatus,
    SessionIntentVerdict,
    SessionOutputVerdict,
)
from app.repositories.first_week_evaluation_outcomes import (
    FirstWeekEvaluationOutcomeRepository,
)
from app.repositories.users import UserRepository
from app.repositories.weekly_plans import WeeklyTrainingPlanRepository
from app.schemas.weekly_evaluation import PerSessionInsight
from app.services.weekly_evaluation.aggregation import aggregate_week
from app.services.weekly_evaluation.outcomes import OutcomeService
from app.services.weekly_planning.constants import FIRST_WEEK_PLAN_SCHEMA_VERSION


@pytest_asyncio.fixture
async def database() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine: AsyncEngine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    await engine.dispose()


@pytest.mark.asyncio
async def test_persisted_outcome_carries_every_versioned_reference_a_future_fitness_state_consumer_needs(  # noqa: E501
    database: async_sessionmaker[AsyncSession],
) -> None:
    async with database() as session:
        athlete, _ = await UserRepository(session).get_or_create(
            telegram_user_id=1, telegram_username="ada", first_name="Ada"
        )
        plan = await WeeklyTrainingPlanRepository(session).create(
            athlete_id=athlete.id,
            week_start=date(2026, 9, 14),
            plan_jsonb={
                "plan_kind": "FIRST_WEEK_MENU",
                "week_start": "2026-09-14",
                "sessions": [],
                "guardrails": ["x"],
                "logging_instructions": ["x"],
                "sessions_per_discipline": {},
                "total_minutes_per_discipline": {},
            },
            plan_schema_version=FIRST_WEEK_PLAN_SCHEMA_VERSION,
            validation_jsonb=None,
            evidence_snapshot_jsonb={},
            input_digest="0" * 64,
            prompt_version=1,
            calculation_version=1,
            planner_model=None,
            revision=1,
        )
        await session.commit()

        workout_id = uuid.uuid4()
        plan_session_id = uuid.uuid4()
        insight = PerSessionInsight(
            plan_session_id=plan_session_id,
            workout_id=workout_id,
            status=PlannedSessionLinkStatus.MATCHED,
            discipline=Discipline.RUNNING,
            planned_duration_seconds=2400,
            actual_duration_seconds=2400,
            output_metric="PACE_SECONDS_PER_KM",
            actual_output_value=300.0,
            output_verdict=SessionOutputVerdict.WITHIN_EXPECTED_OUTPUT,
            actual_average_hr_bpm=120.0,
            intent_verdict=SessionIntentVerdict.AS_PRESCRIBED,
            intent_point_value=0,
            output_point_value=0,
            point_value=0,
        )
        evaluation = aggregate_week(
            plan_id=plan.id,
            plan_revision=plan.revision,
            week_start=plan.week_start,
            timezone=athlete.timezone,
            insights=(insight,),
        )

        outcome = await OutcomeService(session).persist_evaluation(
            athlete_id=athlete.id, evaluation=evaluation
        )
        await session.commit()

        stored = await FirstWeekEvaluationOutcomeRepository(session).get_latest(
            plan_id=plan.id
        )
        assert stored is not None
        payload = stored.payload_jsonb

        # Identity/version: which plan revision, which week, which rule
        # version produced this -- required to reproduce or supersede it.
        assert payload["plan_id"] == str(plan.id)
        assert payload["plan_revision"] == plan.revision
        assert payload["week_start"] == "2026-09-14"
        assert payload["evaluator_version"] == outcome.evaluator_version
        assert stored.evaluation_revision == 1

        # Per-session evidence references: a future fitness-state snapshot
        # needs to know which workout(s) backed this week's evaluation and
        # their disciplines/verdicts, not just the aggregate signal.
        [stored_insight] = payload["per_session_insights"]
        assert stored_insight["plan_session_id"] == str(plan_session_id)
        assert stored_insight["workout_id"] == str(workout_id)
        assert stored_insight["discipline"] == "RUNNING"
        assert stored_insight["status"] == "MATCHED"
        assert stored_insight["intent_verdict"] == "AS_PRESCRIBED"
        assert stored_insight["output_verdict"] == "WITHIN_EXPECTED_OUTPUT"

        # The suggested signal itself, the facts driving it, and the
        # per-discipline efficiency factor -- the actual handoff payload a
        # future last_week_feedback/fitness-state reader would consume.
        assert "signal" in payload
        assert "signal_reasons" in payload
        assert "efficiency_factor_by_discipline" in payload
        assert "thriving_gate" in payload


@pytest.mark.asyncio
async def test_this_brief_writes_no_fitness_state_row(
    database: async_sessionmaker[AsyncSession],
) -> None:
    """Only first_week_evaluation_outcomes exists; no snapshot table.

    docs/decisions/locked.md: "Fitness-state storage, first-state seeding,
    correction semantics, and last_week_feedback remain deferred to their
    owning design documents. They do not block first-week evaluation
    persistence." This test documents the boundary directly: creating all
    tables in Base.metadata and confirming no fitness-snapshot table is
    among them proves this codebase, as of this brief, still has none.
    """

    table_names = set(Base.metadata.tables.keys())
    assert "first_week_evaluation_outcomes" in table_names
    assert not any("fitness_snapshot" in name for name in table_names)
    assert not any("athlete_fitness" in name for name in table_names)
