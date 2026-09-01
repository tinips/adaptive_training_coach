"""Evidence-gated persistence tests for the first weekly planner slice."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta

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
    GoalTemplate,
    GoalTemplateContext,
    LLMUsage,
    TrainingContext,
    TrainingGoal,
    WeeklyTrainingPlan,
)
from app.domain.enums import (
    ActivitySource,
    CatalogItemSource,
    CatalogItemStatus,
    Discipline,
    DisciplineEvidenceState,
    GoalContextRole,
    GoalTemplateKind,
    RunningType,
)
from app.integrations.llm.mock import DeterministicFakeOnboardingModel, FakeLLMScenario
from app.repositories.activities import TrainingActivityRepository
from app.repositories.users import UserRepository
from app.schemas.common import TelegramIdentity
from app.schemas.workouts import RunningWorkoutDetailsData, WorkoutCreate
from app.services.weekly_planning.service import WeeklyPlanningService, next_week_start

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
        telegram_bot_username=None,
        fitness_window_days=14,
        planner_window_days=30,
    )


def _identity() -> TelegramIdentity:
    return TelegramIdentity(
        telegram_user_id=1042,
        telegram_username="weekly_plan",
        first_name="Plan",
        language_code="en",
    )


async def _seed_target_goal(
    session: AsyncSession,
    *,
    discipline: Discipline = Discipline.RUNNING,
    supporting_discipline: Discipline | None = None,
) -> uuid.UUID:
    user, _ = await UserRepository(session).get_or_create(
        telegram_user_id=_identity().telegram_user_id,
        telegram_username="weekly_plan",
        first_name="Plan",
        timezone="Europe/Madrid",
    )
    primary = GoalTemplate(
        id=uuid.uuid4(),
        code="WEEKLY_PLAN_PRIMARY",
        kind=GoalTemplateKind.PRIMARY,
        display_name="Weekly plan primary",
        description="Synthetic plan target",
        source=CatalogItemSource.SEEDED,
        status=CatalogItemStatus.ACTIVE,
        definition_version=1,
    )
    session.add(primary)
    await session.flush()
    await _add_context(
        session,
        template=primary,
        discipline=discipline,
        role=GoalContextRole.TARGET,
        code="weekly_plan_target",
    )
    supporting_goal_template_id: uuid.UUID | None = None
    if supporting_discipline is not None:
        supporting = GoalTemplate(
            id=uuid.uuid4(),
            code="WEEKLY_PLAN_SUPPORTING",
            kind=GoalTemplateKind.SUPPORTING,
            display_name="Weekly plan supporting",
            description="Synthetic plan support",
            source=CatalogItemSource.SEEDED,
            status=CatalogItemStatus.ACTIVE,
            definition_version=1,
        )
        session.add(supporting)
        await session.flush()
        await _add_context(
            session,
            template=supporting,
            discipline=supporting_discipline,
            role=GoalContextRole.SUPPORTING,
            code="weekly_plan_supporting",
        )
        supporting_goal_template_id = supporting.id
    session.add(
        TrainingGoal(
            user_id=user.id,
            main_goal="A synthetic target goal",
            secondary_priority=None,
            goal_template_id=primary.id,
            supporting_goal_template_id=supporting_goal_template_id,
        )
    )
    await session.flush()
    return user.id


async def _add_context(
    session: AsyncSession,
    *,
    template: GoalTemplate,
    discipline: Discipline,
    role: GoalContextRole,
    code: str,
) -> None:
    context = TrainingContext(
        id=uuid.uuid4(),
        code=code,
        display_name=code.replace("_", " ").title(),
        description="Synthetic context",
        discipline=discipline,
        source=CatalogItemSource.SEEDED,
        status=CatalogItemStatus.ACTIVE,
        definition_version=1,
    )
    session.add(context)
    await session.flush()
    session.add(
        GoalTemplateContext(
            goal_template_id=template.id,
            training_context_id=context.id,
            role=role,
            priority=10,
        )
    )
    await session.flush()


async def _add_running(
    session: AsyncSession,
    *,
    athlete_id: uuid.UUID,
    started_at: datetime,
) -> None:
    await TrainingActivityRepository(session).create_manual(
        WorkoutCreate(
            athlete_id=athlete_id,
            discipline=Discipline.RUNNING,
            started_at=started_at,
            duration_seconds=1800,
            source=ActivitySource.MANUAL,
            title="Private workout title",
            details=RunningWorkoutDetailsData(
                running_type=RunningType.OUTDOOR,
                distance_meters=5_000,
                moving_duration_seconds=1750,
                calories_kcal=360,
                elevation_gain_meters=80,
                average_cadence_spm=170,
            ),
        )
    )


def _service(
    factory: async_sessionmaker[AsyncSession],
    *,
    scenario: FakeLLMScenario = FakeLLMScenario.AUTO,
) -> WeeklyPlanningService:
    return WeeklyPlanningService(
        session_factory=factory,
        settings=_settings(),
        model=DeterministicFakeOnboardingModel(scenario=scenario),
    )


@pytest.mark.asyncio
async def test_preflight_blocks_insufficient_recent_target_evidence(
    database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, factory = database
    monkeypatch.setattr("app.services.weekly_planning.service.utc_now", lambda: NOW)
    async with factory.begin() as session:
        athlete_id = await _seed_target_goal(session)
        await _add_running(
            session, athlete_id=athlete_id, started_at=NOW - timedelta(days=1)
        )
        await _add_running(
            session, athlete_id=athlete_id, started_at=NOW - timedelta(days=4)
        )

    result = await _service(factory).generate_next_week(_identity())

    assert result.kind == "insufficient"
    assert result.readiness is not None
    row = result.readiness.disciplines[0]
    assert (row.discipline, row.session_count, row.active_day_count, row.state) == (
        Discipline.RUNNING,
        2,
        2,
        DisciplineEvidenceState.THIN,
    )
    async with factory() as session:
        assert (await session.scalars(select(WeeklyTrainingPlan))).all() == []


@pytest.mark.asyncio
async def test_planner_saves_one_plan_for_the_target_disciplines(
    database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, factory = database
    monkeypatch.setattr("app.services.weekly_planning.service.utc_now", lambda: NOW)
    async with factory.begin() as session:
        athlete_id = await _seed_target_goal(
            session, supporting_discipline=Discipline.STRENGTH
        )
        for days_ago in (1, 3, 5):
            await _add_running(
                session,
                athlete_id=athlete_id,
                started_at=NOW - timedelta(days=days_ago),
            )

    planner = _service(factory)
    created = await planner.generate_next_week(_identity())
    existing = await planner.generate_next_week(_identity())
    viewed = await planner.view_next_week(_identity())

    assert created.kind == "created"
    assert created.plan is not None
    assert len(created.plan.days) == 7
    assert existing.kind == "existing"
    assert viewed.kind == "existing"
    assert await planner.has_plan_for_next_week(_identity()) is True
    async with factory() as session:
        plans = (await session.scalars(select(WeeklyTrainingPlan))).all()
        usages = (await session.scalars(select(LLMUsage))).all()
    assert len(plans) == 1
    assert plans[0].week_start == date(2026, 8, 24)
    assert "Private workout title" not in str(plans[0].evidence_snapshot_jsonb)
    assert len(usages) == 1
    assert usages[0].feature == "WEEKLY_PLAN"


@pytest.mark.asyncio
async def test_invalid_provider_output_does_not_persist_a_plan(
    database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, factory = database
    monkeypatch.setattr("app.services.weekly_planning.service.utc_now", lambda: NOW)
    async with factory.begin() as session:
        athlete_id = await _seed_target_goal(session)
        for days_ago in (1, 3, 5):
            await _add_running(
                session,
                athlete_id=athlete_id,
                started_at=NOW - timedelta(days=days_ago),
            )

    result = await _service(
        factory, scenario=FakeLLMScenario.MALFORMED
    ).generate_next_week(_identity())

    assert result.kind == "unavailable"
    async with factory() as session:
        assert (await session.scalars(select(WeeklyTrainingPlan))).all() == []


@pytest.mark.asyncio
async def test_planner_accepts_sessions_from_the_full_30_day_window(
    database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, factory = database
    monkeypatch.setattr("app.services.weekly_planning.service.utc_now", lambda: NOW)
    async with factory.begin() as session:
        athlete_id = await _seed_target_goal(session)
        for days_ago in (16, 18, 20):
            await _add_running(
                session,
                athlete_id=athlete_id,
                started_at=NOW - timedelta(days=days_ago),
            )

    result = await _service(factory).generate_next_week(_identity())

    assert result.kind == "created"
    async with factory() as session:
        plan = await session.scalar(select(WeeklyTrainingPlan))
    assert plan is not None
    assert (
        plan.evidence_snapshot_jsonb["window"]["started_at"]
        == (NOW - timedelta(days=30)).isoformat()
    )


@pytest.mark.asyncio
async def test_planner_excludes_sessions_older_than_30_days(
    database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, factory = database
    monkeypatch.setattr("app.services.weekly_planning.service.utc_now", lambda: NOW)
    async with factory.begin() as session:
        athlete_id = await _seed_target_goal(session)
        for days_ago in (16, 20, 31):
            await _add_running(
                session,
                athlete_id=athlete_id,
                started_at=NOW - timedelta(days=days_ago),
            )

    result = await _service(factory).generate_next_week(_identity())

    assert result.kind == "insufficient"
    assert result.readiness is not None
    assert result.readiness.disciplines[0].session_count == 2


def test_next_week_start_is_strict_and_uses_timezone_fallback() -> None:
    monday = datetime(2026, 8, 17, 23, 30, tzinfo=UTC)
    assert next_week_start(monday, "UTC") == date(2026, 8, 24)
    # In Madrid this instant is already Tuesday, but the next Monday is still
    # the same target week. Invalid stored zones safely fall back to UTC.
    assert next_week_start(monday, "Europe/Madrid") == date(2026, 8, 24)
    assert next_week_start(monday, "not/a-timezone") == date(2026, 8, 24)


async def _seed_ready_runner(session: AsyncSession) -> uuid.UUID:
    """A single-sport athlete clearing the floor: 3 sessions on 3 days."""

    athlete_id = await _seed_target_goal(session)
    for days_ago in (1, 3, 5):
        await _add_running(
            session, athlete_id=athlete_id, started_at=NOW - timedelta(days=days_ago)
        )
    return athlete_id


@pytest.mark.asyncio
async def test_an_unusable_reply_is_logged_differently_from_an_outage(
    database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A reply with the wrong shape must not look like the provider being down."""

    _, factory = database
    monkeypatch.setattr("app.services.weekly_planning.service.utc_now", lambda: NOW)
    async with factory.begin() as session:
        await _seed_ready_runner(session)

    with caplog.at_level("ERROR"):
        result = await _service(
            factory, scenario=FakeLLMScenario.MALFORMED
        ).generate_next_week(_identity())

    assert result.kind == "unavailable"
    assert "weekly_plan_response_invalid" in caplog.text
    assert "weekly_plan_provider_error" not in caplog.text


@pytest.mark.asyncio
async def test_an_outage_is_logged_as_a_provider_error(
    database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _, factory = database
    monkeypatch.setattr("app.services.weekly_planning.service.utc_now", lambda: NOW)
    async with factory.begin() as session:
        await _seed_ready_runner(session)

    with caplog.at_level("ERROR"):
        result = await _service(
            factory, scenario=FakeLLMScenario.PROVIDER_FAILURE
        ).generate_next_week(_identity())

    assert result.kind == "unavailable"
    assert "weekly_plan_provider_error" in caplog.text
    assert "weekly_plan_response_invalid" not in caplog.text


@pytest.mark.asyncio
async def test_planner_uses_the_full_evidence_window(
    database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Three runs spread across the 30-day window are all planning evidence."""

    _, factory = database
    monkeypatch.setattr("app.services.weekly_planning.service.utc_now", lambda: NOW)
    async with factory.begin() as session:
        athlete_id = await _seed_target_goal(session)
        for days_ago in (28, 20, 2):
            await _add_running(
                session,
                athlete_id=athlete_id,
                started_at=NOW - timedelta(days=days_ago),
            )

    result = await _service(factory).generate_next_week(_identity())

    assert result.kind == "created"
    async with factory() as session:
        plan = await session.scalar(select(WeeklyTrainingPlan))
    assert plan is not None
    evidence = plan.evidence_snapshot_jsonb["recent_evidence"]
    assert evidence["RUNNING"]["session_count"] == 3


@pytest.mark.asyncio
async def test_a_supporting_goal_is_planned_alongside_the_primary_one(
    database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Strength chosen as a support must appear in the planned disciplines."""

    _, factory = database
    monkeypatch.setattr("app.services.weekly_planning.service.utc_now", lambda: NOW)
    async with factory.begin() as session:
        athlete_id = await _seed_target_goal(
            session, supporting_discipline=Discipline.STRENGTH
        )
        for days_ago in (1, 3, 5):
            await _add_running(
                session,
                athlete_id=athlete_id,
                started_at=NOW - timedelta(days=days_ago),
            )

    result = await _service(factory).generate_next_week(_identity())

    assert result.kind == "created"
    assert result.readiness is not None
    planned = {row.discipline for row in result.readiness.disciplines}
    assert Discipline.RUNNING in planned
    assert Discipline.STRENGTH in planned
