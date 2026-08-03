"""Durable, ownership-scoped daily workout feedback use cases."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.db.base import Base
from app.db.models import (
    ActivityFeedback,
    ActivitySourceLink,
    BodyArea,
    RunningWorkoutDetails,
    User,
    Workout,
)
from app.domain.enums import (
    ActivitySource,
    Discipline,
    DiscomfortSeverity,
    RunningType,
    UserStatus,
    WorkoutFlowStep,
)
from app.repositories.users import UserRepository
from app.schemas.common import TelegramIdentity
from app.services.workout_feedback import (
    WorkoutFeedbackError,
    WorkoutFeedbackService,
)


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


def identity(telegram_id: int) -> TelegramIdentity:
    return TelegramIdentity(
        telegram_user_id=telegram_id,
        telegram_username=f"athlete_{telegram_id}",
        first_name="Athlete",
        language_code="en",
    )


async def create_user(
    factory: async_sessionmaker[AsyncSession],
    *,
    telegram_id: int,
    status: UserStatus = UserStatus.PROFILE_COMPLETED,
) -> uuid.UUID:
    async with factory.begin() as session:
        user, _ = await UserRepository(session).get_or_create(
            telegram_user_id=telegram_id,
            telegram_username=None,
            first_name="Athlete",
        )
        user.status = status
        await session.flush()
        return user.id


async def create_activity(
    factory: async_sessionmaker[AsyncSession],
    *,
    user_id: uuid.UUID,
    external_id: str,
    average_heart_rate: float | None = None,
) -> uuid.UUID:
    async with factory.begin() as session:
        workout = Workout(
            athlete_id=user_id,
            source=ActivitySource.TCX,
            external_id=external_id,
            discipline=Discipline.RUNNING,
            title="Synthetic run",
            started_at=datetime(2026, 7, 28, 6, tzinfo=UTC),
            duration_seconds=3600,
            running_details=RunningWorkoutDetails(
                running_type=RunningType.OUTDOOR,
                moving_duration_seconds=3600,
                average_heart_rate=average_heart_rate,
            ),
            source_links=[
                ActivitySourceLink(
                    user_id=user_id,
                    source=ActivitySource.TCX,
                    external_id=external_id,
                    raw_sport="Running",
                )
            ],
        )
        session.add(workout)
        await session.flush()
        return workout.id


@pytest.mark.asyncio
async def test_waiting_for_upload_is_completed_profile_only_and_resumes(
    database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = database
    complete_identity = identity(5101)
    incomplete_identity = identity(5102)
    complete_user_id = await create_user(factory, telegram_id=5101)
    await create_user(
        factory,
        telegram_id=5102,
        status=UserStatus.ONBOARDING_IN_PROGRESS,
    )
    service = WorkoutFeedbackService(factory)

    waiting = await service.begin_waiting_upload(complete_identity)
    resumed = await WorkoutFeedbackService(factory).snapshot(complete_identity)
    duplicate = await service.begin_waiting_upload(complete_identity)

    assert waiting.user_id == complete_user_id
    assert waiting.state is WorkoutFlowStep.WAITING_FOR_FILE
    assert resumed is not None
    assert resumed.state is WorkoutFlowStep.WAITING_FOR_FILE
    assert duplicate.state is WorkoutFlowStep.WAITING_FOR_FILE

    with pytest.raises(WorkoutFeedbackError) as failure:
        await service.begin_waiting_upload(incomplete_identity)
    assert failure.value.code == "profile_incomplete"


@pytest.mark.asyncio
async def test_manual_hr_requires_confirmation_and_survives_restart(
    database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = database
    athlete = identity(5110)
    user_id = await create_user(factory, telegram_id=5110)
    activity_id = await create_activity(
        factory,
        user_id=user_id,
        external_id="missing-hr",
    )
    service = WorkoutFeedbackService(factory)

    started = await service.start_for_activity(
        user_id=user_id,
        activity_id=activity_id,
    )
    entry = await service.choose_manual_heart_rate(athlete, enter=True)

    assert started.state is WorkoutFlowStep.HR_OFFER
    assert entry.state is WorkoutFlowStep.HR_ENTRY
    for invalid in ("148.0", "", 29, 251, True):
        with pytest.raises(WorkoutFeedbackError):
            await service.submit_manual_heart_rate(athlete, invalid)  # type: ignore[arg-type]

    confirmation = await service.submit_manual_heart_rate(athlete, "148")
    resumed = await WorkoutFeedbackService(factory).snapshot(athlete)

    assert confirmation.state is WorkoutFlowStep.HR_CONFIRM
    assert confirmation.feedback is None
    assert confirmation.average_heart_rate is None
    assert resumed is not None
    assert resumed.pending_manual_average_heart_rate == 148

    confirmed = await service.confirm_manual_heart_rate(athlete)
    replay = await service.confirm_manual_heart_rate(athlete)

    assert confirmed.state is WorkoutFlowStep.RPE
    assert confirmed.feedback is not None
    assert confirmed.feedback.manual_average_heart_rate == 148
    assert confirmed.average_heart_rate == 148
    assert replay == confirmed


@pytest.mark.asyncio
async def test_existing_hr_skips_manual_offer(
    database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = database
    reliable_user_id = await create_user(factory, telegram_id=5120)
    reliable_activity_id = await create_activity(
        factory,
        user_id=reliable_user_id,
        external_id="reliable-hr",
        average_heart_rate=151,
    )
    provider_user_id = await create_user(factory, telegram_id=5121)
    provider_activity_id = await create_activity(
        factory,
        user_id=provider_user_id,
        external_id="provider-hr",
        average_heart_rate=150,
    )
    service = WorkoutFeedbackService(factory)

    reliable = await service.start_for_activity(
        user_id=reliable_user_id,
        activity_id=reliable_activity_id,
    )
    assert reliable.state is WorkoutFlowStep.RPE

    provider = await service.start_for_activity(
        user_id=provider_user_id,
        activity_id=provider_activity_id,
    )
    assert provider.state is WorkoutFlowStep.RPE
    assert provider.average_heart_rate == 150


@pytest.mark.asyncio
async def test_manual_hr_change_and_skip_do_not_persist_pending_value(
    database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = database
    athlete = identity(5122)
    user_id = await create_user(factory, telegram_id=5122)
    activity_id = await create_activity(
        factory,
        user_id=user_id,
        external_id="changed-manual-hr",
    )
    service = WorkoutFeedbackService(factory)
    await service.start_for_activity(user_id=user_id, activity_id=activity_id)
    await service.choose_manual_heart_rate(athlete, enter=True)
    await service.submit_manual_heart_rate(athlete, 146)

    changed = await service.change_manual_heart_rate(athlete)
    await service.submit_manual_heart_rate(athlete, 149)
    skipped = await service.skip_manual_heart_rate(athlete)

    assert changed.state is WorkoutFlowStep.HR_ENTRY
    assert changed.pending_manual_average_heart_rate is None
    assert skipped.state is WorkoutFlowStep.RPE
    assert skipped.pending_manual_average_heart_rate is None
    assert skipped.feedback is not None
    assert skipped.feedback.manual_average_heart_rate is None
    assert skipped.average_heart_rate is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("label", "expected_value", "expected_label"),
    [
        ("Very easy", 2, "Very easy"),
        ("Easy", 4, "Easy"),
        ("Moderate", 6, "Moderate"),
        ("Hard", 8, "Hard"),
        ("Very hard", 10, "Very hard"),
    ],
)
async def test_rpe_mapping_is_deterministic(
    database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    label: str,
    expected_value: int,
    expected_label: str,
) -> None:
    _, factory = database
    telegram_id = 5200 + expected_value
    athlete = identity(telegram_id)
    user_id = await create_user(factory, telegram_id=telegram_id)
    activity_id = await create_activity(
        factory,
        user_id=user_id,
        external_id=f"rpe-{expected_value}",
        average_heart_rate=140,
    )
    service = WorkoutFeedbackService(factory)
    await service.start_for_activity(user_id=user_id, activity_id=activity_id)

    selected = await service.select_rpe(athlete, label)
    replay = await service.select_rpe(athlete, label)

    assert selected.state is WorkoutFlowStep.MOBILITY
    assert selected.feedback is not None
    assert selected.feedback.reported_rpe == expected_value
    assert selected.feedback.reported_rpe_label == expected_label
    assert replay == selected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("telegram_id", "operation_name", "expected_mobility"),
    [
        (5301, "report_mobility", True),
        (5302, "report_no_mobility", False),
        (5303, "skip_mobility", None),
    ],
)
async def test_mobility_yes_no_and_skip_preserve_tristate(
    database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    telegram_id: int,
    operation_name: str,
    expected_mobility: bool | None,
) -> None:
    _, factory = database
    athlete = identity(telegram_id)
    user_id = await create_user(factory, telegram_id=telegram_id)
    activity_id = await create_activity(
        factory,
        user_id=user_id,
        external_id=f"mobility-{telegram_id}",
        average_heart_rate=140,
    )
    service = WorkoutFeedbackService(factory)
    await service.start_for_activity(user_id=user_id, activity_id=activity_id)
    rpe = await service.skip_rpe(athlete)

    operation = getattr(service, operation_name)
    selected = await operation(athlete)
    replay = await operation(athlete)

    assert rpe.state is WorkoutFlowStep.MOBILITY
    assert selected.state is WorkoutFlowStep.DISCOMFORT
    assert selected.feedback is not None
    assert selected.feedback.mobility_done is expected_mobility
    assert replay == selected


@pytest.mark.asyncio
async def test_mobility_back_and_stale_back_follow_persisted_state(
    database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = database
    athlete = identity(5304)
    user_id = await create_user(factory, telegram_id=5304)
    activity_id = await create_activity(
        factory,
        user_id=user_id,
        external_id="mobility-back",
        average_heart_rate=140,
    )
    service = WorkoutFeedbackService(factory)
    await service.start_for_activity(user_id=user_id, activity_id=activity_id)
    await service.skip_rpe(athlete)
    await service.report_mobility(athlete)

    backed = await service.back(
        athlete,
        expected_state=WorkoutFlowStep.DISCOMFORT,
    )
    stale_back = await service.back(
        athlete,
        expected_state=WorkoutFlowStep.DISCOMFORT,
    )
    rpe = await service.back(
        athlete,
        expected_state=WorkoutFlowStep.MOBILITY,
    )

    assert backed.state is WorkoutFlowStep.MOBILITY
    assert backed.feedback is not None
    assert backed.feedback.mobility_done is True
    assert stale_back == backed
    assert rpe.state is WorkoutFlowStep.RPE


@pytest.mark.asyncio
async def test_discomfort_no_completes_with_explicit_false(
    database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = database
    athlete = identity(5130)
    user_id = await create_user(factory, telegram_id=5130)
    activity_id = await create_activity(
        factory,
        user_id=user_id,
        external_id="no-discomfort",
        average_heart_rate=140,
    )
    service = WorkoutFeedbackService(factory)
    await service.start_for_activity(user_id=user_id, activity_id=activity_id)
    await service.skip_rpe(athlete)
    mobility = await service.report_no_mobility(athlete)

    completed = await service.select_discomfort(athlete, False)

    assert mobility.state is WorkoutFlowStep.DISCOMFORT
    assert completed.state is WorkoutFlowStep.COMPLETE
    assert completed.completed is True
    assert completed.feedback is not None
    assert completed.feedback.mobility_done is False
    assert completed.feedback.reported_discomfort is False
    assert completed.feedback.discomfort_body_area is None


@pytest.mark.asyncio
async def test_optional_discomfort_and_detail_skip_paths_remain_unknown(
    database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = database
    skip_identity = identity(5131)
    skip_user_id = await create_user(factory, telegram_id=5131)
    skip_activity_id = await create_activity(
        factory,
        user_id=skip_user_id,
        external_id="skip-discomfort",
        average_heart_rate=140,
    )
    detail_identity = identity(5132)
    detail_user_id = await create_user(factory, telegram_id=5132)
    detail_activity_id = await create_activity(
        factory,
        user_id=detail_user_id,
        external_id="skip-details",
        average_heart_rate=140,
    )
    service = WorkoutFeedbackService(factory)

    await service.start_for_activity(
        user_id=skip_user_id,
        activity_id=skip_activity_id,
    )
    await service.skip_rpe(skip_identity)
    await service.skip_mobility(skip_identity)
    skipped = await service.skip_discomfort(skip_identity)

    assert skipped.state is WorkoutFlowStep.COMPLETE
    assert skipped.feedback is not None
    assert skipped.feedback.mobility_done is None
    assert skipped.feedback.reported_discomfort is None

    started = await service.start_for_activity(
        user_id=detail_user_id,
        activity_id=detail_activity_id,
    )
    await service.skip_rpe(detail_identity)
    await service.report_mobility(detail_identity)
    await service.report_discomfort(detail_identity)
    severity = await service.skip_body_area(detail_identity)
    completed = await service.skip_severity(detail_identity)

    assert started.activity_id == detail_activity_id
    assert severity.state is WorkoutFlowStep.SEVERITY
    assert completed.feedback is not None
    assert completed.feedback.mobility_done is True
    assert completed.feedback.reported_discomfort is True
    assert completed.feedback.discomfort_body_area is None
    assert completed.feedback.discomfort_severity is None


@pytest.mark.asyncio
async def test_predefined_discomfort_area_and_severity_are_persisted(
    database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = database
    athlete = identity(5140)
    user_id = await create_user(factory, telegram_id=5140)
    activity_id = await create_activity(
        factory,
        user_id=user_id,
        external_id="knee-discomfort",
        average_heart_rate=140,
    )
    service = WorkoutFeedbackService(factory)
    await service.start_for_activity(user_id=user_id, activity_id=activity_id)
    await service.select_rpe(athlete, "Moderate")
    await service.report_mobility(athlete)
    body_area = await service.select_discomfort(athlete, True)
    severity = await service.select_body_area(athlete, "Knee")
    completed = await service.select_severity(athlete, "Moderate")

    assert body_area.state is WorkoutFlowStep.BODY_AREA
    assert severity.state is WorkoutFlowStep.SEVERITY
    assert completed.state is WorkoutFlowStep.COMPLETE
    assert completed.feedback is not None
    assert completed.feedback.reported_discomfort is True
    assert completed.feedback.discomfort_body_area is BodyArea.KNEE
    assert completed.feedback.discomfort_severity is DiscomfortSeverity.MODERATE


@pytest.mark.asyncio
async def test_other_description_is_staged_confirmed_and_resumable(
    database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = database
    athlete = identity(5150)
    user_id = await create_user(factory, telegram_id=5150)
    activity_id = await create_activity(
        factory,
        user_id=user_id,
        external_id="other-discomfort",
        average_heart_rate=140,
    )
    service = WorkoutFeedbackService(factory)
    await service.start_for_activity(user_id=user_id, activity_id=activity_id)
    await service.skip_rpe(athlete)
    await service.report_no_mobility(athlete)
    await service.select_discomfort(athlete, True)
    entry = await service.select_body_area(athlete, BodyArea.OTHER)
    staged = await service.submit_discomfort_description(
        athlete,
        "  Tender spot near my left elbow  ",
    )
    resumed = await WorkoutFeedbackService(factory).snapshot(athlete)

    assert entry.state is WorkoutFlowStep.DESCRIPTION_ENTRY
    assert staged.state is WorkoutFlowStep.DESCRIPTION_CONFIRM
    assert staged.feedback is not None
    assert staged.feedback.discomfort_description is None
    assert staged.pending_discomfort_description == "Tender spot near my left elbow"
    assert resumed is not None
    assert (
        resumed.pending_discomfort_description == staged.pending_discomfort_description
    )

    changed = await service.change_discomfort_description(athlete)
    assert changed.state is WorkoutFlowStep.DESCRIPTION_ENTRY
    assert changed.pending_discomfort_description is None
    await service.submit_discomfort_description(athlete, "Left elbow")
    confirmed = await service.confirm_discomfort_description(athlete)
    completed = await service.select_severity(athlete, None)

    assert confirmed.state is WorkoutFlowStep.SEVERITY
    assert confirmed.feedback is not None
    assert confirmed.feedback.discomfort_description == "Left elbow"
    assert completed.state is WorkoutFlowStep.COMPLETE
    assert completed.feedback is not None
    assert completed.feedback.discomfort_severity is None


@pytest.mark.asyncio
async def test_back_cancel_and_new_flow_do_not_depend_on_memory(
    database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = database
    athlete = identity(5160)
    user_id = await create_user(factory, telegram_id=5160)
    activity_id = await create_activity(
        factory,
        user_id=user_id,
        external_id="cancelled-feedback",
    )
    service = WorkoutFeedbackService(factory)
    await service.start_for_activity(user_id=user_id, activity_id=activity_id)
    await service.choose_manual_heart_rate(athlete, enter=True)
    await service.submit_manual_heart_rate(athlete, 149)

    backed = await service.back(
        athlete,
        expected_state=WorkoutFlowStep.HR_CONFIRM,
    )
    replayed_back = await service.back(
        athlete,
        expected_state=WorkoutFlowStep.HR_CONFIRM,
    )
    cancelled = await WorkoutFeedbackService(factory).cancel(athlete)
    restarted = await WorkoutFeedbackService(factory).begin_waiting_upload(athlete)
    exited_waiting = await WorkoutFeedbackService(factory).back(
        athlete,
        expected_state=WorkoutFlowStep.WAITING_FOR_FILE,
    )

    assert backed.state is WorkoutFlowStep.HR_ENTRY
    assert replayed_back.state is WorkoutFlowStep.HR_ENTRY
    assert cancelled.state is WorkoutFlowStep.CANCELLED
    assert cancelled.completed is True
    assert restarted.state is WorkoutFlowStep.WAITING_FOR_FILE
    assert restarted.activity_id is None
    assert restarted.pending_manual_average_heart_rate is None
    assert exited_waiting.state is WorkoutFlowStep.CANCELLED


@pytest.mark.asyncio
async def test_activity_and_feedback_are_strictly_user_owned(
    database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = database
    owner = identity(5170)
    other = identity(5171)
    owner_id = await create_user(factory, telegram_id=5170)
    other_id = await create_user(factory, telegram_id=5171)
    activity_id = await create_activity(
        factory,
        user_id=owner_id,
        external_id="owned-activity",
    )
    service = WorkoutFeedbackService(factory)

    with pytest.raises(WorkoutFeedbackError) as failure:
        await service.start_for_activity(
            user_id=other_id,
            activity_id=activity_id,
        )
    assert failure.value.code == "activity_not_found"

    await service.start_for_activity(user_id=owner_id, activity_id=activity_id)
    await service.choose_manual_heart_rate(owner, enter=True)
    await service.submit_manual_heart_rate(owner, 147)
    await service.confirm_manual_heart_rate(owner)

    assert await service.snapshot(other) is None
    async with factory() as session:
        feedback_count = await session.scalar(
            select(func.count(ActivityFeedback.id)).where(
                ActivityFeedback.user_id == owner_id,
                ActivityFeedback.workout_id == activity_id,
            )
        )
        other_feedback_count = await session.scalar(
            select(func.count(ActivityFeedback.id)).where(
                ActivityFeedback.user_id == other_id,
            )
        )
        users = await session.scalar(select(func.count(User.id)))

    assert feedback_count == 1
    assert other_feedback_count == 0
    assert users == 2
