"""Facade routing tests for the post-onboarding workout questionnaire."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, call
from uuid import UUID, uuid4

import pytest

from app.bot import keyboards, messages
from app.bot.service import CoachBotApplicationService
from app.domain.enums import (
    AppleHealthImportStatus,
    TrainingFileFormat,
    UserStatus,
    WorkoutFlowStep,
)
from app.schemas.common import TelegramIdentity
from app.services.onboarding import OnboardingService
from app.services.training_import.service import TrainingFileImportOutcome
from app.services.workout_feedback import (
    WorkoutFeedbackResult,
    WorkoutFeedbackService,
)


def _identity() -> TelegramIdentity:
    return TelegramIdentity(
        telegram_user_id=8172,
        telegram_username="runner",
        first_name="Ada",
        language_code="en",
    )


def _feedback_result(
    state: WorkoutFlowStep,
    *,
    user_id: UUID | None = None,
    activity_id: UUID | None = None,
    pending_heart_rate: int | None = None,
    pending_description: str | None = None,
) -> WorkoutFeedbackResult:
    return WorkoutFeedbackResult(
        user_id=user_id or uuid4(),
        activity_id=activity_id,
        state=state,
        pending_manual_average_heart_rate=pending_heart_rate,
        pending_discomfort_description=pending_description,
    )


def _facade(
    *,
    feedback: AsyncMock,
    training_import: object | None = None,
    resolved_user_id: UUID | None = None,
    feedback_enabled: bool = True,
) -> tuple[CoachBotApplicationService, AsyncMock, SimpleNamespace]:
    onboarding = AsyncMock(spec=OnboardingService)
    account_queries = SimpleNamespace(
        resolve_user_id=AsyncMock(return_value=resolved_user_id),
        lifecycle=AsyncMock(
            return_value={
                "user_id": resolved_user_id,
                "status": UserStatus.BASELINE_READY,
            }
        ),
        strava=AsyncMock(return_value=None),
    )
    service = CoachBotApplicationService(
        onboarding=onboarding,
        profiles=SimpleNamespace(),
        account_queries=account_queries,
        accounts=SimpleNamespace(),
        strava=SimpleNamespace(),
        apple_health=training_import,
        workout_feedback=feedback,
        workout_feedback_enabled=feedback_enabled,
    )
    return service, onboarding, account_queries


@pytest.mark.asyncio
async def test_add_workout_starts_durable_waiting_state_and_renders_request() -> None:
    feedback = AsyncMock(spec=WorkoutFeedbackService)
    feedback.begin_waiting_upload.return_value = _feedback_result(
        WorkoutFlowStep.WAITING_FOR_FILE
    )
    service, _, _ = _facade(feedback=feedback)
    identity = _identity()

    response = await service.add_workout(identity)

    feedback.begin_waiting_upload.assert_awaited_once_with(identity)
    assert response.text == messages.ADD_WORKOUT_REQUEST
    assert response.keyboard == keyboards.add_workout_keyboard()


@pytest.mark.asyncio
async def test_daily_tcx_import_starts_feedback_for_the_owned_activity() -> None:
    identity = _identity()
    user_id = uuid4()
    activity_id = uuid4()
    started_at = datetime(2026, 7, 29, 8, 30, tzinfo=UTC)
    outcome = TrainingFileImportOutcome(
        status=AppleHealthImportStatus.SUCCEEDED,
        file_format=TrainingFileFormat.TCX,
        activity_id=activity_id,
        activities_imported=1,
        sport="RUNNING",
        started_at=started_at,
        duration_seconds=3_600,
        distance_meters=10_000,
        average_heart_rate=None,
    )
    training_import = SimpleNamespace(
        process_upload=AsyncMock(return_value=outcome),
    )
    feedback = AsyncMock(spec=WorkoutFeedbackService)
    feedback.snapshot.return_value = None
    feedback.start_for_activity.return_value = _feedback_result(
        WorkoutFlowStep.HR_OFFER,
        user_id=user_id,
        activity_id=activity_id,
    )
    service, _, account_queries = _facade(
        feedback=feedback,
        training_import=training_import,
        resolved_user_id=user_id,
    )
    document = SimpleNamespace(file_id="telegram-file")
    download = AsyncMock()
    progress = AsyncMock()

    response = await service.handle_document(
        identity,
        document,
        download,
        progress,
    )

    training_import.process_upload.assert_awaited_once_with(
        identity=identity,
        document=document,
        download=download,
        progress=progress,
    )
    account_queries.resolve_user_id.assert_awaited_once_with(identity)
    feedback.start_for_activity.assert_awaited_once_with(
        user_id=user_id,
        activity_id=activity_id,
    )
    summary = messages.tcx_workout_result(
        sport="RUNNING",
        started_at=started_at,
        duration_seconds=3_600,
        distance_meters=10_000,
        average_heart_rate=None,
    )
    assert response.text == f"{summary}\n\n{messages.HEART_RATE_MISSING}"
    assert response.keyboard == keyboards.manual_heart_rate_offer_keyboard()


@pytest.mark.asyncio
async def test_document_is_rejected_while_other_feedback_is_active() -> None:
    identity = _identity()
    training_import = SimpleNamespace(process_upload=AsyncMock())
    feedback = AsyncMock(spec=WorkoutFeedbackService)
    feedback.snapshot.return_value = _feedback_result(WorkoutFlowStep.RPE)
    service, _, _ = _facade(
        feedback=feedback,
        training_import=training_import,
    )

    response = await service.handle_document(
        identity,
        SimpleNamespace(file_id="telegram-file"),
        AsyncMock(),
        AsyncMock(),
    )

    training_import.process_upload.assert_not_awaited()
    assert response.text == messages.validation_error("workout_flow_already_active")


@pytest.mark.asyncio
async def test_daily_apple_import_closes_waiting_flow_without_questions() -> None:
    identity = _identity()
    outcome = TrainingFileImportOutcome(
        status=AppleHealthImportStatus.SUCCEEDED,
        file_format=TrainingFileFormat.APPLE_HEALTH_ZIP,
        activities_imported=3,
    )
    training_import = SimpleNamespace(
        process_upload=AsyncMock(return_value=outcome),
    )
    feedback = AsyncMock(spec=WorkoutFeedbackService)
    feedback.snapshot.side_effect = [
        _feedback_result(WorkoutFlowStep.WAITING_FOR_FILE),
        _feedback_result(WorkoutFlowStep.WAITING_FOR_FILE),
    ]
    service, _, _ = _facade(
        feedback=feedback,
        training_import=training_import,
        resolved_user_id=uuid4(),
    )

    response = await service.handle_document(
        identity,
        SimpleNamespace(file_id="telegram-file"),
        AsyncMock(),
        AsyncMock(),
    )

    feedback.cancel.assert_awaited_once_with(identity)
    feedback.start_for_activity.assert_not_awaited()
    assert (
        messages.apple_health_file_result(
            activities_imported=3,
            activities_updated=0,
            activities_skipped=0,
        )
        in response.text
    )


@pytest.mark.asyncio
async def test_add_workout_remains_available_when_feedback_is_disabled() -> None:
    feedback = AsyncMock(spec=WorkoutFeedbackService)
    service, _, _ = _facade(feedback=feedback, feedback_enabled=False)

    response = await service.add_workout(_identity())

    feedback.begin_waiting_upload.assert_not_awaited()
    assert response.text == messages.ADD_WORKOUT_REQUEST


@pytest.mark.asyncio
async def test_add_workout_back_cancels_waiting_flow_before_home() -> None:
    feedback = AsyncMock(spec=WorkoutFeedbackService)
    feedback.back.return_value = _feedback_result(WorkoutFlowStep.CANCELLED)
    service, _, _ = _facade(
        feedback=feedback,
        resolved_user_id=uuid4(),
    )

    response = await service.handle_callback(
        _identity(),
        "wf:v1:back:waiting_for_file",
    )

    feedback.back.assert_awaited_once_with(
        _identity(),
        expected_state=WorkoutFlowStep.WAITING_FOR_FILE,
    )
    assert messages.WORKOUT_FEEDBACK_CANCELLED in response.text
    assert response.keyboard == keyboards.state_menu("ready")


@pytest.mark.asyncio
async def test_direct_daily_import_failure_returns_valid_home_actions() -> None:
    failed = TrainingFileImportOutcome(
        status=AppleHealthImportStatus.FAILED,
        file_format=TrainingFileFormat.UNKNOWN,
        safe_error_code="unsupported_training_file",
    )
    training_import = SimpleNamespace(
        process_upload=AsyncMock(return_value=failed),
    )
    feedback = AsyncMock(spec=WorkoutFeedbackService)
    feedback.snapshot.return_value = None
    service, _, _ = _facade(
        feedback=feedback,
        training_import=training_import,
        resolved_user_id=uuid4(),
    )

    response = await service.handle_document(
        _identity(),
        SimpleNamespace(file_id="unsupported"),
        AsyncMock(),
        AsyncMock(),
    )

    assert messages.validation_error("unsupported_training_file") in response.text
    assert response.keyboard == keyboards.state_menu("ready")
    assert response.keyboard != keyboards.add_workout_keyboard()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("callback_data", "operation_name", "expected_call", "next_state", "copy"),
    [
        (
            "wf:v1:hr:enter",
            "choose_manual_heart_rate",
            call(_identity(), enter=True),
            WorkoutFlowStep.HR_ENTRY,
            messages.HEART_RATE_ENTRY,
        ),
        (
            "wf:v1:rpe:very_hard",
            "select_rpe",
            call(_identity(), "very_hard"),
            WorkoutFlowStep.MOBILITY,
            messages.MOBILITY_QUESTION,
        ),
        (
            "wf:v1:mobility:yes",
            "select_mobility",
            call(_identity(), True),
            WorkoutFlowStep.DISCOMFORT,
            messages.DISCOMFORT_QUESTION,
        ),
        (
            "wf:v1:mobility:no",
            "select_mobility",
            call(_identity(), False),
            WorkoutFlowStep.DISCOMFORT,
            messages.DISCOMFORT_QUESTION,
        ),
        (
            "wf:v1:mobility:skip",
            "select_mobility",
            call(_identity(), None),
            WorkoutFlowStep.DISCOMFORT,
            messages.DISCOMFORT_QUESTION,
        ),
        (
            "wf:v1:discomfort:yes",
            "select_discomfort",
            call(_identity(), True),
            WorkoutFlowStep.BODY_AREA,
            messages.DISCOMFORT_AREA_QUESTION,
        ),
        (
            "wf:v1:area:other",
            "select_body_area",
            call(_identity(), "other"),
            WorkoutFlowStep.DESCRIPTION_ENTRY,
            messages.DISCOMFORT_DESCRIPTION_REQUEST,
        ),
        (
            "wf:v1:description:confirm",
            "confirm_discomfort_description",
            call(_identity()),
            WorkoutFlowStep.SEVERITY,
            messages.DISCOMFORT_SEVERITY_QUESTION,
        ),
        (
            "wf:v1:back:hr_confirm",
            "back",
            call(
                _identity(),
                expected_state=WorkoutFlowStep.HR_CONFIRM,
            ),
            WorkoutFlowStep.HR_ENTRY,
            messages.HEART_RATE_ENTRY,
        ),
        (
            "wf:v1:back:discomfort",
            "back",
            call(
                _identity(),
                expected_state=WorkoutFlowStep.DISCOMFORT,
            ),
            WorkoutFlowStep.MOBILITY,
            messages.MOBILITY_QUESTION,
        ),
        (
            "wf:v1:back:mobility",
            "back",
            call(
                _identity(),
                expected_state=WorkoutFlowStep.MOBILITY,
            ),
            WorkoutFlowStep.RPE,
            messages.RPE_QUESTION,
        ),
    ],
)
async def test_feedback_callbacks_route_deterministically_without_onboarding(
    callback_data: str,
    operation_name: str,
    expected_call: object,
    next_state: WorkoutFlowStep,
    copy: str,
) -> None:
    feedback = AsyncMock(spec=WorkoutFeedbackService)
    operation = getattr(feedback, operation_name)
    operation.return_value = _feedback_result(next_state)
    service, onboarding, _ = _facade(feedback=feedback)

    response = await service.handle_callback(_identity(), callback_data)

    assert operation.await_args == expected_call
    assert response.text == copy
    assert response.edit_existing is True
    onboarding.handle_text.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "current_state",
        "text",
        "operation_name",
        "next_result",
        "expected_copy",
    ),
    [
        (
            WorkoutFlowStep.HR_ENTRY,
            "148",
            "submit_manual_heart_rate",
            _feedback_result(
                WorkoutFlowStep.HR_CONFIRM,
                pending_heart_rate=148,
            ),
            messages.manual_heart_rate_confirmation(148),
        ),
        (
            WorkoutFlowStep.DESCRIPTION_ENTRY,
            "front of lower leg",
            "submit_discomfort_description",
            _feedback_result(
                WorkoutFlowStep.DESCRIPTION_CONFIRM,
                pending_description="front of lower leg",
            ),
            messages.discomfort_description_confirmation("front of lower leg"),
        ),
    ],
)
async def test_feedback_text_states_bypass_onboarding_parser(
    current_state: WorkoutFlowStep,
    text: str,
    operation_name: str,
    next_result: WorkoutFeedbackResult,
    expected_copy: str,
) -> None:
    identity = _identity()
    feedback = AsyncMock(spec=WorkoutFeedbackService)
    feedback.snapshot.return_value = _feedback_result(current_state)
    operation = getattr(feedback, operation_name)
    operation.return_value = next_result
    service, onboarding, _ = _facade(feedback=feedback)

    response = await service.handle_text(identity, text)

    feedback.snapshot.assert_awaited_once_with(identity)
    operation.assert_awaited_once_with(identity, text)
    onboarding.handle_text.assert_not_awaited()
    assert response.text == expected_copy
