"""Immutable, versioned outcome persistence: idempotent replay, correction.

Test-first step 8 of docs/briefs/backlog/first-week-evaluator.md.
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
from app.db.models import User
from app.domain.enums import WeeklySignal
from app.repositories.first_week_evaluation_outcomes import (
    FirstWeekEvaluationOutcomeRepository,
)
from app.repositories.users import UserRepository
from app.repositories.weekly_plans import WeeklyTrainingPlanRepository
from app.schemas.weekly_evaluation import ThrivingGateResult, WeeklyVolumeRangeResult
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


async def _make_user(session: AsyncSession) -> User:
    user, _ = await UserRepository(session).get_or_create(
        telegram_user_id=1, telegram_username="ada", first_name="Ada"
    )
    return user


async def _make_plan(session: AsyncSession, athlete_id: uuid.UUID):
    return await WeeklyTrainingPlanRepository(session).create(
        athlete_id=athlete_id,
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


def _evaluation(*, plan_id: uuid.UUID):
    return aggregate_week(
        plan_id=plan_id,
        plan_revision=1,
        week_start=date(2026, 9, 14),
        timezone="UTC",
        insights=(),
    )


@pytest.mark.asyncio
async def test_first_persist_creates_revision_one(
    database: async_sessionmaker[AsyncSession],
) -> None:
    async with database() as session:
        athlete = await _make_user(session)
        plan = await _make_plan(session, athlete.id)
        await session.commit()

        outcome = await OutcomeService(session).persist_evaluation(
            athlete_id=athlete.id, evaluation=_evaluation(plan_id=plan.id)
        )
        await session.commit()

        assert outcome.evaluation_revision == 1
        assert outcome.athlete_id == athlete.id
        assert outcome.plan_id == plan.id
        assert outcome.week_start == date(2026, 9, 14)
        assert outcome.evaluator_version == 1


@pytest.mark.asyncio
async def test_payload_carries_the_full_evaluation_shape(
    database: async_sessionmaker[AsyncSession],
) -> None:
    async with database() as session:
        athlete = await _make_user(session)
        plan = await _make_plan(session, athlete.id)
        await session.commit()

        outcome = await OutcomeService(session).persist_evaluation(
            athlete_id=athlete.id, evaluation=_evaluation(plan_id=plan.id)
        )

        assert (
            outcome.payload_jsonb["signal"] == WeeklySignal.INSUFFICIENT_EVIDENCE.value
        )
        assert "per_session_insights" in outcome.payload_jsonb
        assert "thriving_gate" in outcome.payload_jsonb


@pytest.mark.asyncio
async def test_replaying_the_same_evaluation_is_idempotent(
    database: async_sessionmaker[AsyncSession],
) -> None:
    async with database() as session:
        athlete = await _make_user(session)
        plan = await _make_plan(session, athlete.id)
        await session.commit()

        service = OutcomeService(session)
        first = await service.persist_evaluation(
            athlete_id=athlete.id, evaluation=_evaluation(plan_id=plan.id)
        )
        second = await service.persist_evaluation(
            athlete_id=athlete.id, evaluation=_evaluation(plan_id=plan.id)
        )

        assert first.id == second.id
        assert second.evaluation_revision == 1

        all_rows = await FirstWeekEvaluationOutcomeRepository(session).get_all(
            plan_id=plan.id
        )
        assert len(all_rows) == 1


@pytest.mark.asyncio
async def test_a_changed_result_creates_a_superseding_revision(
    database: async_sessionmaker[AsyncSession],
) -> None:
    async with database() as session:
        athlete = await _make_user(session)
        plan = await _make_plan(session, athlete.id)
        await session.commit()

        service = OutcomeService(session)
        first = await service.persist_evaluation(
            athlete_id=athlete.id, evaluation=_evaluation(plan_id=plan.id)
        )

        changed = _evaluation(plan_id=plan.id).model_copy(
            update={
                "signal": WeeklySignal.ON_TRACK,
                "matched_count": 1,
                "session_completion_percent": 100.0,
                "comparable_session_count": 1,
            }
        )
        second = await service.persist_evaluation(
            athlete_id=athlete.id, evaluation=changed
        )

        assert second.evaluation_revision == 2
        assert second.id != first.id

        all_rows = await FirstWeekEvaluationOutcomeRepository(session).get_all(
            plan_id=plan.id
        )
        assert len(all_rows) == 2  # revision 1 is retained, not overwritten
        assert all_rows[0].evaluation_revision == 1
        assert all_rows[1].evaluation_revision == 2
        assert (
            all_rows[0].payload_jsonb["signal"]
            == WeeklySignal.INSUFFICIENT_EVIDENCE.value
        )
        assert all_rows[1].payload_jsonb["signal"] == WeeklySignal.ON_TRACK.value


@pytest.mark.asyncio
async def test_outcomes_are_scoped_to_athlete_week_and_plan_revision(
    database: async_sessionmaker[AsyncSession],
) -> None:
    async with database() as session:
        athlete_one = await _make_user(session)
        user_repo = UserRepository(session)
        athlete_two, _ = await user_repo.get_or_create(
            telegram_user_id=2, telegram_username="bea", first_name="Bea"
        )
        plan_one = await _make_plan(session, athlete_one.id)
        plan_two = await _make_plan(session, athlete_two.id)
        await session.commit()

        service = OutcomeService(session)
        await service.persist_evaluation(
            athlete_id=athlete_one.id, evaluation=_evaluation(plan_id=plan_one.id)
        )
        await service.persist_evaluation(
            athlete_id=athlete_two.id, evaluation=_evaluation(plan_id=plan_two.id)
        )

        repo = FirstWeekEvaluationOutcomeRepository(session)
        rows_one = await repo.get_all(plan_id=plan_one.id)
        rows_two = await repo.get_all(plan_id=plan_two.id)

        assert len(rows_one) == 1
        assert len(rows_two) == 1
        assert rows_one[0].athlete_id == athlete_one.id
        assert rows_two[0].athlete_id == athlete_two.id


def test_thriving_gate_and_volume_result_are_json_round_trippable() -> None:
    """Sanity: the nested contracts embed cleanly in the JSON payload."""

    gate = ThrivingGateResult(
        met=False,
        zero_overcooked=True,
        zero_below_expected_output=True,
        zero_variance_flag=True,
        volume_not_below_range=True,
        has_positive_efficiency_session=False,
    )
    volume = WeeklyVolumeRangeResult(status="WITHIN_RANGE")
    assert gate.model_dump(mode="json")["met"] is False
    assert volume.model_dump(mode="json")["status"] == "WITHIN_RANGE"
