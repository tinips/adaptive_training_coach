"""Telegram application facade for onboarding, profiles, and file imports."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path
from typing import Protocol, cast
from uuid import UUID

from langchain_core.messages import HumanMessage
from telegram import ReplyKeyboardMarkup

from app.bot import keyboards, messages
from app.bot.rendering import TelegramResponse
from app.domain.enums import (
    AppleHealthImportStatus,
    OnboardingStatus,
    OnboardingStep,
    TrainingImportContext,
    UserStatus,
)
from app.schemas.common import TelegramIdentity
from app.schemas.onboarding_service import OnboardingServiceResult
from app.schemas.profile_settings import ProfileSettingsResult
from app.schemas.training_import import TelegramDocumentUpload
from app.services.accounts import AccountQueryService, AccountService
from app.services.onboarding import OnboardingApplicationError, OnboardingService
from app.services.profiles import ProfileService
from app.services.training_import import TrainingFileImportOutcome
from app.workflows.telegram_orchestrator.workspace import (
    TelegramAgentContext,
    TelegramAgentWorkspace,
    TelegramEventType,
)


class TrainingImportBotPort(Protocol):
    async def process_upload(
        self,
        *,
        identity: TelegramIdentity,
        document: TelegramDocumentUpload,
        download: Callable[[Path], Awaitable[None]],
        progress: Callable[[str], Awaitable[None]],
    ) -> TrainingFileImportOutcome: ...

    async def latest_outcome(
        self, *, user_id: UUID
    ) -> TrainingFileImportOutcome | None: ...
    async def cancel_active(self, *, user_id: UUID) -> None: ...


class CoachBotApplicationService:
    def __init__(
        self,
        *,
        onboarding: OnboardingService,
        profiles: ProfileService,
        account_queries: AccountQueryService,
        accounts: AccountService,
        apple_health: TrainingImportBotPort | None = None,
        apple_health_enabled: bool = True,
        tcx_enabled: bool = True,
        agent_workspace: TelegramAgentWorkspace | None = None,
    ) -> None:
        self._onboarding = onboarding
        self._profiles = profiles
        self._account_queries = account_queries
        self._accounts = accounts
        self._apple_health = apple_health
        self._apple_health_enabled = apple_health_enabled
        self._tcx_enabled = tcx_enabled
        self._agent_workspace = agent_workspace

    async def handle_agent_input(
        self, identity: TelegramIdentity, message: HumanMessage
    ) -> TelegramResponse:
        before = await self._account_queries.lifecycle(identity)
        response = await self._handle_agent_input(identity, message)
        after = await self._account_queries.lifecycle(identity)
        lifecycle_keyboard = self._keyboard_for_lifecycle(after)
        first_label = lifecycle_keyboard.keyboard[0][0].text
        lifecycle_changed = self._lifecycle_category(
            before
        ) != self._lifecycle_category(after)
        requires_new_message = (
            response.user_keyboard is not None
            or response.clear_agent_thread
            or first_label == keyboards.LABELS["start"]
        )
        return replace(
            response,
            user_keyboard=lifecycle_keyboard,
            edit_existing=(False if requires_new_message else response.edit_existing),
            refresh_user_keyboard=(
                lifecycle_changed or after is None or response.user_keyboard is not None
            ),
        )

    async def _handle_agent_input(
        self, identity: TelegramIdentity, message: HumanMessage
    ) -> TelegramResponse:
        event_type: TelegramEventType = (
            "callback"
            if message.additional_kwargs.get("telegram_event_type") == "callback"
            else "text"
        )
        if not isinstance(message.content, str) or not message.content.strip():
            return TelegramResponse(messages.GENERIC_ERROR)
        content = message.content
        if event_type == "text":
            content = {
                keyboards.LABELS["start"]: "/start",
                keyboards.LABELS["resume_menu"]: "/start",
                keyboards.LABELS["profile"]: "/profile",
                keyboards.LABELS["delete"]: "/delete_me",
            }.get(content, content)
        if event_type == "text" and content.startswith("/dev_step "):
            return await self._render_onboarding(
                identity,
                await self._onboarding.seed_development_step(
                    identity, content.removeprefix("/dev_step ").strip()
                ),
            )
        if event_type == "text" and content == "/dev_reset":
            return await self._render_onboarding(
                identity, await self._onboarding.reset_development_onboarding(identity)
            )
        deterministic_routes = {
            "/start",
            "/help",
            "/profile",
            "/add_workout",
            "/cancel",
            "/delete_me",
        }
        if event_type == "text" and content in deterministic_routes:
            return await self._dispatch(identity, event_type, content)
        lifecycle = await self._account_queries.lifecycle(identity)
        if (
            lifecycle is None
            or lifecycle["status"] is UserStatus.ONBOARDING_IN_PROGRESS
        ):
            return await self._dispatch(identity, event_type, content)
        if (
            lifecycle["status"]
            in {UserStatus.ONBOARDING_COMPLETED, UserStatus.PROFILE_COMPLETED}
            and event_type == "text"
            and not content.startswith("/")
        ):
            if content == keyboards.LABELS["change_profile"]:
                return self._render_profile_settings(
                    await self._onboarding.open_profile_settings(identity)
                )
            result = await self._onboarding.submit_profile_settings_text(
                identity, content
            )
            return (
                self._render_profile_settings(result)
                if result is not None
                else TelegramResponse(
                    messages.PROFILE_SETTINGS_UNPROMPTED,
                    user_keyboard=keyboards.completed_onboarding_keyboard(),
                )
            )
        if self._agent_workspace is None:
            return await self._dispatch(identity, event_type, content)
        return await self._agent_workspace.invoke(
            thread_id=f"telegram:{identity.telegram_user_id}",
            message=message,
            context=TelegramAgentContext(
                user_id=cast(UUID, lifecycle["user_id"]),
                dispatcher=lambda kind, content: self._dispatch(
                    identity, kind, content
                ),
                onboarding_updater=None,
                onboarding_active=False,
            ),
        )

    async def _dispatch(
        self, identity: TelegramIdentity, event_type: TelegramEventType, content: str
    ) -> TelegramResponse:
        if event_type == "callback":
            return await self.handle_callback(identity, content)
        routes = {
            "/start": self.start,
            "/help": self._help,
            "/profile": self.profile,
            "/add_workout": self.add_workout,
            "/cancel": self.cancel,
            "/delete_me": self.delete_me,
        }
        if content.startswith("/dev_step "):
            return await self._render_onboarding(
                identity,
                await self._onboarding.seed_development_step(
                    identity, content.removeprefix("/dev_step ").strip()
                ),
            )
        if content == "/dev_reset":
            return await self._render_onboarding(
                identity, await self._onboarding.reset_development_onboarding(identity)
            )
        if content in routes:
            return await routes[content](identity)
        return await self.handle_text(identity, content)

    async def _help(self, _: TelegramIdentity) -> TelegramResponse:
        return TelegramResponse(messages.HELP, keyboards.information_keyboard())

    async def start(self, identity: TelegramIdentity) -> TelegramResponse:
        return await self._render_onboarding(
            identity, await self._onboarding.start(identity)
        )

    async def handle_text(
        self, identity: TelegramIdentity, text: str
    ) -> TelegramResponse:
        result = await self._onboarding.handle_text(identity, text)
        return await self._render_onboarding(identity, result)

    async def handle_document(
        self,
        identity: TelegramIdentity,
        document: TelegramDocumentUpload,
        download: Callable[[Path], Awaitable[None]],
        progress: Callable[[str], Awaitable[None]],
    ) -> TelegramResponse:
        if self._apple_health is None:
            return TelegramResponse(messages.GENERIC_ERROR)
        outcome = await self._apple_health.process_upload(
            identity=identity, document=document, download=download, progress=progress
        )
        if outcome.status is not AppleHealthImportStatus.SUCCEEDED:
            error = messages.validation_error(
                outcome.safe_error_code or "training_file_import_failed"
            )
            if outcome.context is TrainingImportContext.ONBOARDING_HISTORY:
                return TelegramResponse(
                    f"{error}\n\n{messages.TRAINING_HISTORY_IMPORT}",
                    keyboards.training_history_import_keyboard(),
                )
            return TelegramResponse(
                error,
                user_keyboard=await self._lifecycle_keyboard(identity),
            )
        result_text = (
            messages.onboarding_history_imported(
                file_format=outcome.file_format,
                activities_imported=outcome.activities_imported,
                activities_updated=outcome.activities_updated,
                activities_skipped=outcome.activities_skipped,
            )
            if outcome.completed_onboarding
            else messages.training_file_result(
                file_format=outcome.file_format,
                activities_imported=outcome.activities_imported,
                activities_updated=outcome.activities_updated,
                activities_skipped=outcome.activities_skipped,
            )
        )
        response = TelegramResponse(result_text)
        return replace(
            response,
            user_keyboard=await self._lifecycle_keyboard(identity),
        )

    async def _lifecycle_keyboard(
        self, identity: TelegramIdentity
    ) -> ReplyKeyboardMarkup:
        lifecycle = await self._account_queries.lifecycle(identity)
        return self._keyboard_for_lifecycle(lifecycle)

    @staticmethod
    def _keyboard_for_lifecycle(
        lifecycle: dict[str, object] | None,
    ) -> ReplyKeyboardMarkup:
        if lifecycle is None:
            return keyboards.start_keyboard()
        if lifecycle["status"] in {
            UserStatus.ONBOARDING_COMPLETED,
            UserStatus.PROFILE_COMPLETED,
        }:
            return keyboards.completed_onboarding_keyboard()
        return keyboards.onboarding_keyboard()

    @staticmethod
    def _lifecycle_category(lifecycle: dict[str, object] | None) -> str:
        if lifecycle is None:
            return "absent"
        if lifecycle["status"] in {
            UserStatus.ONBOARDING_COMPLETED,
            UserStatus.PROFILE_COMPLETED,
        }:
            return "completed"
        return "onboarding"

    async def handle_callback(
        self, identity: TelegramIdentity, callback_data: str
    ) -> TelegramResponse:
        try:
            response = await self._handle_callback(identity, callback_data)
        except OnboardingApplicationError as exc:
            if exc.code not in {"invalid_action", "stale_action"}:
                raise
            if callback_data.startswith("ob:v1:equipment:"):
                response = await self._render_onboarding(
                    identity,
                    await self._onboarding.snapshot(identity),
                )
            elif callback_data.startswith("ps:v1:equipment:"):
                state = await self._onboarding.profile_settings_snapshot(identity)
                if state is None:
                    state = await self._onboarding.open_profile_settings(identity)
                response = self._render_profile_settings(state)
            else:
                raise
        return replace(response, edit_existing=True)

    async def _handle_callback(
        self, identity: TelegramIdentity, callback_data: str
    ) -> TelegramResponse:
        if callback_data == "nav:v1:welcome":
            return TelegramResponse(messages.WELCOME, keyboards.welcome_keyboard())
        if callback_data == "nav:v1:help":
            return await self._help(identity)
        if callback_data == "nav:v1:privacy":
            return TelegramResponse(
                messages.PRIVACY_SAFETY, keyboards.information_keyboard()
            )
        if callback_data == "acct:v1:delete:confirm":
            user_id = await self._account_queries.resolve_user_id(identity)
            if user_id is None:
                return TelegramResponse(messages.NOT_FOUND)
            deleted = await self._accounts.delete(user_id=user_id)
            return TelegramResponse(
                messages.DELETED if deleted else messages.DELETE_FAILED,
                clear_agent_thread=deleted,
            )
        if callback_data == "acct:v1:delete:keep":
            return TelegramResponse(messages.ACCOUNT_KEPT)
        if callback_data == "ps:v1:open":
            return self._render_profile_settings(
                await self._onboarding.open_profile_settings(identity)
            )
        if callback_data.startswith("ps:v1:"):
            return self._render_profile_settings(
                await self._onboarding.choose_profile_settings(
                    identity, callback_data.removeprefix("ps:v1:")
                )
            )
        if callback_data in {"nav:v1:consent", "ob:v1:consent"}:
            state = await self._onboarding.start(identity)
            if state.onboarding_status is OnboardingStatus.CANCELLED:
                state = await self._onboarding.restart(identity)
            result = await self._onboarding.confirm_consent(identity)
            return await self._render_onboarding(identity, result)
        if callback_data == "ob:v1:restart":
            return await self._render_onboarding(
                identity, await self._onboarding.restart(identity)
            )
        if callback_data == "ob:v1:resume":
            return await self._render_onboarding(
                identity, await self._onboarding.start(identity)
            )
        if callback_data == "ob:v1:profile":
            result = await self._onboarding.start_profile(identity)
            return await self._render_onboarding(identity, result)
        if callback_data == "ob:v1:goal:confirm":
            result = await self._onboarding.confirm_goal(identity)
            return await self._render_onboarding(identity, result)
        if callback_data.startswith("ob:v1:goal:choice:"):
            result = await self._onboarding.choose_goal_clarification(
                identity, callback_data.removeprefix("ob:v1:goal:choice:")
            )
            return await self._render_onboarding(identity, result)
        if callback_data.startswith("ob:v1:profile:gender:"):
            result = await self._onboarding.choose_gender(
                identity, callback_data.removeprefix("ob:v1:profile:gender:")
            )
            return await self._render_onboarding(identity, result)
        if callback_data.startswith("ob:v1:equipment:"):
            choice = callback_data.removeprefix("ob:v1:equipment:")
            result = await self._onboarding.choose_equipment(identity, choice)
            return await self._render_onboarding(identity, result)
        if callback_data.startswith("ob:v1:health:"):
            result = await self._onboarding.choose_health_limitations(
                identity, callback_data.removeprefix("ob:v1:health:")
            )
            return await self._render_onboarding(identity, result)
        if callback_data == "ob:v1:history:skip":
            result = await self._onboarding.skip_training_history(identity)
            return await self._render_onboarding(identity, result)
        if callback_data == "ob:v1:cancel":
            return TelegramResponse(
                messages.CANCEL_CONFIRM, keyboards.cancel_confirmation_keyboard()
            )
        if callback_data == "ob:v1:cancel:confirm":
            result = await self._onboarding.cancel(identity)
            return await self._render_onboarding(identity, result)
        if callback_data == "ob:v1:cancel:keep":
            result = await self._onboarding.snapshot(identity)
            return await self._render_onboarding(identity, result)
        raise OnboardingApplicationError("invalid_action")

    async def profile(self, identity: TelegramIdentity) -> TelegramResponse:
        profile = await self._account_queries.profile(identity)
        return (
            TelegramResponse(messages.persisted_profile(profile))
            if profile is not None
            else TelegramResponse(messages.NOT_FOUND)
        )

    async def add_workout(self, _: TelegramIdentity) -> TelegramResponse:
        return TelegramResponse(
            messages.ADD_WORKOUT_REQUEST, keyboards.add_workout_keyboard()
        )

    async def cancel(self, identity: TelegramIdentity) -> TelegramResponse:
        return await self._render_onboarding(
            identity, await self._onboarding.cancel(identity)
        )

    async def delete_me(self, _: TelegramIdentity) -> TelegramResponse:
        return TelegramResponse(
            messages.DELETE_CONFIRM, keyboards.deletion_confirmation_keyboard()
        )

    @staticmethod
    def _render_profile_settings(result: ProfileSettingsResult) -> TelegramResponse:
        if result.step.value == "GOAL_CLASSIFICATION_CONFIRM":
            text = messages.profile_goal_classification_confirmation(result.pending)
            if result.saved_field == "__classification_failed__":
                text = f"{messages.PROFILE_GOAL_CLASSIFICATION_FAILED}\n\n{text}"
            return TelegramResponse(
                text,
                keyboards.profile_goal_classification_keyboard(),
            )
        if result.step.value == "EQUIPMENT" and result.capability_review is not None:
            review = result.capability_review
            text = messages.equipment_review(review)
            if result.saved_field not in {None, "__closed__"}:
                text = (
                    f"{messages.PROFILE_SAVED.format(field=result.saved_field)}"
                    f"\n\n{text}"
                )
            return TelegramResponse(
                text,
                keyboards.profile_equipment_keyboard(
                    {str(item.id): item.display_name for item in review.options},
                    {str(item.id) for item in review.options if item.selected},
                ),
            )
        if result.step.value == "MENU":
            if result.saved_field == "__closed__":
                return TelegramResponse(messages.PROFILE_SETTINGS_CLOSED)
            notices: list[str] = []
            if result.execution_assessment is not None:
                notices.append(messages.equipment_summary(result.execution_assessment))
            if result.saved_field not in {None, "__closed__"}:
                notices.append(messages.PROFILE_SAVED.format(field=result.saved_field))
            notices.append(messages.PROFILE_SETTINGS_MENU)
            return TelegramResponse(
                "\n\n".join(notices),
                keyboards.profile_settings_keyboard(),
            )
        prompts = {
            "GOAL_MENU": messages.PROFILE_GOAL_MENU,
            "GOAL_MAIN": messages.PROFILE_GOAL_MAIN,
            "GOAL_OUTCOME": messages.PROFILE_GOAL_OUTCOME,
            "GOAL_DATE": messages.PROFILE_GOAL_DATE,
            "GOAL_SECONDARY": messages.PROFILE_GOAL_SECONDARY,
            "AVAILABILITY": messages.PROFILE_AVAILABILITY,
            "HEALTH": messages.PROFILE_HEALTH,
            "PERSONAL_MENU": messages.PROFILE_PERSONAL,
            "PERSONAL_BIRTH_YEAR": messages.PROFILE_BIRTH_YEAR,
            "PERSONAL_GENDER": messages.PROFILE_CATEGORY,
            "PERSONAL_WEIGHT": messages.PROFILE_WEIGHT,
            "PERSONAL_HEIGHT": messages.PROFILE_HEIGHT,
        }
        keyboard = (
            keyboards.profile_goal_keyboard()
            if result.step.value == "GOAL_MENU"
            else (
                keyboards.profile_goal_date_keyboard()
                if result.step.value == "GOAL_DATE"
                else (
                    keyboards.profile_goal_secondary_keyboard()
                    if result.step.value == "GOAL_SECONDARY"
                    else (
                        keyboards.profile_health_keyboard()
                        if result.step.value == "HEALTH"
                        else (
                            keyboards.profile_personal_keyboard()
                            if result.step.value == "PERSONAL_MENU"
                            else (
                                keyboards.profile_settings_gender_keyboard()
                                if result.step.value == "PERSONAL_GENDER"
                                else (
                                    keyboards.profile_goal_text_keyboard()
                                    if result.step.value.startswith("GOAL_")
                                    else keyboards.profile_settings_text_keyboard()
                                )
                            )
                        )
                    )
                )
            )
        )
        prompt = prompts.get(result.step.value, messages.PROFILE_SETTINGS_MENU)
        prompt = messages.profile_setting_prompt(
            result.step,
            result.current_value,
            prompt,
        )
        if result.saved_field not in {None, "__closed__"}:
            prompt = (
                f"{messages.PROFILE_SAVED.format(field=result.saved_field)}\n\n{prompt}"
            )
        return TelegramResponse(prompt, keyboard)

    async def _render_onboarding(
        self, identity: TelegramIdentity, result: OnboardingServiceResult
    ) -> TelegramResponse:
        mapping = {
            "setup_introduction": (
                messages.SETUP_INTRODUCTION,
                keyboards.setup_introduction_keyboard(),
            ),
            "goal_intake": (messages.GOAL_INTAKE, keyboards.goal_input_keyboard()),
            "profile_birth_year_intake": (
                messages.PROFILE_BIRTH_YEAR_INTAKE,
                keyboards.profile_text_input_keyboard(),
            ),
            "profile_gender_intake": (
                messages.PROFILE_GENDER_INTAKE,
                keyboards.profile_gender_keyboard(),
            ),
            "profile_weight_intake": (
                messages.PROFILE_WEIGHT_INTAKE,
                keyboards.profile_text_input_keyboard(),
            ),
            "profile_height_intake": (
                messages.PROFILE_HEIGHT_INTAKE,
                keyboards.profile_text_input_keyboard(),
            ),
            "availability_intake": (
                messages.AVAILABILITY_INTAKE,
                keyboards.profile_text_input_keyboard(),
            ),
            "health_limitations_intake": (
                messages.HEALTH_LIMITATIONS_INTAKE,
                keyboards.health_limitations_keyboard(),
            ),
            "training_history_import": (
                messages.TRAINING_HISTORY_IMPORT,
                keyboards.training_history_import_keyboard(),
            ),
            "cancelled": (messages.CANCELLED, keyboards.cancelled_keyboard()),
        }
        if result.created:
            return TelegramResponse(messages.WELCOME, keyboards.welcome_keyboard())
        if result.kind == "step":
            return TelegramResponse(
                messages.PRIVACY_SAFETY, keyboards.consent_keyboard()
            )
        if result.kind == "goal_clarification":
            field = result.answers.get("_goal_clarification_field")
            keyboard = (
                keyboards.goal_date_clarification_keyboard()
                if field == "event_date"
                else (
                    keyboards.goal_main_clarification_keyboard()
                    if field == "main_goal"
                    else keyboards.goal_input_keyboard()
                )
            )
            return TelegramResponse(
                (
                    f"{messages.validation_error(result.error_code)}\n\n"
                    f"{messages.goal_clarification(result.answers)}"
                    if result.error_code == "invalid_event_date"
                    else messages.goal_clarification(result.answers)
                ),
                keyboard,
            )
        if result.kind == "goal_confirmation":
            return TelegramResponse(
                messages.goal_confirmation(result.answers),
                keyboards.goal_confirmation_keyboard(),
            )
        if result.kind == "goal_off_topic":
            return TelegramResponse(
                messages.GOAL_OFF_TOPIC,
                keyboards.goal_input_keyboard(),
            )
        if result.kind == "context_validation_error":
            retry_prompts = {
                OnboardingStep.AVAILABILITY_INTAKE: (
                    messages.AVAILABILITY_INTAKE,
                    keyboards.profile_text_input_keyboard(),
                ),
                OnboardingStep.HEALTH_LIMITATIONS_INTAKE: (
                    messages.HEALTH_LIMITATIONS_INTAKE,
                    keyboards.health_limitations_keyboard(),
                ),
            }
            prompt, keyboard = retry_prompts.get(
                result.current_step,
                (messages.GOAL_INTAKE, keyboards.goal_input_keyboard()),
            )
            return TelegramResponse(
                f"{messages.CONTEXT_VALIDATION_ERROR}\n\n{prompt}", keyboard
            )
        if result.kind == "equipment_recommendation":
            if result.capability_review is None:
                return TelegramResponse(
                    messages.GOAL_CLASSIFICATION_REQUIRED,
                    keyboards.goal_input_keyboard(),
                )
        if result.kind in {"equipment_recommendation", "equipment_intake"}:
            if result.capability_review is None:
                return TelegramResponse(
                    messages.EQUIPMENT_UNMATCHED,
                    keyboards.health_limitations_keyboard(),
                )
            review = result.capability_review
            return TelegramResponse(
                messages.equipment_review(review),
                keyboards.equipment_intake_keyboard(
                    {str(item.id): item.display_name for item in review.options},
                    {str(item.id) for item in review.options if item.selected},
                ),
            )
        if result.kind == "equipment_unmatched":
            return TelegramResponse(
                f"{messages.EQUIPMENT_UNMATCHED}\n\n{messages.HEALTH_LIMITATIONS_INTAKE}",
                keyboards.health_limitations_keyboard(),
            )
        if (
            result.kind == "health_limitations_intake"
            and result.execution_assessment is not None
        ):
            return TelegramResponse(
                f"{messages.equipment_summary(result.execution_assessment)}\n\n"
                f"{messages.HEALTH_LIMITATIONS_INTAKE}",
                keyboards.health_limitations_keyboard(),
            )
        if result.kind == "onboarding_completed":
            return TelegramResponse(
                messages.ONBOARDING_COMPLETED,
                user_keyboard=keyboards.completed_onboarding_keyboard(),
            )
        if result.kind in mapping:
            text, keyboard = mapping[result.kind]
            return TelegramResponse(text, keyboard)
        if result.kind == "profile_validation_error":
            return TelegramResponse(
                messages.validation_error(result.error_code or "invalid_action"),
                keyboards.profile_text_input_keyboard(),
            )
        if result.kind == "provider_error":
            return TelegramResponse(
                messages.PARSE_PROVIDER_ERROR, keyboards.goal_input_keyboard()
            )
        if result.kind == "rate_limited":
            return TelegramResponse(
                messages.PARSE_RATE_LIMITED, keyboards.goal_input_keyboard()
            )
        return TelegramResponse(
            messages.PARSE_FALLBACK, keyboards.goal_input_keyboard()
        )
