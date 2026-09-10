"""Link persistence/service: ownership, eligibility, uniqueness, relinking.

Test-first step 3 of docs/briefs/backlog/first-week-evaluator.md.
"""

from __future__ import annotations

import uuid
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
from app.db.models import User
from app.domain.enums import ActivitySource, Discipline, RunningType
from app.repositories.errors import OwnedRecordNotFoundError
from app.repositories.planned_session_links import PlannedSessionLinkRepository
from app.repositories.users import UserRepository
from app.repositories.weekly_plans import WeeklyTrainingPlanRepository
from app.schemas.weekly_plans import FirstWeekEnduranceSession
from app.schemas.workouts import RunningWorkoutDetailsData, WorkoutCreate
from app.services.weekly_evaluation.errors import (
    PlannedSessionNotFoundError,
    PlanNotLinkableError,
    WorkoutAlreadyLinkedError,
    WorkoutNotEligibleError,
)
from app.services.weekly_evaluation.linking import LinkingService
from app.services.weekly_planning.constants import FIRST_WEEK_PLAN_SCHEMA_VERSION


@pytest_asyncio.fixture
async def database() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine: AsyncEngine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    await engine.dispose()


def _endurance_session() -> FirstWeekEnduranceSession:
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


async def _make_user(
    session: AsyncSession, *, telegram_id: int, timezone: str | None = "UTC"
) -> User:
    user, _ = await UserRepository(session).get_or_create(
        telegram_user_id=telegram_id,
        telegram_username=f"athlete{telegram_id}",
        first_name="Ada",
        timezone=timezone,
    )
    return user


async def _make_plan(
    session: AsyncSession,
    *,
    athlete_id: uuid.UUID,
    week_start: date,
    schema_version: int = FIRST_WEEK_PLAN_SCHEMA_VERSION,
    sessions: list[FirstWeekEnduranceSession] | None = None,
):
    sessions = sessions if sessions is not None else [_endurance_session()]
    counts = {
        s.discipline: sum(1 for x in sessions if x.discipline == s.discipline)
        for s in sessions
    }
    minutes: dict[Discipline, int] = {}
    for s in sessions:
        minutes[s.discipline] = minutes.get(s.discipline, 0) + (
            s.targets.duration_minutes or 0
        )
    plan_jsonb = {
        "plan_kind": "FIRST_WEEK_MENU",
        "week_start": week_start.isoformat(),
        "sessions": [s.model_dump(mode="json") for s in sessions],
        "guardrails": ["Stop if you feel sharp pain."],
        "logging_instructions": ["Log every session you complete."],
        "sessions_per_discipline": {k.value: v for k, v in counts.items()},
        "total_minutes_per_discipline": {k.value: v for k, v in minutes.items()},
    }
    return await WeeklyTrainingPlanRepository(session).create(
        athlete_id=athlete_id,
        week_start=week_start,
        plan_jsonb=plan_jsonb,
        plan_schema_version=schema_version,
        validation_jsonb=None,
        evidence_snapshot_jsonb={},
        input_digest="0" * 64,
        prompt_version=1,
        calculation_version=1,
        planner_model=None,
        revision=1,
    )


async def _make_workout(
    session: AsyncSession, *, athlete_id: uuid.UUID, started_at: datetime
):
    from app.repositories.activities import TrainingActivityRepository

    return await TrainingActivityRepository(session).create_manual(
        WorkoutCreate(
            athlete_id=athlete_id,
            discipline=Discipline.RUNNING,
            started_at=started_at,
            duration_seconds=2400,
            source=ActivitySource.MANUAL,
            details=RunningWorkoutDetailsData(
                running_type=RunningType.OUTDOOR,
                distance_meters=8500.0,
                moving_duration_seconds=2400,
            ),
        )
    )


class TestValidLink:
    @pytest.mark.asyncio
    async def test_a_valid_link_is_created(
        self, database: async_sessionmaker[AsyncSession]
    ) -> None:
        async with database() as session:
            athlete = await _make_user(session, telegram_id=1)
            plan = await _make_plan(
                session, athlete_id=athlete.id, week_start=date(2026, 9, 14)
            )
            workout = await _make_workout(
                session,
                athlete_id=athlete.id,
                started_at=datetime(2026, 9, 15, 8, tzinfo=UTC),
            )
            await session.commit()

        async with database() as session:
            stored_plan = await WeeklyTrainingPlanRepository(session).get_by_id(
                plan_id=plan.id
            )
            assert stored_plan is not None
            real_plan_session_id = uuid.UUID(
                stored_plan.plan_jsonb["sessions"][0]["id"]
            )
            link = await LinkingService(session).link_workout_to_session(
                athlete_id=athlete.id,
                plan_id=plan.id,
                plan_session_id=real_plan_session_id,
                workout_id=workout.id,
            )
            await session.commit()
            assert link.plan_session_id == real_plan_session_id
            assert link.workout_id == workout.id
            assert link.athlete_id == athlete.id


@pytest.mark.asyncio
async def test_nonexistent_plan_raises_not_found(
    database: async_sessionmaker[AsyncSession],
) -> None:
    async with database() as session:
        athlete = await _make_user(session, telegram_id=2)
        workout = await _make_workout(
            session, athlete_id=athlete.id, started_at=datetime(2026, 9, 15, tzinfo=UTC)
        )
        await session.commit()

    async with database() as session:
        with pytest.raises(OwnedRecordNotFoundError):
            await LinkingService(session).link_workout_to_session(
                athlete_id=athlete.id,
                plan_id=uuid.uuid4(),
                plan_session_id=uuid.uuid4(),
                workout_id=workout.id,
            )


@pytest.mark.asyncio
async def test_nonexistent_session_reference_raises(
    database: async_sessionmaker[AsyncSession],
) -> None:
    async with database() as session:
        athlete = await _make_user(session, telegram_id=3)
        plan = await _make_plan(
            session, athlete_id=athlete.id, week_start=date(2026, 9, 14)
        )
        workout = await _make_workout(
            session, athlete_id=athlete.id, started_at=datetime(2026, 9, 15, tzinfo=UTC)
        )
        await session.commit()

    async with database() as session:
        with pytest.raises(PlannedSessionNotFoundError):
            await LinkingService(session).link_workout_to_session(
                athlete_id=athlete.id,
                plan_id=plan.id,
                plan_session_id=uuid.uuid4(),
                workout_id=workout.id,
            )


@pytest.mark.asyncio
async def test_cross_athlete_plan_is_rejected(
    database: async_sessionmaker[AsyncSession],
) -> None:
    async with database() as session:
        owner = await _make_user(session, telegram_id=4)
        intruder = await _make_user(session, telegram_id=5)
        plan = await _make_plan(
            session, athlete_id=owner.id, week_start=date(2026, 9, 14)
        )
        workout = await _make_workout(
            session,
            athlete_id=intruder.id,
            started_at=datetime(2026, 9, 15, tzinfo=UTC),
        )
        await session.commit()

    async with database() as session:
        stored_plan = await WeeklyTrainingPlanRepository(session).get_by_id(
            plan_id=plan.id
        )
        assert stored_plan is not None
        plan_session_id = uuid.UUID(stored_plan.plan_jsonb["sessions"][0]["id"])
        with pytest.raises(OwnedRecordNotFoundError):
            await LinkingService(session).link_workout_to_session(
                athlete_id=intruder.id,
                plan_id=plan.id,
                plan_session_id=plan_session_id,
                workout_id=workout.id,
            )


@pytest.mark.asyncio
async def test_cross_athlete_workout_is_rejected(
    database: async_sessionmaker[AsyncSession],
) -> None:
    async with database() as session:
        owner = await _make_user(session, telegram_id=6)
        other = await _make_user(session, telegram_id=7)
        plan = await _make_plan(
            session, athlete_id=owner.id, week_start=date(2026, 9, 14)
        )
        other_workout = await _make_workout(
            session, athlete_id=other.id, started_at=datetime(2026, 9, 15, tzinfo=UTC)
        )
        await session.commit()

    async with database() as session:
        stored_plan = await WeeklyTrainingPlanRepository(session).get_by_id(
            plan_id=plan.id
        )
        assert stored_plan is not None
        plan_session_id = uuid.UUID(stored_plan.plan_jsonb["sessions"][0]["id"])
        with pytest.raises(OwnedRecordNotFoundError):
            await LinkingService(session).link_workout_to_session(
                athlete_id=owner.id,
                plan_id=plan.id,
                plan_session_id=plan_session_id,
                workout_id=other_workout.id,
            )


@pytest.mark.asyncio
async def test_legacy_schema_version_plan_is_not_linkable(
    database: async_sessionmaker[AsyncSession],
) -> None:
    async with database() as session:
        athlete = await _make_user(session, telegram_id=8)
        plan = await _make_plan(
            session,
            athlete_id=athlete.id,
            week_start=date(2026, 9, 14),
            schema_version=4,
        )
        workout = await _make_workout(
            session, athlete_id=athlete.id, started_at=datetime(2026, 9, 15, tzinfo=UTC)
        )
        await session.commit()

    async with database() as session:
        stored_plan = await WeeklyTrainingPlanRepository(session).get_by_id(
            plan_id=plan.id
        )
        assert stored_plan is not None
        plan_session_id = uuid.UUID(stored_plan.plan_jsonb["sessions"][0]["id"])
        with pytest.raises(PlanNotLinkableError):
            await LinkingService(session).link_workout_to_session(
                athlete_id=athlete.id,
                plan_id=plan.id,
                plan_session_id=plan_session_id,
                workout_id=workout.id,
            )


@pytest.mark.asyncio
async def test_superseded_plan_revision_is_not_linkable(
    database: async_sessionmaker[AsyncSession],
) -> None:
    async with database() as session:
        athlete = await _make_user(session, telegram_id=9)
        plan = await _make_plan(
            session, athlete_id=athlete.id, week_start=date(2026, 9, 14)
        )
        await WeeklyTrainingPlanRepository(session).supersede_current(
            athlete_id=athlete.id, week_start=date(2026, 9, 14)
        )
        workout = await _make_workout(
            session, athlete_id=athlete.id, started_at=datetime(2026, 9, 15, tzinfo=UTC)
        )
        await session.commit()

    async with database() as session:
        stored_plan = await WeeklyTrainingPlanRepository(session).get_by_id(
            plan_id=plan.id
        )
        assert stored_plan is not None
        plan_session_id = uuid.UUID(stored_plan.plan_jsonb["sessions"][0]["id"])
        with pytest.raises(PlanNotLinkableError):
            await LinkingService(session).link_workout_to_session(
                athlete_id=athlete.id,
                plan_id=plan.id,
                plan_session_id=plan_session_id,
                workout_id=workout.id,
            )


@pytest.mark.asyncio
async def test_workout_outside_the_plan_week_is_not_eligible(
    database: async_sessionmaker[AsyncSession],
) -> None:
    async with database() as session:
        athlete = await _make_user(session, telegram_id=10)
        plan = await _make_plan(
            session, athlete_id=athlete.id, week_start=date(2026, 9, 14)
        )
        # A week later: outside the Monday-Sunday plan week.
        workout = await _make_workout(
            session,
            athlete_id=athlete.id,
            started_at=datetime(2026, 9, 22, 8, tzinfo=UTC),
        )
        await session.commit()

    async with database() as session:
        stored_plan = await WeeklyTrainingPlanRepository(session).get_by_id(
            plan_id=plan.id
        )
        assert stored_plan is not None
        plan_session_id = uuid.UUID(stored_plan.plan_jsonb["sessions"][0]["id"])
        with pytest.raises(WorkoutNotEligibleError):
            await LinkingService(session).link_workout_to_session(
                athlete_id=athlete.id,
                plan_id=plan.id,
                plan_session_id=plan_session_id,
                workout_id=workout.id,
            )


@pytest.mark.asyncio
async def test_workout_just_after_local_midnight_on_the_plan_sunday_is_eligible(
    database: async_sessionmaker[AsyncSession],
) -> None:
    """Regresses the design doc's flagged UTC-midnight edge-of-day bug.

    Plan week is Mon 2026-09-14 to Sun 2026-09-20 athlete-local
    (America/New_York, UTC-4 in September). 2026-09-21T02:00 UTC is
    2026-09-20T22:00 local -- still Sunday locally, so still eligible, even
    though it is already Monday in UTC.
    """

    async with database() as session:
        athlete = await _make_user(session, telegram_id=11, timezone="America/New_York")
        plan = await _make_plan(
            session, athlete_id=athlete.id, week_start=date(2026, 9, 14)
        )
        workout = await _make_workout(
            session,
            athlete_id=athlete.id,
            started_at=datetime(2026, 9, 21, 2, 0, tzinfo=UTC),
        )
        await session.commit()

    async with database() as session:
        stored_plan = await WeeklyTrainingPlanRepository(session).get_by_id(
            plan_id=plan.id
        )
        assert stored_plan is not None
        plan_session_id = uuid.UUID(stored_plan.plan_jsonb["sessions"][0]["id"])
        link = await LinkingService(session).link_workout_to_session(
            athlete_id=athlete.id,
            plan_id=plan.id,
            plan_session_id=plan_session_id,
            workout_id=workout.id,
        )
        assert link is not None


class TestUniquenessAndRelinking:
    @pytest.mark.asyncio
    async def test_relinking_a_session_to_a_different_workout_updates_in_place(
        self, database: async_sessionmaker[AsyncSession]
    ) -> None:
        async with database() as session:
            athlete = await _make_user(session, telegram_id=12)
            plan = await _make_plan(
                session, athlete_id=athlete.id, week_start=date(2026, 9, 14)
            )
            first_workout = await _make_workout(
                session,
                athlete_id=athlete.id,
                started_at=datetime(2026, 9, 15, tzinfo=UTC),
            )
            second_workout = await _make_workout(
                session,
                athlete_id=athlete.id,
                started_at=datetime(2026, 9, 16, tzinfo=UTC),
            )
            await session.commit()

        async with database() as session:
            stored_plan = await WeeklyTrainingPlanRepository(session).get_by_id(
                plan_id=plan.id
            )
            assert stored_plan is not None
            plan_session_id = uuid.UUID(stored_plan.plan_jsonb["sessions"][0]["id"])
            service = LinkingService(session)
            first_link = await service.link_workout_to_session(
                athlete_id=athlete.id,
                plan_id=plan.id,
                plan_session_id=plan_session_id,
                workout_id=first_workout.id,
            )
            second_link = await service.link_workout_to_session(
                athlete_id=athlete.id,
                plan_id=plan.id,
                plan_session_id=plan_session_id,
                workout_id=second_workout.id,
            )
            await session.commit()
            assert first_link.id == second_link.id  # same row, updated in place
            assert second_link.workout_id == second_workout.id

            all_links = await PlannedSessionLinkRepository(session).get_for_plan(
                plan_id=plan.id
            )
            assert len(all_links) == 1

    @pytest.mark.asyncio
    async def test_relinking_is_idempotent_for_a_double_click(
        self, database: async_sessionmaker[AsyncSession]
    ) -> None:
        async with database() as session:
            athlete = await _make_user(session, telegram_id=13)
            plan = await _make_plan(
                session, athlete_id=athlete.id, week_start=date(2026, 9, 14)
            )
            workout = await _make_workout(
                session,
                athlete_id=athlete.id,
                started_at=datetime(2026, 9, 15, tzinfo=UTC),
            )
            await session.commit()

        async with database() as session:
            stored_plan = await WeeklyTrainingPlanRepository(session).get_by_id(
                plan_id=plan.id
            )
            assert stored_plan is not None
            plan_session_id = uuid.UUID(stored_plan.plan_jsonb["sessions"][0]["id"])
            service = LinkingService(session)
            first = await service.link_workout_to_session(
                athlete_id=athlete.id,
                plan_id=plan.id,
                plan_session_id=plan_session_id,
                workout_id=workout.id,
            )
            second = await service.link_workout_to_session(
                athlete_id=athlete.id,
                plan_id=plan.id,
                plan_session_id=plan_session_id,
                workout_id=workout.id,
            )
            assert first.id == second.id

    @pytest.mark.asyncio
    async def test_a_workout_already_linked_to_a_different_session_is_rejected(
        self, database: async_sessionmaker[AsyncSession]
    ) -> None:
        two_sessions = [_endurance_session(), _endurance_session()]
        async with database() as session:
            athlete = await _make_user(session, telegram_id=14)
            plan = await _make_plan(
                session,
                athlete_id=athlete.id,
                week_start=date(2026, 9, 14),
                sessions=two_sessions,
            )
            workout = await _make_workout(
                session,
                athlete_id=athlete.id,
                started_at=datetime(2026, 9, 15, tzinfo=UTC),
            )
            await session.commit()

        async with database() as session:
            stored_plan = await WeeklyTrainingPlanRepository(session).get_by_id(
                plan_id=plan.id
            )
            assert stored_plan is not None
            session_ids = [
                uuid.UUID(raw["id"]) for raw in stored_plan.plan_jsonb["sessions"]
            ]
            service = LinkingService(session)
            await service.link_workout_to_session(
                athlete_id=athlete.id,
                plan_id=plan.id,
                plan_session_id=session_ids[0],
                workout_id=workout.id,
            )
            with pytest.raises(WorkoutAlreadyLinkedError):
                await service.link_workout_to_session(
                    athlete_id=athlete.id,
                    plan_id=plan.id,
                    plan_session_id=session_ids[1],
                    workout_id=workout.id,
                )

    @pytest.mark.asyncio
    async def test_duplicate_workout_rows_each_link_to_at_most_one_session(
        self, database: async_sessionmaker[AsyncSession]
    ) -> None:
        """Two separately-imported workout rows never collapse into one link."""

        two_sessions = [_endurance_session(), _endurance_session()]
        async with database() as session:
            athlete = await _make_user(session, telegram_id=15)
            plan = await _make_plan(
                session,
                athlete_id=athlete.id,
                week_start=date(2026, 9, 14),
                sessions=two_sessions,
            )
            workout_a = await _make_workout(
                session,
                athlete_id=athlete.id,
                started_at=datetime(2026, 9, 15, tzinfo=UTC),
            )
            workout_b = await _make_workout(
                session,
                athlete_id=athlete.id,
                started_at=datetime(2026, 9, 15, 9, tzinfo=UTC),
            )
            await session.commit()

        async with database() as session:
            stored_plan = await WeeklyTrainingPlanRepository(session).get_by_id(
                plan_id=plan.id
            )
            assert stored_plan is not None
            session_ids = [
                uuid.UUID(raw["id"]) for raw in stored_plan.plan_jsonb["sessions"]
            ]
            service = LinkingService(session)
            first = await service.link_workout_to_session(
                athlete_id=athlete.id,
                plan_id=plan.id,
                plan_session_id=session_ids[0],
                workout_id=workout_a.id,
            )
            second = await service.link_workout_to_session(
                athlete_id=athlete.id,
                plan_id=plan.id,
                plan_session_id=session_ids[1],
                workout_id=workout_b.id,
            )
            assert first.workout_id != second.workout_id
            assert first.plan_session_id != second.plan_session_id
