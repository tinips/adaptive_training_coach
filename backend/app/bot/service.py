"""Telegram application facade for onboarding, profiles, and file imports."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path
from typing import Literal, Protocol, cast
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import UUID

from langchain_core.messages import HumanMessage
from pydantic import SecretStr
from telegram import ReplyKeyboardMarkup

from app.api.routes.telegram_web_app import workout_history_web_app_url
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
from app.services.weekly_planning.service import (
    WeeklyPlanningResult,
)

TelegramEventType = Literal["text", "callback"]
# Mirrors OnboardingService's internal `_GOAL_SPORT_KEY`. Kept as a literal here
# (as "_goal_clarification_field" was) so this module reads plain answer data
# rather than importing a service-layer enum.
_GOAL_SPORT_ANSWER_KEY = "goal_sport"


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


class WeeklyPlanningBotPort(Protocol):
    async def has_plan_for_next_week(self, identity: TelegramIdentity) -> bool: ...

    async def generate_next_week(
        self, identity: TelegramIdentity
    ) -> WeeklyPlanningResult: ...

    async def view_next_week(
        self, identity: TelegramIdentity
    ) -> WeeklyPlanningResult: ...

    async def delete_next_week(self, identity: TelegramIdentity) -> bool: ...


class CoachBotApplicationService:
    def __init__(
        self,
        *,
        onboarding: OnboardingService,
        profiles: ProfileService,
        account_queries: AccountQueryService,
        accounts: AccountService,
        training_import: TrainingImportBotPort | None = None,
        tcx_enabled: bool = True,
        planning: WeeklyPlanningBotPort | None = None,
        telegram_web_app_url: str | None = None,
        telegram_web_app_token: SecretStr | None = None,
    ) -> None:
        self._onboarding = onboarding
        self._profiles = profiles
        self._account_queries = account_queries
        self._accounts = accounts
        self._training_import = training_import
        self._tcx_enabled = tcx_enabled
        self._planning = planning
        self._telegram_web_app_url = telegram_web_app_url
        self._telegram_web_app_token = telegram_web_app_token

    async def handle_agent_input(
        self, identity: TelegramIdentity, message: HumanMessage
    ) -> TelegramResponse:
        before = await self._account_queries.lifecycle(identity)
        response = await self._handle_input(identity, message)
        after = await self._account_queries.lifecycle(identity)
        lifecycle_keyboard = await self._keyboard_for_lifecycle(identity, after)
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
            user_keyboard=response.user_keyboard or lifecycle_keyboard,
            edit_existing=(False if requires_new_message else response.edit_existing),
            refresh_user_keyboard=(
                lifecycle_changed or after is None or response.user_keyboard is not None
            ),
        )

    async def _handle_input(
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
                keyboards.LABELS["plan_next_week"]: "/plan_next_week",
                keyboards.LABELS["view_weekly_plan"]: "/view_weekly_plan",
                keyboards.LABELS["delete_weekly_plan"]: "/delete_weekly_plan",
                keyboards.LABELS["delete"]: "/delete_me",
            }.get(content, content)
        if event_type == "text":
            development_response = await self._handle_development_command(
                identity, content
            )
            if development_response is not None:
                return development_response
        deterministic_routes = {
            "/start",
            "/help",
            "/profile",
            "/plan_next_week",
            "/view_weekly_plan",
            "/delete_weekly_plan",
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
                return await self._render_profile_settings(
                    await self._onboarding.open_profile_settings(identity), identity
                )
            result = await self._onboarding.submit_profile_settings_text(
                identity, content
            )
            return (
                await self._render_profile_settings(result, identity)
                if result is not None
                else TelegramResponse(
                    messages.PROFILE_SETTINGS_UNPROMPTED,
                    user_keyboard=self._completed_onboarding_keyboard(identity),
                )
            )
        return await self._dispatch(identity, event_type, content)

    async def _dispatch(
        self, identity: TelegramIdentity, event_type: TelegramEventType, content: str
    ) -> TelegramResponse:
        if event_type == "callback":
            return await self.handle_callback(identity, content)
        routes = {
            "/start": self.start,
            "/help": self._help,
            "/profile": self.profile,
            "/zones": self.zones,
            "/plan_next_week": self.plan_next_week,
            "/view_weekly_plan": self.view_weekly_plan,
            "/delete_weekly_plan": self.delete_weekly_plan,
            "/cancel": self.cancel,
            "/delete_me": self.delete_me,
        }
        development_response = await self._handle_development_command(identity, content)
        if development_response is not None:
            return development_response
        if content in routes:
            return await routes[content](identity)
        return await self.handle_text(identity, content)

    async def _handle_development_command(
        self, identity: TelegramIdentity, content: str
    ) -> TelegramResponse | None:
        if content.startswith("/dev_step "):
            return await self._render_onboarding(
                identity,
                await self._onboarding.seed_development_step(
                    identity, content.removeprefix("/dev_step ").strip()
                ),
            )
        if content == "/dev_import_history":
            return await self._render_onboarding(
                identity,
                await self._onboarding.seed_development_step(identity, "history"),
            )
        if content == "/dev_reset":
            return await self._render_onboarding(
                identity, await self._onboarding.reset_development_onboarding(identity)
            )
        if content == "/dev_reset_goal_equipment":
            return await self._render_onboarding(
                identity,
                await self._onboarding.reset_development_goal_and_equipment(identity),
            )
        if content == "/dev_goal":
            return await self._render_onboarding(
                identity,
                await self._onboarding.reset_development_goal_and_equipment(identity),
            )
        return None

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

    async def submit_baseline_web_app(
        self, identity: TelegramIdentity, data: str
    ) -> TelegramResponse:
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            raise OnboardingApplicationError("invalid_action") from None
        if not isinstance(payload, dict):
            raise OnboardingApplicationError("invalid_action")
        result = await self._onboarding.submit_baseline_form(identity, payload)
        return await self._render_onboarding(identity, result)

    async def handle_document(
        self,
        identity: TelegramIdentity,
        document: TelegramDocumentUpload,
        download: Callable[[Path], Awaitable[None]],
        progress: Callable[[str], Awaitable[None]],
    ) -> TelegramResponse:
        if self._training_import is None:
            return TelegramResponse(messages.GENERIC_ERROR)
        outcome = await self._training_import.process_upload(
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
        return await self._keyboard_for_lifecycle(identity, lifecycle)

    async def _keyboard_for_lifecycle(
        self,
        identity: TelegramIdentity,
        lifecycle: dict[str, object] | None,
    ) -> ReplyKeyboardMarkup:
        if lifecycle is None:
            return keyboards.start_keyboard()
        if lifecycle["status"] in {
            UserStatus.ONBOARDING_COMPLETED,
            UserStatus.PROFILE_COMPLETED,
        }:
            plan_available = (
                await self._planning.has_plan_for_next_week(identity)
                if self._planning is not None
                else False
            )
            return self._completed_onboarding_keyboard(
                identity, plan_available=plan_available
            )
        return keyboards.onboarding_keyboard()

    def _completed_onboarding_keyboard(
        self,
        identity: TelegramIdentity | None = None,
        *,
        plan_available: bool = False,
    ) -> ReplyKeyboardMarkup:
        return keyboards.completed_onboarding_keyboard(
            plan_available=plan_available,
            workout_history_url=workout_history_web_app_url(
                self._telegram_web_app_url,
                telegram_user_id=(
                    identity.telegram_user_id if identity is not None else None
                ),
                bot_token=self._telegram_web_app_token,
            ),
        )

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
                    response = await self._render_profile_settings(state, identity)
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
        if callback_data == "plan:v1:delete:confirm":
            if self._planning is None:
                return TelegramResponse(messages.WEEKLY_PLAN_DELETE_NOT_FOUND)
            deleted = await self._planning.delete_next_week(identity)
            return TelegramResponse(
                messages.WEEKLY_PLAN_DELETED
                if deleted
                else messages.WEEKLY_PLAN_DELETE_NOT_FOUND
            )
        if callback_data == "plan:v1:delete:keep":
            return TelegramResponse("Keeping your weekly plan.")
        if callback_data == "ps:v1:open":
            return await self._render_profile_settings(
                await self._onboarding.open_profile_settings(identity), identity
            )
        if callback_data.startswith("ps:v1:"):
            profile_state = await self._onboarding.choose_profile_settings(
                identity, callback_data.removeprefix("ps:v1:")
            )
            if profile_state.baseline_reopened:
                return await self._render_onboarding(
                    identity, await self._onboarding.snapshot(identity)
                )
            return await self._render_profile_settings(profile_state, identity)
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
        if callback_data.startswith("ob:v1:goal:sport:"):
            result = await self._onboarding.choose_goal_sport(
                identity, callback_data.removeprefix("ob:v1:goal:sport:")
            )
            return await self._render_onboarding(identity, result)
        if callback_data == "ob:v1:goal:back":
            result = await self._onboarding.reopen_goal_sports(identity)
            return await self._render_onboarding(identity, result)
        if callback_data.startswith("ob:v1:goal:swim:"):
            result = await self._onboarding.choose_swimming_type(
                identity, callback_data.removeprefix("ob:v1:goal:swim:")
            )
            return await self._render_onboarding(identity, result)
        if callback_data.startswith("ob:v1:goal:template:"):
            result = await self._onboarding.choose_goal_template(
                identity, callback_data.removeprefix("ob:v1:goal:template:")
            )
            return await self._render_onboarding(identity, result)
        if callback_data == "ob:v1:goal:metric:skip":
            return await self._render_onboarding(
                identity, await self._onboarding.skip_goal_metric(identity)
            )
        if callback_data == "ob:v1:availability:confirm":
            return await self._render_onboarding(
                identity, await self._onboarding.confirm_availability(identity)
            )
        if callback_data == "ob:v1:availability:edit":
            return await self._render_onboarding(
                identity, await self._onboarding.edit_availability(identity)
            )
        if callback_data == "ob:v1:goal:nodate":
            result = await self._onboarding.skip_event_date(identity)
            return await self._render_onboarding(identity, result)
        if callback_data.startswith("ob:v1:support:"):
            raw = callback_data.removeprefix("ob:v1:support:")
            result = await self._onboarding.choose_supporting_goal(
                identity, None if raw == "none" else raw
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
        if callback_data == "ob:v1:history:file":
            return TelegramResponse(
                messages.TRAINING_HISTORY_FILE_PROMPT,
                keyboards.training_history_import_keyboard(),
            )
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

    async def zones(self, identity: TelegramIdentity) -> TelegramResponse:
        zones = await self._account_queries.zones(identity)
        return (
            TelegramResponse(messages.zones_view(zones))
            if zones is not None
            else TelegramResponse(messages.NOT_FOUND)
        )

    async def plan_next_week(self, identity: TelegramIdentity) -> TelegramResponse:
        if self._planning is None:
            return TelegramResponse(messages.WEEKLY_PLAN_UNAVAILABLE)
        result = await self._planning.generate_next_week(identity)
        if result.kind == "baseline_required":
            response = await self._render_onboarding(
                identity, await self._onboarding.snapshot(identity)
            )
            return replace(
                response,
                text=(
                    "Your goal changed, so I need an updated baseline before "
                    "planning.\n\n"
                    f"{response.text}"
                ),
            )
        if result.kind == "timezone_required":
            await self._onboarding.open_profile_settings(identity)
            await self._onboarding.choose_profile_settings(identity, "section:personal")
            settings = await self._onboarding.choose_profile_settings(
                identity, "personal:timezone"
            )
            response = await self._render_profile_settings(settings, identity)
            return replace(
                response,
                text=(
                    "Set your timezone before creating a weekly plan.\n\n"
                    f"{response.text}"
                ),
            )
        return self._render_planning(result, viewing=False)

    async def view_weekly_plan(self, identity: TelegramIdentity) -> TelegramResponse:
        if self._planning is None:
            return TelegramResponse(messages.WEEKLY_PLAN_NOT_FOUND)
        result = await self._planning.view_next_week(identity)
        return self._render_planning(result, viewing=True)

    async def delete_weekly_plan(self, _: TelegramIdentity) -> TelegramResponse:
        return TelegramResponse(
            messages.WEEKLY_PLAN_DELETE_CONFIRM,
            keyboards.weekly_plan_deletion_keyboard(),
        )

    @staticmethod
    def _render_planning(
        result: WeeklyPlanningResult,
        *,
        viewing: bool,
    ) -> TelegramResponse:
        if result.kind in {"created", "existing"} and result.plan is not None:
            rendered_messages = messages.weekly_plan_messages(
                result.plan,
                generation_source=result.generation_source,
            )
            return TelegramResponse(
                rendered_messages[0],
                text_chunks=rendered_messages[1:],
            )
        if result.kind == "insufficient" and result.readiness is not None:
            return TelegramResponse(messages.weekly_plan_readiness(result.readiness))
        return TelegramResponse(
            messages.WEEKLY_PLAN_NOT_FOUND
            if viewing
            else messages.WEEKLY_PLAN_UNAVAILABLE
        )

    async def cancel(self, identity: TelegramIdentity) -> TelegramResponse:
        return await self._render_onboarding(
            identity, await self._onboarding.cancel(identity)
        )

    async def delete_me(self, _: TelegramIdentity) -> TelegramResponse:
        return TelegramResponse(
            messages.DELETE_CONFIRM, keyboards.deletion_confirmation_keyboard()
        )

    async def _render_profile_settings(
        self, result: ProfileSettingsResult, identity: TelegramIdentity
    ) -> TelegramResponse:
        if result.confirm_discard:
            return TelegramResponse(
                messages.PROFILE_DISCARD_CHANGES,
                keyboards.profile_discard_changes_keyboard(),
            )
        if result.step.value == "GOAL_MAIN":
            sport = result.pending.get(_GOAL_SPORT_ANSWER_KEY)
            if isinstance(sport, str) and sport:
                templates = await self._onboarding.goal_template_options(sport)
                return TelegramResponse(
                    messages.PROFILE_GOAL_MAIN_TEMPLATE,
                    keyboards.profile_goal_template_keyboard(templates),
                )
            sports = await self._onboarding.goal_sport_options()
            return TelegramResponse(
                messages.PROFILE_GOAL_MAIN_SPORT,
                keyboards.profile_goal_sport_keyboard(sports),
            )
        if result.step.value == "GOAL_SECONDARY":
            supporting_options = await self._onboarding.supporting_goal_options(
                identity
            )
            text = messages.PROFILE_GOAL_SECONDARY
            if result.saved_field not in {None, "__closed__"}:
                text = (
                    f"{messages.PROFILE_SAVED.format(field=result.saved_field)}"
                    f"\n\n{text}"
                )
            return TelegramResponse(
                text,
                keyboards.profile_supporting_goal_keyboard(supporting_options),
            )
        if result.step.value == "GOAL_METRICS":
            fields = result.pending.get("goal_metric_fields")
            index = result.pending.get("goal_metric_index")
            if (
                isinstance(fields, list)
                and isinstance(index, int)
                and 0 <= index < len(fields)
                and isinstance(fields[index], str)
            ):
                return TelegramResponse(
                    messages.goal_metric_prompt(cast(str, fields[index])),
                    keyboards.profile_goal_metric_keyboard(),
                )
            return TelegramResponse(messages.GENERIC_ERROR)
        if result.step.value == "AVAILABILITY_REVIEW":
            return TelegramResponse(
                messages.availability_review(result.pending.get("availability_draft")),
                keyboards.profile_availability_review_keyboard(),
            )
        if result.step.value == "AVAILABILITY":
            return TelegramResponse(
                messages.profile_availability_prompt(
                    result.pending.get("current_availability")
                ),
                keyboards.profile_settings_text_keyboard(),
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
            "GOAL_DATE": messages.PROFILE_GOAL_DATE,
            "AVAILABILITY": messages.PROFILE_AVAILABILITY,
            "HEALTH": messages.PROFILE_HEALTH,
            "PERSONAL_MENU": messages.PROFILE_PERSONAL,
            "PERSONAL_BIRTH_YEAR": messages.PROFILE_BIRTH_YEAR,
            "PERSONAL_GENDER": messages.PROFILE_CATEGORY,
            "PERSONAL_WEIGHT": messages.PROFILE_WEIGHT,
            "PERSONAL_HEIGHT": messages.PROFILE_HEIGHT,
            "PERSONAL_TIMEZONE": messages.PROFILE_TIMEZONE,
        }
        keyboard = (
            keyboards.profile_goal_keyboard(result.pending)
            if result.step.value == "GOAL_MENU"
            else (
                keyboards.profile_goal_date_keyboard()
                if result.step.value == "GOAL_DATE"
                else (
                    keyboards.profile_health_keyboard()
                    if result.step.value == "HEALTH"
                    else (
                        keyboards.profile_personal_keyboard(result.pending)
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
            "profile_timezone_intake": (
                messages.PROFILE_TIMEZONE_INTAKE,
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
        if result.kind == "goal_intake":
            sport = result.answers.get(_GOAL_SPORT_ANSWER_KEY)
            if isinstance(sport, str) and sport:
                templates = await self._onboarding.goal_template_options(sport)
                return TelegramResponse(
                    messages.GOAL_TEMPLATE_PROMPT,
                    keyboards.goal_template_keyboard(templates),
                )
            sports = await self._onboarding.goal_sport_options()
            return TelegramResponse(
                messages.GOAL_INTAKE, keyboards.goal_sport_keyboard(sports)
            )
        if result.kind == "goal_event_date":
            return TelegramResponse(
                messages.GOAL_EVENT_DATE_PROMPT, keyboards.goal_event_date_keyboard()
            )
        if result.kind == "goal_swimming_type":
            return TelegramResponse(
                messages.GOAL_SWIMMING_TYPE_PROMPT, keyboards.swimming_type_keyboard()
            )
        if result.kind == "goal_metric_intake":
            fields = result.answers.get("goal_metric_fields")
            index = result.answers.get("goal_metric_index")
            if (
                not isinstance(fields, list)
                or not isinstance(index, int)
                or isinstance(index, bool)
                or not 0 <= index < len(fields)
                or not isinstance(fields[index], str)
            ):
                return TelegramResponse(messages.GENERIC_ERROR)
            return TelegramResponse(
                messages.goal_metric_prompt(cast(str, fields[index])),
                keyboards.goal_metric_keyboard(),
            )
        if result.kind == "goal_confirmed":
            supporting_options = await self._onboarding.supporting_goal_options(
                identity
            )
            return TelegramResponse(
                messages.GOAL_SUPPORT_PROMPT,
                keyboards.supporting_goal_keyboard(supporting_options),
            )
        if result.kind == "availability_review":
            return TelegramResponse(
                messages.availability_review(result.answers.get("availability_draft")),
                keyboards.availability_review_keyboard(),
            )
        if result.kind == "availability_details":
            draft = result.answers.get("availability_draft")
            raw_missing = (
                draft.get("missing_details") if isinstance(draft, dict) else []
            )
            missing = raw_missing if isinstance(raw_missing, list) else []
            descriptions = [
                str(item.get("description"))
                for item in missing
                if isinstance(item, dict) and item.get("description")
            ]
            detail = " ".join(dict.fromkeys(descriptions)) or (
                "Please include a duration for each day."
            )
            return TelegramResponse(
                f"{detail}\n\n{messages.AVAILABILITY_INTAKE}",
                keyboards.profile_text_input_keyboard(),
            )
        if result.kind == "availability_clarification":
            return TelegramResponse(
                messages.AVAILABILITY_CLARIFICATION,
                keyboards.profile_text_input_keyboard(),
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
                (messages.AVAILABILITY_INTAKE, keyboards.profile_text_input_keyboard()),
            )
            return TelegramResponse(
                f"{messages.CONTEXT_VALIDATION_ERROR}\n\n{prompt}", keyboard
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
                messages.HEALTH_LIMITATIONS_INTAKE,
                keyboards.health_limitations_keyboard(),
            )
        if (
            result.kind == "health_limitations_intake"
            and result.execution_assessment is not None
        ):
            return TelegramResponse(
                messages.HEALTH_LIMITATIONS_INTAKE,
                keyboards.health_limitations_keyboard(),
            )
        if result.kind == "health_limitations_intake":
            return TelegramResponse(
                messages.HEALTH_LIMITATIONS_INTAKE,
                keyboards.health_limitations_keyboard(),
            )
        if result.kind in {"baseline_intake", "baseline_validation_error"}:
            if not self._telegram_web_app_url:
                return TelegramResponse(
                    "The baseline form is not configured. Please contact support."
                )
            raw_fields = result.answers.get("baseline_fields")
            fields = raw_fields if isinstance(raw_fields, list) else []
            query = dict(parse_qsl(urlsplit(self._telegram_web_app_url).query))
            query["fields"] = ",".join(
                value for value in fields if isinstance(value, str)
            )
            if result.kind == "baseline_validation_error" and result.error_code:
                query["error"] = result.error_code
            parts = urlsplit(self._telegram_web_app_url)
            web_app_url = urlunsplit(
                (parts.scheme, parts.netloc, parts.path, urlencode(query), "")
            )
            return TelegramResponse(
                "Complete your training baseline\n\n"
                "Answer a few quick questions so I can create a safe first-week plan.",
                keyboards.baseline_web_app_keyboard(web_app_url),
            )
        if result.kind == "onboarding_completed":
            return TelegramResponse(
                (
                    messages.TRAINING_HISTORY_SKIP_SUGGESTION
                    if result.training_history_skipped
                    else messages.ONBOARDING_COMPLETED
                ),
                user_keyboard=self._completed_onboarding_keyboard(identity),
            )
        if result.kind in mapping:
            text, keyboard = mapping[result.kind]
            return TelegramResponse(text, keyboard)
        if (
            result.kind == "profile_validation_error"
            and result.current_step is OnboardingStep.GOAL_EVENT_DATE
        ):
            error = messages.validation_error(result.error_code or "invalid_action")
            return TelegramResponse(
                f"{error}\n\n{messages.GOAL_EVENT_DATE_PROMPT}",
                keyboards.goal_event_date_keyboard(),
            )
        if result.kind == "profile_validation_error":
            return TelegramResponse(
                messages.validation_error(result.error_code or "invalid_action"),
                keyboards.profile_text_input_keyboard(),
            )
        return TelegramResponse(messages.GENERIC_ERROR)
