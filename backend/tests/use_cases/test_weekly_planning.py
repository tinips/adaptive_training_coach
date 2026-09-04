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
    AthleteCapability,
    AthleteProfile,
    AthleteSelfReportedBaseline,
    Capability,
    GoalTemplate,
    GoalTemplateContext,
    LLMUsage,
    TrainingContext,
    TrainingGoal,
    WeeklyTrainingPlan,
)
from app.domain.enums import (
    ActivitySource,
    AthleteCapabilityStatus,
    AthleteGender,
    CapabilityKind,
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
from app.schemas.weekly_plans import FirstWeekPlan
from app.schemas.workouts import RunningWorkoutDetailsData, WorkoutCreate
from app.services.weekly_planning.service import (
    FirstWeekPlanner,
    WeeklyPlanningService,
    _plan_schema,
    next_week_start,
)

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
async def test_first_week_prompt_contains_all_confirmed_onboarding_context(
    database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, factory = database
    monkeypatch.setattr("app.services.weekly_planning.service.utc_now", lambda: NOW)
    async with factory.begin() as session:
        athlete_id = await _seed_target_goal(session)
        goal = await session.scalar(select(TrainingGoal))
        assert goal is not None
        goal.goal_metadata_jsonb = {
            "primary_goal": {"discipline": "TRIATHLON", "goal_type": "SPRINT"}
        }
        session.add(
            AthleteProfile(
                user_id=athlete_id,
                birth_year=1988,
                gender=AthleteGender.FEMALE,
                weight_kg=62.5,
                height_cm=168,
                health_limitations_text="Avoid aggravating an old knee issue.",
                weekly_availability_jsonb={
                    "schema_version": 2,
                    "status": "confirmed",
                    "days": {
                        day: {
                            "available": True,
                            "disciplines": ["running"],
                            "time_windows": [
                                {"time_of_day": "morning", "duration_minutes": 60}
                            ],
                        }
                        for day in (
                            "monday",
                            "tuesday",
                            "wednesday",
                            "thursday",
                            "friday",
                            "saturday",
                            "sunday",
                        )
                    },
                },
            )
        )
        session.add(
            AthleteSelfReportedBaseline(
                athlete_id=athlete_id,
                goal_signature=str(goal.goal_template_id),
                baseline_jsonb={
                    "running": {
                        "typical_weekly_sessions": 3,
                        "typical_weekly_duration_minutes": 150,
                        "longest_recent_run_minutes": 75,
                        "recent_race_result": {
                            "distance_km": 10,
                            "duration_seconds": 3_000,
                        },
                    },
                    "triathlon": {
                        "prior_experience": "SPRINT",
                        "weakest_discipline": "SWIMMING",
                        "open_water_confidence": "SOME_EXPERIENCE",
                    },
                    "preferences": {
                        "coaching_style": "CONSERVATIVE",
                        "desired_weekly_sessions": {"RUNNING": 3},
                        "fits_availability": True,
                    },
                },
            )
        )
        pool = Capability(
            id=uuid.uuid4(),
            code="pool_access",
            display_name="Pool access",
            description="Reliable access to a swimming pool.",
            kind=CapabilityKind.ACCESS,
            source=CatalogItemSource.SEEDED,
            status=CatalogItemStatus.ACTIVE,
            definition_version=1,
        )
        session.add(pool)
        await session.flush()
        session.add(
            AthleteCapability(
                athlete_id=athlete_id,
                capability_id=pool.id,
                status=AthleteCapabilityStatus.AVAILABLE,
            )
        )
        for days_ago in (1, 3, 5):
            await _add_running(
                session,
                athlete_id=athlete_id,
                started_at=NOW - timedelta(days=days_ago),
            )

    prepared = await _service(factory)._prepare(_identity())

    assert not isinstance(prepared, type(None))
    context = prepared.prompt_context
    assert context["athlete_profile"] == {
        "birth_year": 1988,
        "sex_category": "FEMALE",
        "weight_kg": 62.5,
        "height_cm": 168,
        "timezone": "Europe/Madrid",
    }
    assert context["health_limitations"] == "Avoid aggravating an old knee issue."
    assert context["goal"] == {
        "main_goal": "A synthetic target goal",
        "event_date": None,
        "secondary_priority": None,
        "goal_metadata": {
            "primary_goal": {"discipline": "TRIATHLON", "goal_type": "SPRINT"}
        },
        "triathlon_context": {
            "prior_experience": "SPRINT",
            "weakest_discipline": "SWIMMING",
            "open_water_confidence": "SOME_EXPERIENCE",
        },
        "performance_targets": {
            "distance_km": None,
            "elevation_m": None,
            "running_pace_seconds_per_km": None,
            "swim_pace_seconds_per_100m": None,
            "average_speed_kph": None,
            "finish_time_seconds": None,
        },
        "target_contexts": [
            {
                "code": "weekly_plan_target",
                "display_name": "Weekly Plan Target",
                "discipline": "RUNNING",
                "role": "TARGET",
            }
        ],
    }
    assert context["confirmed_availability"] is not None
    assert context["availability_constraints"] is not None
    assert context["equipment_and_access"] == [
        {"code": "pool_access", "display_name": "Pool access", "kind": "ACCESS"}
    ]
    assert context["self_reported_baseline"] is not None
    assert context["preferences"] == {
        "coaching_style": "CONSERVATIVE",
        "desired_weekly_sessions": {"RUNNING": 3},
        "desired_sessions_fit_availability": True,
    }

    first_week = FirstWeekPlanner(
        session_factory=factory,
        settings=_settings(),
        model=DeterministicFakeOnboardingModel(),
    )
    first_week_prepared = await first_week._prepare(_identity())
    assert first_week_prepared.prompt_context["planner_mode"] == "FIRST_WEEK"
    assert first_week_prepared.prompt_context["planned_disciplines"] == ["RUNNING"]
    assert "goal" not in first_week_prepared.prompt_context
    assert (
        first_week_prepared.prompt_context["resolved_intensity_zones"]["RUNNING"][
            "metric"
        ]
        == "PACE_SECONDS_PER_KM"
    )

    first_week_result = await first_week.generate_next_week(_identity())
    assert first_week_result.kind == "created"
    assert first_week_result.plan is not None
    assert isinstance(first_week_result.plan, FirstWeekPlan)
    first_session = first_week_result.plan.sessions[0]
    assert first_session.purpose
    assert first_session.intensity.metric == "RPE"
    assert first_session.intensity.rpe_range == (2, 3)
    assert first_week_result.plan.tests == ()
    assert first_week_result.plan.guardrails
    assert first_week_result.plan.logging_instructions


@pytest.mark.asyncio
async def test_first_week_schema_failure_persists_a_safe_menu_fallback(
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

    planner = FirstWeekPlanner(
        session_factory=factory,
        settings=_settings(),
        model=DeterministicFakeOnboardingModel(scenario=FakeLLMScenario.MALFORMED),
    )
    result = await planner.generate_next_week(_identity())

    assert result.kind == "created"
    assert isinstance(result.plan, FirstWeekPlan)
    assert result.plan.plan_kind == "FIRST_WEEK_MENU"
    assert result.plan.guardrails


@pytest.mark.asyncio
async def test_discarded_plan_can_be_regenerated_as_a_new_revision(
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

    planner = _service(factory)
    assert (await planner.generate_next_week(_identity())).kind == "created"
    assert await planner.delete_next_week(_identity()) is True
    assert await planner.has_plan_for_next_week(_identity()) is False
    assert (await planner.generate_next_week(_identity())).kind == "created"

    async with factory() as session:
        plans = list((await session.scalars(select(WeeklyTrainingPlan))).all())
    plans.sort(key=lambda plan: plan.revision)
    assert [plan.revision for plan in plans] == [1, 2]
    assert plans[0].superseded_at is not None
    assert plans[1].superseded_at is None


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


def test_v1_plan_payload_is_adapted() -> None:
    athlete_id = uuid.uuid4()
    stored = WeeklyTrainingPlan(
        athlete_id=athlete_id,
        week_start=date(2026, 8, 24),
        revision=1,
        plan_jsonb={
            "week_start": "2026-08-24",
            "days": [
                {
                    "date": "2026-08-24",
                    "sessions": [
                        {
                            "discipline": "RUNNING",
                            "objective": "Easy aerobic run",
                            "duration_minutes": 45,
                            "intensity": "EASY",
                            "structure": "Easy throughout.",
                        }
                    ],
                    "rest_note": None,
                },
                *[
                    {
                        "date": date.fromordinal(
                            date(2026, 8, 24).toordinal() + offset
                        ).isoformat(),
                        "sessions": [],
                        "rest_note": "Rest and recover.",
                    }
                    for offset in range(1, 7)
                ],
            ],
        },
        plan_schema_version=1,
        evidence_snapshot_jsonb={},
        input_digest="0" * 64,
        prompt_version=5,
        calculation_version=1,
        planner_model=None,
    )

    plan = _plan_schema(stored)

    session = plan.days[0].sessions[0]
    assert session.targets.duration_minutes == 45
    assert session.execution == "Easy throughout."


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
