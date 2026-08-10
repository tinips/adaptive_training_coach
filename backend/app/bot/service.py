"""Telegram application facade for onboarding, profiles, and file imports."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Protocol, cast
from uuid import UUID

from langchain_core.messages import HumanMessage

from app.bot import keyboards, messages
from app.bot.rendering import TelegramResponse
from app.domain.enums import UserStatus
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
        event_type: TelegramEventType = (
            "callback"
            if message.additional_kwargs.get("telegram_event_type") == "callback"
            else "text"
        )
        if not isinstance(message.content, str) or not message.content.strip():
            return TelegramResponse(messages.GENERIC_ERROR)
        if event_type == "text" and message.content.startswith("/dev_step "):
            return await self._render_onboarding(
                identity,
                await self._onboarding.seed_development_step(
                    identity, message.content.removeprefix("/dev_step ").strip()
                ),
            )
        if event_type == "text" and message.content == "/dev_reset":
            return await self._render_onboarding(
                identity, await self._onboarding.reset_development_onboarding(identity)
            )
        lifecycle = await self._account_queries.lifecycle(identity)
        if (
            lifecycle is None
            or lifecycle["status"] is UserStatus.ONBOARDING_IN_PROGRESS
        ):
            return await self._dispatch(identity, event_type, message.content)
        if (
            lifecycle["status"] is UserStatus.ONBOARDING_COMPLETED
            and event_type == "text"
            and not message.content.startswith("/")
        ):
            result = await self._onboarding.submit_profile_settings_text(
                identity, message.content
            )
            return (
                self._render_profile_settings(result)
                if result is not None
                else TelegramResponse(
                    messages.PROFILE_SETTINGS_UNPROMPTED,
                    keyboards.completed_onboarding_keyboard(),
                )
            )
        if self._agent_workspace is None:
            return await self._dispatch(identity, event_type, message.content)
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
        return TelegramResponse(
            messages.apple_health_file_result(
                activities_imported=outcome.activities_imported,
                activities_updated=outcome.activities_updated,
                activities_skipped=outcome.activities_skipped,
            )
        )

    async def handle_callback(
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
        if callback_data.startswith("ps:v1:"):
            return self._render_profile_settings(
                await self._onboarding.choose_profile_settings(identity, callback_data)
            )
        if callback_data in {"nav:v1:consent", "ob:v1:consent"}:
            result = await self._onboarding.confirm_consent(identity)
            return await self._render_onboarding(identity, result)
        if callback_data == "ob:v1:profile":
            result = await self._onboarding.start_profile(identity)
            return await self._render_onboarding(identity, result)
        if callback_data == "ob:v1:goal:confirm":
            result = await self._onboarding.confirm_goal(identity)
            return await self._render_onboarding(identity, result)
        if callback_data == "ob:v1:goal:add":
            result = await self._onboarding.add_to_goal(identity)
            return await self._render_onboarding(identity, result)
        if callback_data == "ob:v1:goal:restart":
            result = await self._onboarding.restart_goal(identity)
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
            if choice == "skip":
                result = await self._onboarding.skip_equipment_details(identity)
            else:
                result = await self._onboarding.choose_equipment(identity, choice)
            return await self._render_onboarding(identity, result)
        if callback_data.startswith("ob:v1:health:"):
            result = await self._onboarding.choose_health_limitations(
                identity, callback_data.removeprefix("ob:v1:health:")
            )
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
        if result.step.value == "MENU":
            return TelegramResponse(
                messages.PROFILE_SETTINGS_MENU, keyboards.profile_settings_keyboard()
            )
        prompts = {
            "GOAL_MAIN": messages.PROFILE_GOAL_MAIN,
            "GOAL_OUTCOME": messages.PROFILE_GOAL_OUTCOME,
            "GOAL_DATE": messages.PROFILE_GOAL_DATE,
            "AVAILABILITY": messages.PROFILE_AVAILABILITY,
            "HEALTH": messages.PROFILE_HEALTH,
            "PERSONAL_MENU": messages.PROFILE_PERSONAL,
            "PERSONAL_BIRTH_YEAR": messages.PROFILE_BIRTH_YEAR,
            "PERSONAL_GENDER": messages.PROFILE_CATEGORY,
            "PERSONAL_WEIGHT": messages.PROFILE_WEIGHT,
            "PERSONAL_HEIGHT": messages.PROFILE_HEIGHT,
        }
        return TelegramResponse(
            prompts.get(result.step.value, messages.PROFILE_SETTINGS_MENU),
            keyboards.profile_text_input_keyboard(),
        )

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
            "onboarding_completed": (
                messages.ONBOARDING_COMPLETED,
                keyboards.completed_onboarding_keyboard(),
            ),
            "cancelled": (messages.CANCELLED, keyboards.cancelled_keyboard()),
        }
        if result.created:
            return TelegramResponse(messages.WELCOME, keyboards.welcome_keyboard())
        if result.kind == "goal_confirmation":
            return TelegramResponse(
                messages.goal_confirmation(result.answers),
                keyboards.goal_confirmation_keyboard(),
            )
        if (
            result.kind == "equipment_recommendation"
            or result.kind == "equipment_intake"
        ):
            return TelegramResponse(
                messages.equipment_recommendation(
                    cast(
                        str | None, result.answers.get("equipment_recommendation_text")
                    )
                ),
                keyboards.equipment_intake_keyboard(),
            )
        if result.kind == "equipment_details_intake":
            return TelegramResponse(
                messages.EQUIPMENT_DETAILS_INTAKE,
                keyboards.equipment_details_keyboard(),
            )
        if result.kind in mapping:
            text, keyboard = mapping[result.kind]
            return TelegramResponse(text, keyboard)
        if result.kind == "profile_validation_error":
            return TelegramResponse(
                messages.validation_error(result.error_code or "invalid_action"),
                keyboards.profile_text_input_keyboard(),
            )
        return TelegramResponse(
            messages.PARSE_FALLBACK, keyboards.goal_input_keyboard()
        )
