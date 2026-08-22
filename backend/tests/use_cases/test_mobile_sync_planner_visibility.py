"""HealthKit POC data reaches the existing weekly-planner evidence path."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import Settings
from app.db.base import Base
from app.db.models import (
    AthleteBaselineAssessment,
    GoalTemplate,
    GoalTemplateContext,
    TrainingContext,
    TrainingGoal,
    WeeklyTrainingPlan,
)
from app.domain.enums import (
    CatalogItemSource,
    CatalogItemStatus,
    Discipline,
    GoalContextRole,
    GoalTemplateKind,
)
from app.integrations.llm.mock import DeterministicFakeOnboardingModel
from app.repositories.users import UserRepository
from app.schemas.common import TelegramIdentity
from app.schemas.mobile_sync import HealthKitWorkoutPayload
from app.services.mobile_sync import MobileSyncService
from app.services.weekly_planning.service import WeeklyPlanningService

NOW = datetime(2026, 8, 21, 12, tzinfo=UTC)


@pytest_asyncio.fixture
async def database() -> AsyncIterator[
    tuple[AsyncEngine, async_sessionmaker[AsyncSession]]
]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    yield engine, factory
    await engine.dispose()


def _settings() -> Settings:
    return Settings(
        environment="test",
        database_url="sqlite+aiosqlite:///:memory:",
        mobile_sync_enabled=True,
        fitness_window_days=14,
        planner_window_days=30,
    )


def _identity() -> TelegramIdentity:
    return TelegramIdentity(
        telegram_user_id=810,
        telegram_username="healthkit_planner",
        first_name="HealthKit",
        language_code="en",
    )


async def _seed_running_goal(
    session: AsyncSession,
) -> uuid.UUID:
    identity = _identity()
    user, _ = await UserRepository(session).get_or_create(
        telegram_user_id=identity.telegram_user_id,
        telegram_username=identity.telegram_username,
        first_name=identity.first_name,
        language_code=identity.language_code,
    )
    goal_template = GoalTemplate(
        id=uuid.uuid4(),
        code="HEALTHKIT_PLANNER_GOAL",
        kind=GoalTemplateKind.PRIMARY,
        display_name="HealthKit planner goal",
        description="Synthetic running target",
        source=CatalogItemSource.SEEDED,
        status=CatalogItemStatus.ACTIVE,
        definition_version=1,
    )
    context = TrainingContext(
        id=uuid.uuid4(),
        code="healthkit_running_target",
        display_name="HealthKit running target",
        description="Synthetic running context",
        discipline=Discipline.RUNNING,
        source=CatalogItemSource.SEEDED,
        status=CatalogItemStatus.ACTIVE,
        definition_version=1,
    )
    session.add_all((goal_template, context))
    await session.flush()
    session.add(
        GoalTemplateContext(
            goal_template_id=goal_template.id,
            training_context_id=context.id,
            role=GoalContextRole.TARGET,
            priority=1,
        )
    )
    session.add(
        TrainingGoal(
            user_id=user.id,
            main_goal="Synthetic running target",
            target_outcome="Finish safely",
            original_description="Synthetic running target",
            goal_template_id=goal_template.id,
        )
    )
    await session.flush()
    return user.id


@pytest.mark.asyncio
async def test_healthkit_sync_workouts_are_counted_by_weekly_planner(
    database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _engine, factory = database
    settings = _settings()
    async with factory() as session, session.begin():
        athlete_id = await _seed_running_goal(session)

    sync = MobileSyncService(
        session_factory=factory,
        settings=settings,
        clock=lambda: NOW,
    )
    pairing = await sync.issue_pairing_code(_identity())
    token = await sync.pair(
        pairing_code=pairing.code,
        installation_id=uuid.uuid4(),
    )
    await sync.sync_healthkit_workouts(
        access_token=token,
        workouts=tuple(
            HealthKitWorkoutPayload(
                workout_uuid=uuid.uuid4(),
                activity_type="running",
                started_at=NOW - timedelta(days=days_ago, minutes=30),
                ended_at=NOW - timedelta(days=days_ago),
                duration_seconds=1800,
                distance_meters=5_000,
                calories_kcal=350,
            )
            for days_ago in (1, 3, 5)
        ),
    )

    monkeypatch.setattr("app.services.weekly_planning.service.utc_now", lambda: NOW)
    result = await WeeklyPlanningService(
        session_factory=factory,
        settings=settings,
        model=DeterministicFakeOnboardingModel(),
    ).generate_next_week(_identity())

    assert result.kind == "created"
    async with factory() as session:
        plan = await session.scalar(select(WeeklyTrainingPlan))
        baseline = await session.scalar(
            select(AthleteBaselineAssessment).where(
                AthleteBaselineAssessment.athlete_id == athlete_id
            )
        )
    assert plan is not None
    recent_evidence = cast(
        dict[str, dict[str, object]],
        plan.evidence_snapshot_jsonb["recent_evidence"],
    )
    assert recent_evidence["RUNNING"]["session_count"] == 3
    assert baseline is not None
