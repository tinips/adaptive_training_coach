"""The first-week evaluator's athlete-triggered delivery boundary."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.db.base import Base
from app.domain.enums import ActivitySource, AthleteGender, Discipline, RunningType
from app.repositories.activities import TrainingActivityRepository
from app.repositories.first_week_evaluation_outcomes import (
    FirstWeekEvaluationOutcomeRepository,
)
from app.repositories.planned_session_links import PlannedSessionLinkRepository
from app.repositories.profiles import ProfileRepository
from app.repositories.users import UserRepository
from app.repositories.weekly_plans import WeeklyTrainingPlanRepository
from app.schemas.weekly_plans import FirstWeekEnduranceSession
from app.schemas.workouts import RunningWorkoutDetailsData, WorkoutCreate
from app.services.weekly_evaluation.delivery import FirstWeekEvaluationService
from app.services.weekly_evaluation.linking import LinkingService
from app.services.weekly_planning.constants import FIRST_WEEK_PLAN_SCHEMA_VERSION


@pytest_asyncio.fixture
async def database() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine: AsyncEngine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    await engine.dispose()


async def _seed_first_week(
    factory: async_sessionmaker[AsyncSession],
) -> tuple[int, object, object]:
    async with factory.begin() as session:
        athlete, _ = await UserRepository(session).get_or_create(
            telegram_user_id=8172,
            telegram_username="runner",
            first_name="Ada",
            timezone="UTC",
        )
        await ProfileRepository(session).upsert_mandatory_athlete_profile(
            user_id=athlete.id,
            birth_year=1986,
            gender=AthleteGender.FEMALE,
            weight_kg=60,
            height_cm=170,
        )
        planned = FirstWeekEnduranceSession(
            discipline=Discipline.RUNNING,
            purpose="Build a controlled aerobic baseline.",
            intensity={
                "metric": "PACE_SECONDS_PER_KM",
                "target_range": [330, 360],
                "rpe_range": [3, 4],
                "guidance": "Stay relaxed and conversational.",
            },
            objective="Cover the target distance at an easy pace.",
            targets={"distance_range_meters": [5000, 6000]},
            execution="Keep the pace controlled from start to finish.",
        )
        plan = await WeeklyTrainingPlanRepository(session).create(
            athlete_id=athlete.id,
            week_start=date(2026, 9, 14),
            plan_jsonb={
                "plan_kind": "FIRST_WEEK_MENU",
                "week_start": "2026-09-14",
                "sessions": [planned.model_dump(mode="json")],
                "guardrails": ["Stop for sharp pain."],
                "logging_instructions": ["Log the completed workout."],
                "sessions_per_discipline": {"RUNNING": 1},
                "total_minutes_per_discipline": {
                    "RUNNING": planned.targets.duration_minutes
                },
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
        workout = await TrainingActivityRepository(session).create_manual(
            WorkoutCreate(
                athlete_id=athlete.id,
                discipline=Discipline.RUNNING,
                started_at=datetime(2026, 9, 15, 8, tzinfo=UTC),
                duration_seconds=1800,
                source=ActivitySource.MANUAL,
                details=RunningWorkoutDetailsData(
                    running_type=RunningType.OUTDOOR,
                    distance_meters=5000,
                    moving_duration_seconds=1800,
                    average_heart_rate=140,
                    max_heart_rate=155,
                ),
            )
        )
        plan_session_id = planned.id
        await LinkingService(session).link_workout_to_session(
            athlete_id=athlete.id,
            plan_id=plan.id,
            plan_session_id=plan_session_id,
            workout_id=workout.id,
        )
    return 8172, plan, workout


@pytest.mark.asyncio
async def test_manual_evaluation_compares_explicit_links_and_is_idempotent(
    database: async_sessionmaker[AsyncSession],
) -> None:
    telegram_user_id, plan, workout = await _seed_first_week(database)
    service = FirstWeekEvaluationService(session_factory=database)

    first = await service.evaluate_plan(
        telegram_user_id=telegram_user_id,
        plan_id=plan.id,  # type: ignore[attr-defined]
    )
    repeated = await service.evaluate_plan(
        telegram_user_id=telegram_user_id,
        plan_id=plan.id,  # type: ignore[attr-defined]
    )

    assert first.evaluation.matched_count == 1
    assert first.evaluation.missed_count == 0
    assert first.evaluation.per_session_insights[0].workout_id == workout.id  # type: ignore[attr-defined]
    assert repeated.outcome_id == first.outcome_id
    async with database() as session:
        rows = await FirstWeekEvaluationOutcomeRepository(session).get_all(
            plan_id=plan.id  # type: ignore[attr-defined]
        )
        links = await PlannedSessionLinkRepository(session).get_for_plan(
            plan_id=plan.id  # type: ignore[attr-defined]
        )
    assert len(rows) == 1
    assert len(links) == 1
