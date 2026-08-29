"""Telegram rendering and deterministic callbacks for context onboarding."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from langchain_core.messages import HumanMessage

from app.bot import keyboards, messages
from app.bot.service import CoachBotApplicationService
from app.domain.enums import (
    CapabilityImportance,
    CapabilityKind,
    ExecutionOptionRole,
    GoalContextRole,
    OnboardingStatus,
    OnboardingStep,
    UserStatus,
)
from app.schemas.capabilities import (
    CapabilityOption,
    CapabilityReview,
    CapabilityReviewContext,
)
from app.schemas.common import TelegramIdentity
from app.schemas.onboarding_service import OnboardingResultKind, OnboardingServiceResult
from app.schemas.profile_settings import ProfileSettingsResult, ProfileSettingsStep
from app.schemas.weekly_plans import PlanDay, PlanSession, WeeklyPlan
from app.services.mobile_sync import (
    MobileSyncDisabledError,
    MobileSyncIdentityNotFoundError,
)
from app.services.onboarding import OnboardingApplicationError, OnboardingService
from app.services.weekly_planning.service import WeeklyPlanningResult


def _identity() -> TelegramIdentity:
    return TelegramIdentity(
        telegram_user_id=8172,
        telegram_username="runner",
        first_name="Ada",
        language_code="en",
    )


def _result(
    kind: OnboardingResultKind,
    step: OnboardingStep,
    *,
    answers: dict[str, str] | None = None,
    training_history_skipped: bool = False,
    capability_review: CapabilityReview | None = None,
) -> OnboardingServiceResult:
    return OnboardingServiceResult(
        kind=kind,
        user_id=uuid4(),
        user_status=UserStatus.ONBOARDING_IN_PROGRESS,
        onboarding_status=OnboardingStatus.ACTIVE,
        current_step=step,
        answers=answers or {},
        training_history_skipped=training_history_skipped,
        capability_review=capability_review,
    )


def _facade(
    onboarding: object,
    *,
    account_queries: object | None = None,
    planning: object | None = None,
    mobile_sync: object | None = None,
    mobile_sync_enabled: bool = False,
) -> CoachBotApplicationService:
    default_queries = SimpleNamespace(
        lifecycle=AsyncMock(
            return_value={
                "user_id": uuid4(),
                "status": UserStatus.ONBOARDING_COMPLETED,
            }
        )
    )
    return CoachBotApplicationService(
        onboarding=cast(OnboardingService, onboarding),
        profiles=SimpleNamespace(),
        account_queries=cast(object, account_queries or default_queries),
        accounts=SimpleNamespace(),
        planning=cast(object, planning),
        mobile_sync=cast(object, mobile_sync),
        mobile_sync_enabled=mobile_sync_enabled,
    )


@pytest.mark.asyncio
async def test_equipment_callback_uses_only_deterministic_onboarding_method() -> None:
    identity = _identity()
    onboarding = SimpleNamespace(
        choose_equipment=AsyncMock(
            return_value=_result(
                "health_limitations_intake",
                OnboardingStep.HEALTH_LIMITATIONS_INTAKE,
            )
        )
    )

    response = await _facade(onboarding).handle_callback(
        identity,
        "ob:v1:equipment:done",
    )

    onboarding.choose_equipment.assert_awaited_once_with(identity, "done")
    assert response.text == messages.HEALTH_LIMITATIONS_INTAKE
    assert response.keyboard == keyboards.health_limitations_keyboard()
    assert response.edit_existing is True


@pytest.mark.asyncio
async def test_stale_equipment_uuid_rerenders_current_durable_review() -> None:
    identity = _identity()
    review = CapabilityReview(
        contexts=(
            CapabilityReviewContext(
                code="cycling_road",
                display_name="Road cycling",
                role=GoalContextRole.TARGET,
            ),
        ),
        options=(
            CapabilityOption(
                id=uuid4(),
                code="stationary_bike",
                display_name="Stationary bike",
                kind=CapabilityKind.EQUIPMENT,
                importance=CapabilityImportance.REQUIRED,
                execution_roles=(ExecutionOptionRole.SUBSTITUTE,),
                target_context_codes=("cycling_road",),
                selected=True,
            ),
        ),
    )
    onboarding = SimpleNamespace(
        choose_equipment=AsyncMock(
            side_effect=OnboardingApplicationError("invalid_action")
        ),
        snapshot=AsyncMock(
            return_value=_result(
                "equipment_intake",
                OnboardingStep.EQUIPMENT_INTAKE,
                capability_review=review,
            )
        ),
    )

    response = await _facade(onboarding).handle_callback(
        identity,
        f"ob:v1:equipment:{uuid4()}",
    )

    onboarding.snapshot.assert_awaited_once_with(identity)
    assert "Stationary bike" in response.text
    assert response.edit_existing is True


@pytest.mark.asyncio
async def test_health_callback_uses_only_deterministic_onboarding_method() -> None:
    identity = _identity()
    onboarding = SimpleNamespace(
        choose_health_limitations=AsyncMock(
            return_value=_result(
                "onboarding_completed",
                OnboardingStep.HEALTH_LIMITATIONS_INTAKE,
            )
        )
    )

    response = await _facade(onboarding).handle_callback(
        identity,
        "ob:v1:health:none",
    )

    onboarding.choose_health_limitations.assert_awaited_once_with(identity, "none")
    assert response.text == messages.ONBOARDING_COMPLETED
    assert response.edit_existing is True


@pytest.mark.asyncio
async def test_history_skip_renders_suggestion_and_completed_controls() -> None:
    identity = _identity()
    onboarding = SimpleNamespace(
        skip_training_history=AsyncMock(
            return_value=_result(
                "onboarding_completed",
                OnboardingStep.TRAINING_HISTORY_IMPORT,
                training_history_skipped=True,
            )
        )
    )

    response = await _facade(onboarding).handle_callback(
        identity,
        "ob:v1:history:skip",
    )

    onboarding.skip_training_history.assert_awaited_once_with(identity)
    assert response.text == messages.TRAINING_HISTORY_SKIP_SUGGESTION
    assert response.user_keyboard == keyboards.completed_onboarding_keyboard()
    assert response.edit_existing is True


@pytest.mark.asyncio
async def test_history_phone_choice_pairs_and_completes_onboarding() -> None:
    identity = _identity()
    onboarding = SimpleNamespace(
        complete_training_history=AsyncMock(
            return_value=_result(
                "onboarding_completed",
                OnboardingStep.TRAINING_HISTORY_IMPORT,
            )
        )
    )
    mobile_sync = SimpleNamespace(
        issue_pairing_code=AsyncMock(
            return_value=SimpleNamespace(
                code="ABCD2345",
                expires_at=datetime(2026, 8, 21, 10, 30, tzinfo=UTC),
            )
        )
    )

    response = await _facade(
        onboarding,
        mobile_sync=mobile_sync,
        mobile_sync_enabled=True,
    ).handle_callback(identity, "ob:v1:history:phone")

    mobile_sync.issue_pairing_code.assert_awaited_once_with(identity)
    onboarding.complete_training_history.assert_awaited_once_with(identity)
    assert "<code>ABCD2345</code>" in response.text
    assert messages.ONBOARDING_COMPLETED in response.text
    assert response.edit_existing is True


@pytest.mark.asyncio
async def test_history_file_choice_prompts_for_a_document() -> None:
    response = await _facade(SimpleNamespace()).handle_callback(
        _identity(), "ob:v1:history:file"
    )

    assert response.text == messages.TRAINING_HISTORY_FILE_PROMPT
    assert response.keyboard == keyboards.training_history_import_keyboard()
    assert response.edit_existing is True


def _weekly_plan() -> WeeklyPlan:
    week_start = date(2026, 8, 24)
    days = []
    for offset in range(7):
        day = week_start + timedelta(days=offset)
        if offset == 0:
            days.append(
                PlanDay(
                    date=day,
                    sessions=(
                        PlanSession(
                            discipline="RUNNING",
                            objective="Easy aerobic run",
                            duration_minutes=45,
                            intensity="EASY",
                            structure="Easy throughout.",
                        ),
                    ),
                )
            )
        else:
            days.append(PlanDay(date=day, rest_note="Rest."))
    return WeeklyPlan(week_start=week_start, days=tuple(days))


@pytest.mark.asyncio
async def test_plan_next_week_uses_deterministic_route_and_switches_keyboard() -> None:
    identity = _identity()
    plan = _weekly_plan()
    planning = SimpleNamespace(
        generate_next_week=AsyncMock(
            return_value=WeeklyPlanningResult(kind="created", plan=plan)
        ),
        view_next_week=AsyncMock(),
        has_plan_for_next_week=AsyncMock(return_value=True),
    )

    response = await _facade(SimpleNamespace(), planning=planning).handle_agent_input(
        identity,
        HumanMessage(content=keyboards.LABELS["plan_next_week"]),
    )

    planning.generate_next_week.assert_awaited_once_with(identity)
    assert "Your plan for the week of 2026-08-24" in response.text
    assert response.user_keyboard == keyboards.completed_onboarding_keyboard(
        plan_available=True
    )


@pytest.mark.asyncio
async def test_connect_iphone_uses_deterministic_route_without_agent() -> None:
    identity = _identity()
    mobile_sync = SimpleNamespace(
        issue_pairing_code=AsyncMock(
            return_value=SimpleNamespace(
                code="ABCD2345",
                expires_at=datetime(2026, 8, 21, 10, 30, tzinfo=UTC),
            )
        ),
        revoke_device=AsyncMock(),
    )
    response = await _facade(
        SimpleNamespace(),
        mobile_sync=mobile_sync,
        mobile_sync_enabled=True,
    ).handle_agent_input(identity, HumanMessage(content="/connect_iphone"))

    mobile_sync.issue_pairing_code.assert_awaited_once_with(identity)
    assert "<code>ABCD2345</code>" in response.text
    assert "10:30 UTC" in response.text


@pytest.mark.asyncio
async def test_connect_iphone_is_unavailable_when_mobile_sync_is_disabled() -> None:
    mobile_sync = SimpleNamespace(
        issue_pairing_code=AsyncMock(),
        revoke_device=AsyncMock(),
    )

    response = await _facade(
        SimpleNamespace(), mobile_sync=mobile_sync, mobile_sync_enabled=False
    ).handle_agent_input(_identity(), HumanMessage(content="/connect_iphone"))

    mobile_sync.issue_pairing_code.assert_not_awaited()
    assert response.text == messages.MOBILE_SYNC_UNAVAILABLE


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (MobileSyncDisabledError("disabled"), messages.MOBILE_SYNC_UNAVAILABLE),
        (MobileSyncIdentityNotFoundError("missing"), messages.NOT_FOUND),
    ],
)
async def test_connect_iphone_translates_safe_mobile_service_errors(
    error: RuntimeError, expected: str
) -> None:
    mobile_sync = SimpleNamespace(
        issue_pairing_code=AsyncMock(side_effect=error),
        revoke_device=AsyncMock(),
    )

    response = await _facade(
        SimpleNamespace(), mobile_sync=mobile_sync, mobile_sync_enabled=True
    ).handle_agent_input(_identity(), HumanMessage(content="/connect_iphone"))

    assert response.text == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("revoked", "expected"),
    [
        (True, messages.IPHONE_DISCONNECTED),
        (False, messages.IPHONE_NOT_CONNECTED),
    ],
)
async def test_disconnect_iphone_renders_the_durable_revocation_result(
    revoked: bool, expected: str
) -> None:
    identity = _identity()
    mobile_sync = SimpleNamespace(
        issue_pairing_code=AsyncMock(),
        revoke_device=AsyncMock(return_value=revoked),
    )

    response = await _facade(
        SimpleNamespace(), mobile_sync=mobile_sync, mobile_sync_enabled=True
    ).handle_agent_input(identity, HumanMessage(content="/disconnect_iphone"))

    mobile_sync.revoke_device.assert_awaited_once_with(identity)
    assert response.text == expected


@pytest.mark.asyncio
async def test_profile_settings_callbacks_strip_the_transport_prefix() -> None:
    identity = _identity()
    onboarding = SimpleNamespace(
        open_profile_settings=AsyncMock(
            return_value=ProfileSettingsResult(step=ProfileSettingsStep.MENU)
        ),
        choose_profile_settings=AsyncMock(
            return_value=ProfileSettingsResult(step=ProfileSettingsStep.AVAILABILITY)
        ),
    )
    facade = _facade(onboarding)

    opened = await facade.handle_callback(identity, "ps:v1:open")
    availability = await facade.handle_callback(identity, "ps:v1:section:availability")

    onboarding.open_profile_settings.assert_awaited_once_with(identity)
    onboarding.choose_profile_settings.assert_awaited_once_with(
        identity, "section:availability"
    )
    assert opened.text == messages.PROFILE_SETTINGS_MENU
    assert "Current availability" in availability.text
    assert "Not set" in availability.text
    assert availability.text.endswith(messages.PROFILE_AVAILABILITY)
    assert availability.keyboard == keyboards.profile_settings_text_keyboard()


@pytest.mark.asyncio
async def test_profile_settings_done_closes_without_onboarding_cancel() -> None:
    identity = _identity()
    onboarding = SimpleNamespace(
        choose_profile_settings=AsyncMock(
            return_value=ProfileSettingsResult(
                step=ProfileSettingsStep.MENU,
                saved_field="__closed__",
            )
        )
    )

    response = await _facade(onboarding).handle_callback(identity, "ps:v1:done")

    onboarding.choose_profile_settings.assert_awaited_once_with(identity, "done")
    assert response.text == messages.PROFILE_SETTINGS_CLOSED
    assert response.keyboard is None
    assert response.edit_existing is True


@pytest.mark.asyncio
async def test_development_step_bypasses_global_agent_for_completed_accounts() -> None:
    identity = _identity()
    onboarding = SimpleNamespace(
        seed_development_step=AsyncMock(
            return_value=_result(
                "availability_intake", OnboardingStep.AVAILABILITY_INTAKE
            )
        )
    )
    response = await _facade(onboarding).handle_agent_input(
        identity, HumanMessage(content="/dev_step availability")
    )

    onboarding.seed_development_step.assert_awaited_once_with(identity, "availability")
    assert response.text == messages.AVAILABILITY_INTAKE


@pytest.mark.asyncio
async def test_development_import_history_shortcut_bypasses_global_agent() -> None:
    identity = _identity()
    onboarding = SimpleNamespace(
        seed_development_step=AsyncMock(
            return_value=_result(
                "training_history_import", OnboardingStep.TRAINING_HISTORY_IMPORT
            )
        )
    )
    response = await _facade(onboarding).handle_agent_input(
        identity, HumanMessage(content="/dev_import_history")
    )

    onboarding.seed_development_step.assert_awaited_once_with(identity, "history")
    assert response.text == messages.TRAINING_HISTORY_IMPORT
    assert response.keyboard == keyboards.training_history_import_keyboard()


@pytest.mark.asyncio
async def test_development_goal_equipment_reset_bypasses_global_agent() -> None:
    identity = _identity()
    onboarding = SimpleNamespace(
        reset_development_goal_and_equipment=AsyncMock(
            return_value=_result("goal_intake", OnboardingStep.GOAL_INTAKE)
        ),
        goal_sport_options=AsyncMock(return_value=("RUNNING", "TRIATHLON")),
    )
    response = await _facade(onboarding).handle_agent_input(
        identity, HumanMessage(content="/dev_reset_goal_equipment")
    )

    onboarding.reset_development_goal_and_equipment.assert_awaited_once_with(identity)
    assert response.text == messages.GOAL_INTAKE
    assert response.keyboard == keyboards.goal_sport_keyboard(("RUNNING", "TRIATHLON"))


@pytest.mark.asyncio
async def test_context_steps_render_the_correct_prompt_and_controls() -> None:
    facade = _facade(SimpleNamespace())
    identity = _identity()

    availability = await facade._render_onboarding(
        identity,
        _result("availability_intake", OnboardingStep.AVAILABILITY_INTAKE),
    )
    equipment = await facade._render_onboarding(
        identity,
        _result(
            "equipment_intake",
            OnboardingStep.EQUIPMENT_INTAKE,
            capability_review=CapabilityReview(
                contexts=(
                    CapabilityReviewContext(
                        code="running_road",
                        display_name="Road running",
                        role=GoalContextRole.TARGET,
                    ),
                ),
                options=(
                    CapabilityOption(
                        id=uuid4(),
                        code="running_shoes",
                        display_name="Running shoes",
                        kind=CapabilityKind.EQUIPMENT,
                        importance=CapabilityImportance.REQUIRED,
                        execution_roles=(ExecutionOptionRole.PREFERRED,),
                        target_context_codes=("running_road",),
                    ),
                ),
            ),
        ),
    )
    details = await facade._render_onboarding(
        identity,
        _result(
            "health_limitations_intake",
            OnboardingStep.HEALTH_LIMITATIONS_INTAKE,
        ),
    )
    health = await facade._render_onboarding(
        identity,
        _result(
            "health_limitations_intake",
            OnboardingStep.HEALTH_LIMITATIONS_INTAKE,
        ),
    )

    assert availability.text == messages.AVAILABILITY_INTAKE
    assert "swim at a pool" in availability.text
    assert "ride for up to two hours" in availability.text
    assert availability.keyboard == keyboards.profile_text_input_keyboard()
    assert "Running shoes" in equipment.text
    assert "Equipment" in equipment.text
    assert equipment.keyboard is not None
    assert details.text == messages.HEALTH_LIMITATIONS_INTAKE
    assert details.keyboard == keyboards.health_limitations_keyboard()
    assert health.text == messages.HEALTH_LIMITATIONS_INTAKE
    assert health.keyboard == keyboards.health_limitations_keyboard()


@pytest.mark.asyncio
async def test_goal_intake_renders_the_sport_menu_before_a_sport_is_chosen() -> None:
    onboarding = SimpleNamespace(
        goal_sport_options=AsyncMock(return_value=("RUNNING", "TRIATHLON")),
    )
    response = await _facade(onboarding)._render_onboarding(
        _identity(),
        _result("goal_intake", OnboardingStep.GOAL_INTAKE),
    )

    assert response.text == messages.GOAL_INTAKE
    assert response.keyboard == keyboards.goal_sport_keyboard(("RUNNING", "TRIATHLON"))


@pytest.mark.asyncio
async def test_goal_intake_renders_the_template_menu_once_a_sport_is_chosen() -> None:
    onboarding = SimpleNamespace(
        goal_template_options=AsyncMock(return_value=(("MARATHON", "Marathon"),)),
    )
    response = await _facade(onboarding)._render_onboarding(
        _identity(),
        _result(
            "goal_intake",
            OnboardingStep.GOAL_INTAKE,
            answers={"goal_sport": "RUNNING"},
        ),
    )

    onboarding.goal_template_options.assert_awaited_once_with("RUNNING")
    assert response.text == messages.GOAL_TEMPLATE_PROMPT
    assert response.keyboard == keyboards.goal_template_keyboard(
        (("MARATHON", "Marathon"),)
    )


@pytest.mark.asyncio
async def test_goal_confirmed_renders_the_supporting_goal_menu() -> None:
    onboarding = SimpleNamespace(
        supporting_goal_options=AsyncMock(
            return_value=(("STRENGTH_MAINTENANCE", "Maintain strength"),)
        ),
    )
    response = await _facade(onboarding)._render_onboarding(
        _identity(),
        _result("goal_confirmed", OnboardingStep.GOAL_CONFIRMED),
    )

    assert response.text == messages.GOAL_SUPPORT_PROMPT
    assert response.keyboard == keyboards.supporting_goal_keyboard(
        (("STRENGTH_MAINTENANCE", "Maintain strength"),)
    )


@pytest.mark.asyncio
async def test_context_validation_error_repeats_the_relevant_prompt() -> None:
    response = await _facade(SimpleNamespace())._render_onboarding(
        _identity(),
        _result(
            "context_validation_error",
            OnboardingStep.HEALTH_LIMITATIONS_INTAKE,
        ),
    )

    assert response.text == (
        f"{messages.CONTEXT_VALIDATION_ERROR}\n\n{messages.HEALTH_LIMITATIONS_INTAKE}"
    )
    assert response.keyboard == keyboards.health_limitations_keyboard()


@pytest.mark.asyncio
async def test_active_onboarding_raw_context_bypasses_global_agent_checkpoint() -> None:
    identity = _identity()
    onboarding = SimpleNamespace(
        handle_text=AsyncMock(
            return_value=_result(
                "availability_intake",
                OnboardingStep.AVAILABILITY_INTAKE,
            )
        )
    )
    account_queries = SimpleNamespace(
        lifecycle=AsyncMock(
            return_value={
                "user_id": uuid4(),
                "status": UserStatus.ONBOARDING_IN_PROGRESS,
            }
        )
    )
    facade = _facade(
        onboarding,
        account_queries=account_queries,
    )
    raw_limitation = "Private limitation: avoid hard downhill running."

    response = await facade.handle_agent_input(
        identity,
        HumanMessage(
            content=raw_limitation,
            additional_kwargs={"telegram_event_type": "text"},
        ),
    )

    onboarding.handle_text.assert_awaited_once_with(identity, raw_limitation)
    assert response.text == messages.AVAILABILITY_INTAKE
