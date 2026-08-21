"""Immutable, catalog-scoped workout baseline assessment tests."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

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
    Workout,
    WorkoutHeartRateObservation,
)
from app.domain.enums import (
    ActivitySource,
    CatalogItemSource,
    CatalogItemStatus,
    Discipline,
    FitnessBaselineSource,
    GoalContextRole,
    GoalTemplateKind,
    HeartRateTemporalQuality,
    RunningType,
    SwimmingEnvironment,
)
from app.repositories.activities import TrainingActivityRepository
from app.repositories.users import UserRepository
from app.schemas.fitness import FitnessWorkoutEvidence, HeartRateEvidence
from app.schemas.workouts import RunningWorkoutDetailsData, WorkoutCreate
from app.services.fitness import BaselineAssessmentService
from app.services.fitness.calculator import calculate_baseline_window

NOW = datetime(2026, 8, 17, 12, tzinfo=UTC)


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
    )


async def _user_with_goal_scope(session: AsyncSession) -> uuid.UUID:
    user, _ = await UserRepository(session).get_or_create(
        telegram_user_id=987_654,
        telegram_username="baseline_athlete",
        first_name="Baseline",
    )
    primary = GoalTemplate(
        id=uuid.uuid4(),
        code="BASELINE_PRIMARY",
        kind=GoalTemplateKind.PRIMARY,
        display_name="Baseline primary",
        description="Synthetic target goal",
        source=CatalogItemSource.SEEDED,
        status=CatalogItemStatus.ACTIVE,
        definition_version=1,
    )
    supporting = GoalTemplate(
        id=uuid.uuid4(),
        code="BASELINE_SUPPORTING",
        kind=GoalTemplateKind.SUPPORTING,
        display_name="Baseline supporting",
        description="Synthetic supporting goal",
        source=CatalogItemSource.SEEDED,
        status=CatalogItemStatus.ACTIVE,
        definition_version=1,
    )
    session.add_all((primary, supporting))
    await session.flush()

    await _add_context(
        session,
        template=primary,
        discipline=Discipline.RUNNING,
        role=GoalContextRole.TARGET,
        suffix="run_target",
    )
    # This primary SUPPORTING context must not be folded into baseline scope.
    await _add_context(
        session,
        template=primary,
        discipline=Discipline.STRENGTH,
        role=GoalContextRole.SUPPORTING,
        suffix="strength_supporting",
    )
    await _add_context(
        session,
        template=supporting,
        discipline=Discipline.CYCLING,
        role=GoalContextRole.SUPPORTING,
        suffix="cycling_supporting",
    )
    # This supporting TARGET context must not be folded into baseline scope.
    await _add_context(
        session,
        template=supporting,
        discipline=Discipline.OTHER,
        role=GoalContextRole.TARGET,
        suffix="other_target",
    )
    session.add(
        TrainingGoal(
            user_id=user.id,
            main_goal="Synthetic goal",
            target_outcome="Finish",
            secondary_priority="Support",
            original_description="Synthetic goal",
            goal_template_id=primary.id,
            supporting_goal_template_id=supporting.id,
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
    suffix: str,
) -> None:
    context = TrainingContext(
        id=uuid.uuid4(),
        code=f"baseline_{suffix}",
        display_name=suffix,
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
            priority=0,
        )
    )
    await session.flush()


async def _add_running_workout(
    session: AsyncSession,
    *,
    athlete_id: uuid.UUID,
    started_at: datetime,
    distance_meters: float,
    source: ActivitySource = ActivitySource.MANUAL,
) -> Workout:
    return await TrainingActivityRepository(session).create_manual(
        WorkoutCreate(
            athlete_id=athlete_id,
            discipline=Discipline.RUNNING,
            started_at=started_at,
            duration_seconds=1800,
            source=source,
            title="Run",
            details=RunningWorkoutDetailsData(
                running_type=RunningType.OUTDOOR,
                distance_meters=distance_meters,
                moving_duration_seconds=1750,
                calories_kcal=360,
                elevation_gain_meters=80,
                average_cadence_spm=170,
            ),
        )
    )


@pytest.mark.asyncio
async def test_creates_only_goal_scoped_baselines_and_uses_latest_history_window(
    database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = database
    async with factory.begin() as session:
        user_id = await _user_with_goal_scope(session)
        old_run = await _add_running_workout(
            session,
            athlete_id=user_id,
            started_at=NOW - timedelta(days=45),
            distance_meters=3000,
        )
        latest_run = await _add_running_workout(
            session,
            athlete_id=user_id,
            started_at=NOW - timedelta(days=30),
            distance_meters=5000,
        )
        session.add(
            WorkoutHeartRateObservation(
                user_id=user_id,
                workout_id=latest_run.id,
                source=ActivitySource.MANUAL,
                source_record_key="hr-exact",
                started_at=latest_run.started_at,
                ended_at=latest_run.started_at,
                beats_per_minute=150,
                temporal_quality=HeartRateTemporalQuality.EXACT_SAMPLE,
            )
        )
        other_user, _ = await UserRepository(session).get_or_create(
            telegram_user_id=987_655,
            telegram_username="other_athlete",
            first_name="Other",
        )
        await _add_running_workout(
            session,
            athlete_id=other_user.id,
            started_at=NOW - timedelta(days=1),
            distance_meters=20_000,
        )

        created = await BaselineAssessmentService(
            settings=_settings()
        ).create_missing_baselines_for_goal_disciplines_in_session(
            session,
            athlete_id=user_id,
            calculated_at=NOW,
        )

        assert created == (Discipline.RUNNING,)
        baseline = await session.scalar(
            select(AthleteBaselineAssessment).where(
                AthleteBaselineAssessment.athlete_id == user_id,
                AthleteBaselineAssessment.discipline == Discipline.RUNNING,
            )
        )
        assert baseline is not None
        assert baseline.source is FitnessBaselineSource.IMPORTED_WORKOUT_WINDOW
        assert baseline.session_count == 1
        assert baseline.known_distance_meters == 5000
        assert baseline.analysis_started_at.replace(tzinfo=UTC) == NOW - timedelta(
            days=44
        )
        assert baseline.analysis_ended_at.replace(tzinfo=UTC) == NOW - timedelta(
            days=30
        )
        assert baseline.reliable_hr_sample_count == 1
        assert baseline.discipline_metrics_jsonb["running_type_counts"] == {
            "OUTDOOR": 1
        }
        assert (
            await session.scalar(
                select(AthleteBaselineAssessment).where(
                    AthleteBaselineAssessment.athlete_id == user_id,
                    AthleteBaselineAssessment.discipline == Discipline.CYCLING,
                )
            )
            is None
        )
        assert old_run.id != latest_run.id


@pytest.mark.asyncio
async def test_existing_baseline_is_never_recalculated(
    database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = database
    async with factory.begin() as session:
        user_id = await _user_with_goal_scope(session)
        run = await _add_running_workout(
            session,
            athlete_id=user_id,
            started_at=NOW - timedelta(days=1),
            distance_meters=5000,
        )
        service = BaselineAssessmentService(settings=_settings())
        assert await service.create_missing_baselines_for_goal_disciplines_in_session(
            session,
            athlete_id=user_id,
            calculated_at=NOW,
        ) == (Discipline.RUNNING,)
        baseline = await session.scalar(
            select(AthleteBaselineAssessment).where(
                AthleteBaselineAssessment.athlete_id == user_id,
                AthleteBaselineAssessment.discipline == Discipline.RUNNING,
            )
        )
        assert baseline is not None
        original_id = baseline.id
        assert run.running_details is not None
        run.running_details.distance_meters = 6000
        run.fitness_input_updated_at = NOW + timedelta(minutes=1)

        assert (
            await service.create_missing_baselines_for_goal_disciplines_in_session(
                session,
                athlete_id=user_id,
                calculated_at=NOW + timedelta(minutes=1),
            )
            == ()
        )
        persisted = await session.scalar(
            select(AthleteBaselineAssessment).where(
                AthleteBaselineAssessment.id == original_id
            )
        )
        assert persisted is not None
        assert persisted.known_distance_meters == 5000


def test_calculator_excludes_probable_duplicates_and_handles_hr_quality() -> None:
    started_at = NOW - timedelta(days=1)
    apple = FitnessWorkoutEvidence(
        workout_id=uuid.uuid4(),
        discipline=Discipline.RUNNING,
        source=ActivitySource.APPLE_HEALTH,
        started_at=started_at,
        duration_seconds=1800,
        fitness_input_updated_at=NOW,
        distance_meters=5000,
        moving_duration_seconds=None,
        subtype="OUTDOOR",
        heart_rate_observations=(
            HeartRateEvidence(
                started_at=started_at,
                ended_at=started_at + timedelta(minutes=10),
                beats_per_minute=140,
                temporal_quality=HeartRateTemporalQuality.COARSE_INTERVAL,
            ),
        ),
    )
    tcx = FitnessWorkoutEvidence(
        workout_id=uuid.uuid4(),
        discipline=Discipline.RUNNING,
        source=ActivitySource.TCX,
        started_at=started_at + timedelta(seconds=30),
        duration_seconds=1801,
        fitness_input_updated_at=NOW,
        distance_meters=5010,
        moving_duration_seconds=1750,
        subtype="OUTDOOR",
        heart_rate_observations=(
            HeartRateEvidence(
                started_at=started_at,
                ended_at=started_at,
                beats_per_minute=151,
                temporal_quality=HeartRateTemporalQuality.EXACT_SAMPLE,
            ),
        ),
    )

    result = calculate_baseline_window(
        discipline=Discipline.RUNNING,
        workouts=(apple, tcx),
        window_started_at=NOW - timedelta(days=14),
        window_ended_at=NOW,
        calculated_at=NOW,
    )

    assert result is not None
    assert result.session_count == 1
    assert result.reliable_hr_sample_count == 1
    assert (
        result.evidence_summary_jsonb["workouts_excluded_as_possible_duplicates"] == 1
    )
    assert "POSSIBLE_CROSS_SOURCE_DUPLICATES_EXCLUDED" in result.quality_flags_jsonb
    assert "MISSING_MOVING_DURATION" not in result.quality_flags_jsonb

    coarse_only = calculate_baseline_window(
        discipline=Discipline.RUNNING,
        workouts=(apple,),
        window_started_at=NOW - timedelta(days=14),
        window_ended_at=NOW,
        calculated_at=NOW,
    )
    assert coarse_only is not None
    assert coarse_only.reliable_hr_sample_count == 0
    assert "COARSE_HR_ONLY" in coarse_only.quality_flags_jsonb
    assert (
        calculate_baseline_window(
            discipline=Discipline.RUNNING,
            workouts=(),
            window_started_at=NOW - timedelta(days=14),
            window_ended_at=NOW,
            calculated_at=NOW,
        )
        is None
    )


@pytest.mark.parametrize(
    ("discipline", "workout", "expected_key"),
    [
        pytest.param(
            Discipline.CYCLING,
            FitnessWorkoutEvidence(
                workout_id=uuid.uuid4(),
                discipline=Discipline.CYCLING,
                source=ActivitySource.TCX,
                started_at=NOW - timedelta(days=1),
                duration_seconds=3600,
                fitness_input_updated_at=NOW,
                distance_meters=24_000,
                moving_duration_seconds=3500,
                subtype="ROAD",
                elevation_gain_meters=300,
                average_cadence=85,
            ),
            "cycling_type_counts",
            id="cycling",
        ),
        pytest.param(
            Discipline.SWIMMING,
            FitnessWorkoutEvidence(
                workout_id=uuid.uuid4(),
                discipline=Discipline.SWIMMING,
                source=ActivitySource.APPLE_HEALTH,
                started_at=NOW - timedelta(days=1),
                duration_seconds=1800,
                fitness_input_updated_at=NOW,
                distance_meters=1500,
                swimming_environment=SwimmingEnvironment.POOL,
            ),
            "pool",
            id="swimming",
        ),
        pytest.param(
            Discipline.STRENGTH,
            FitnessWorkoutEvidence(
                workout_id=uuid.uuid4(),
                discipline=Discipline.STRENGTH,
                source=ActivitySource.APPLE_HEALTH,
                started_at=NOW - timedelta(days=1),
                duration_seconds=2700,
                fitness_input_updated_at=NOW,
                subtype="GYM",
                structured_exercise_count=4,
            ),
            "structured_exercise_count",
            id="strength",
        ),
        pytest.param(
            Discipline.OTHER,
            FitnessWorkoutEvidence(
                workout_id=uuid.uuid4(),
                discipline=Discipline.OTHER,
                source=ActivitySource.APPLE_HEALTH,
                started_at=NOW - timedelta(days=1),
                duration_seconds=2700,
                fitness_input_updated_at=NOW,
            ),
            "modality_specified",
            id="other",
        ),
    ],
)
def test_calculator_keeps_only_supported_discipline_metrics(
    discipline: Discipline,
    workout: FitnessWorkoutEvidence,
    expected_key: str,
) -> None:
    result = calculate_baseline_window(
        discipline=discipline,
        workouts=(workout,),
        window_started_at=NOW - timedelta(days=14),
        window_ended_at=NOW,
        calculated_at=NOW,
    )

    assert result is not None
    assert expected_key in result.discipline_metrics_jsonb
    if discipline is Discipline.OTHER:
        assert "OTHER_DISCIPLINE_UNSPECIFIED" in result.quality_flags_jsonb
    if discipline is Discipline.SWIMMING:
        assert result.discipline_metrics_jsonb["pool"] == {
            "session_count": 1,
            "duration_seconds": 1800,
            "distance_meters": 1500,
        }
