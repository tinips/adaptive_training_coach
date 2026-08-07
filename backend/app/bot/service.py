"""Concrete application facade consumed by thin Telegram handlers."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path
from typing import Protocol, cast
from uuid import UUID

from langchain_core.messages import HumanMessage
from telegram import InlineKeyboardMarkup

from app.bot import keyboards, messages
from app.bot.rendering import TelegramResponse
from app.domain.enums import (
    AppleHealthImportStatus,
    BaselineSource,
    OnboardingStatus,
    OnboardingStep,
    SyncStatus,
    TrainingFileFormat,
    UserStatus,
    WorkoutFlowStep,
)
from app.schemas.common import TelegramIdentity
from app.schemas.onboarding_service import OnboardingServiceResult
from app.schemas.training_import import TelegramDocumentUpload
from app.services.accounts import AccountQueryService, AccountService
from app.services.onboarding import OnboardingApplicationError, OnboardingService
from app.services.profiles import (
    BaselineSelectionUnavailableError,
    IncompleteProfileError,
    ProfileService,
)
from app.services.strava.disconnect import DisconnectOutcome
from app.services.strava.exceptions import StravaServiceError
from app.services.strava.sync import SyncOutcome
from app.services.training_import import TrainingFileImportOutcome
from app.services.workout_feedback import (
    WorkoutFeedbackError,
    WorkoutFeedbackResult,
    WorkoutFeedbackService,
)
from app.workflows.telegram_orchestrator.workspace import (
    TelegramAgentContext,
    TelegramAgentWorkspace,
    TelegramEventType,
)

logger = logging.getLogger(__name__)


class StravaBotPort(Protocol):
    """Strava orchestration used by bot actions."""

    async def issue_connect_url(self, *, user_id: UUID) -> str: ...

    async def manual_sync(self, *, user_id: UUID) -> SyncOutcome: ...

    async def recalculate_baseline(self, *, user_id: UUID) -> object: ...

    async def disconnect(
        self,
        *,
        user_id: UUID,
        confirmed: bool,
    ) -> DisconnectOutcome: ...

    async def revoke_for_deletion(self, *, user_id: UUID) -> bool: ...


class TrainingImportBotPort(Protocol):
    """Unified file-import orchestration used by document and resume actions."""

    async def process_upload(
        self,
        *,
        identity: TelegramIdentity,
        document: TelegramDocumentUpload,
        download: Callable[[Path], Awaitable[None]],
        progress: Callable[[str], Awaitable[None]],
    ) -> TrainingFileImportOutcome: ...

    async def latest_outcome(
        self,
        *,
        user_id: UUID,
    ) -> TrainingFileImportOutcome | None: ...

    async def cancel_active(self, *, user_id: UUID) -> None: ...


class CoachBotApplicationService:
    """Render application use cases without putting business logic in handlers."""

    def __init__(
        self,
        *,
        onboarding: OnboardingService,
        profiles: ProfileService,
        account_queries: AccountQueryService,
        accounts: AccountService,
        strava: StravaBotPort,
        apple_health: TrainingImportBotPort | None = None,
        workout_feedback: WorkoutFeedbackService | None = None,
        strava_enabled: bool = False,
        apple_health_enabled: bool = True,
        tcx_enabled: bool = True,
        workout_feedback_enabled: bool = True,
        agent_workspace: TelegramAgentWorkspace | None = None,
    ) -> None:
        self._onboarding = onboarding
        self._profiles = profiles
        self._account_queries = account_queries
        self._accounts = accounts
        self._strava = strava
        self._apple_health = apple_health
        self._workout_feedback = workout_feedback
        self._strava_enabled = strava_enabled
        self._apple_health_enabled = apple_health_enabled
        self._tcx_enabled = tcx_enabled
        self._workout_feedback_enabled = workout_feedback_enabled
        self._agent_workspace = agent_workspace

    async def handle_agent_input(
        self,
        identity: TelegramIdentity,
        message: HumanMessage,
    ) -> TelegramResponse:
        """Route one opaque Telegram event through the global agent workspace."""

        raw_event_type = message.additional_kwargs.get("telegram_event_type", "text")
        event_type: TelegramEventType = (
            "callback" if raw_event_type == "callback" else "text"
        )
        if not isinstance(message.content, str) or not message.content.strip():
            return TelegramResponse(messages.GENERIC_ERROR)
        workspace = self._agent_workspace
        if workspace is None:
            return await self._dispatch_agent_event(
                identity,
                event_type,
                message.content,
            )
        lifecycle = await self._account_queries.lifecycle(identity)
        user_id = cast(UUID, lifecycle["user_id"]) if lifecycle is not None else None
        onboarding_active = bool(
            lifecycle is not None
            and lifecycle["status"] is UserStatus.ONBOARDING_IN_PROGRESS
        )

        if onboarding_active:
            # Availability, equipment, and limitations can contain private raw
            # context.  The focused onboarding workflows are stateless, so send
            # active-onboarding events straight to them instead of first adding
            # the HumanMessage to the persistent global-agent checkpoint.
            response = await self._dispatch_agent_event(
                identity,
                event_type,
                message.content,
            )
            if response.clear_agent_thread:
                await workspace.delete_thread(
                    f"telegram:{identity.telegram_user_id}",
                )
            return response

        async def dispatch(
            supplied_event_type: TelegramEventType,
            content: str,
        ) -> TelegramResponse:
            return await self._dispatch_agent_event(
                identity,
                supplied_event_type,
                content,
            )

        async def load_presentation() -> TelegramResponse:
            snapshot = await self._onboarding.snapshot(identity)
            return await self._render_onboarding(identity, snapshot)

        return await workspace.invoke(
            thread_id=f"telegram:{identity.telegram_user_id}",
            message=message,
            context=TelegramAgentContext(
                user_id=user_id,
                dispatcher=dispatch,
                onboarding_updater=(
                    self._onboarding.update_onboarding_data
                    if user_id is not None
                    else None
                ),
                presentation_loader=(
                    load_presentation if user_id is not None else None
                ),
                onboarding_active=onboarding_active,
            ),
        )

    async def _dispatch_agent_event(
        self,
        identity: TelegramIdentity,
        event_type: TelegramEventType,
        content: str,
    ) -> TelegramResponse:
        """Application tool target; Telegram handlers never interpret the event."""

        if event_type == "callback":
            return await self.handle_callback(identity, content)
        command_routes: dict[
            str,
            Callable[[TelegramIdentity], Awaitable[TelegramResponse]],
        ] = {
            "/start": self.start,
            "/help": self._help,
            "/profile": self.profile,
            "/baseline": self.baseline,
            "/add_workout": self.add_workout,
            "/strava": self.strava,
            "/cancel": self.cancel,
            "/delete_me": self.delete_me,
        }
        command = command_routes.get(content.casefold())
        if command is not None:
            return await command(identity)
        return await self.handle_text(identity, content)

    async def _help(self, identity: TelegramIdentity) -> TelegramResponse:
        del identity
        return TelegramResponse(messages.HELP)

    async def start(self, identity: TelegramIdentity) -> TelegramResponse:
        result = await self._onboarding.start(identity)
        return await self._render_onboarding(identity, result)

    async def handle_text(
        self,
        identity: TelegramIdentity,
        text: str,
    ) -> TelegramResponse:
        try:
            if self._workout_feedback_enabled and self._workout_feedback is not None:
                feedback = await self._workout_feedback.snapshot(identity)
                if feedback is not None:
                    if feedback.state is WorkoutFlowStep.HR_ENTRY:
                        updated = await self._workout_feedback.submit_manual_heart_rate(
                            identity,
                            text,
                        )
                        return await self._render_workout_feedback(
                            identity,
                            updated,
                        )
                    if feedback.state is WorkoutFlowStep.DESCRIPTION_ENTRY:
                        updated = (
                            await self._workout_feedback.submit_discomfort_description(
                                identity,
                                text,
                            )
                        )
                        return await self._render_workout_feedback(
                            identity,
                            updated,
                        )
            result = await self._onboarding.handle_text(identity, text)
        except OnboardingApplicationError as exc:
            return TelegramResponse(messages.validation_error(exc.code))
        except WorkoutFeedbackError as exc:
            return TelegramResponse(messages.validation_error(exc.code))
        return await self._render_onboarding(identity, result)

    async def handle_document(
        self,
        identity: TelegramIdentity,
        document: TelegramDocumentUpload,
        download: Callable[[Path], Awaitable[None]],
        progress: Callable[[str], Awaitable[None]],
    ) -> TelegramResponse:
        if self._apple_health is None:
            return TelegramResponse(
                messages.validation_error("training_file_import_disabled")
            )
        feedback: WorkoutFeedbackResult | None = None
        try:
            if self._workout_feedback_enabled and self._workout_feedback is not None:
                feedback = await self._workout_feedback.snapshot(identity)
                if feedback is not None and feedback.state not in {
                    WorkoutFlowStep.WAITING_FOR_FILE,
                    WorkoutFlowStep.COMPLETE,
                    WorkoutFlowStep.CANCELLED,
                }:
                    raise WorkoutFeedbackError("workout_flow_already_active")
            outcome = await self._apple_health.process_upload(
                identity=identity,
                document=document,
                download=download,
                progress=progress,
            )
            if outcome.status is not AppleHealthImportStatus.SUCCEEDED:
                error_text = messages.validation_error(
                    outcome.safe_error_code
                    or (
                        "training_file_import_cancelled"
                        if outcome.status is AppleHealthImportStatus.CANCELLED
                        else "training_file_import_failed"
                    )
                )
                if (
                    feedback is not None
                    and feedback.state is WorkoutFlowStep.WAITING_FOR_FILE
                ):
                    return TelegramResponse(
                        error_text,
                        keyboards.add_workout_keyboard(),
                    )
                return await self._home_with_prefix(identity, error_text)
            return await self._render_import_outcome(identity, outcome)
        except OnboardingApplicationError as exc:
            return TelegramResponse(messages.validation_error(exc.code))
        except WorkoutFeedbackError as exc:
            return TelegramResponse(messages.validation_error(exc.code))

    async def handle_callback(
        self,
        identity: TelegramIdentity,
        callback_data: str,
    ) -> TelegramResponse:
        try:
            response = await self._route_callback(identity, callback_data)
        except OnboardingApplicationError as exc:
            if exc.code in {
                "invalid_action",
                "stale_action",
                "onboarding_not_active",
                "restart_not_allowed",
            }:
                try:
                    response = await self._render_onboarding(
                        identity,
                        await self._onboarding.snapshot(identity),
                    )
                except OnboardingApplicationError:
                    response = TelegramResponse(messages.NOT_FOUND)
            else:
                response = TelegramResponse(messages.validation_error(exc.code))
        except WorkoutFeedbackError as exc:
            response = TelegramResponse(messages.validation_error(exc.code))
        except IncompleteProfileError:
            response = TelegramResponse(messages.validation_error("incomplete_profile"))
        except BaselineSelectionUnavailableError:
            response = TelegramResponse(messages.BASELINE_SELECTION_UNAVAILABLE)
        except StravaServiceError as exc:
            response = TelegramResponse(self._strava_error(exc.error_code))
        return replace(response, edit_existing=True)

    async def profile(self, identity: TelegramIdentity) -> TelegramResponse:
        profile = await self._account_queries.profile(identity)
        if profile is not None:
            return TelegramResponse(messages.persisted_profile(profile))
        try:
            snapshot = await self._onboarding.snapshot(identity)
        except OnboardingApplicationError:
            return TelegramResponse(messages.PROFILE_INCOMPLETE)
        return TelegramResponse(
            messages.PROFILE_INCOMPLETE,
            keyboards.resume_keyboard(
                cancelled=snapshot.kind == "cancelled",
            ),
        )

    async def baseline(self, identity: TelegramIdentity) -> TelegramResponse:
        baseline = await self._account_queries.baseline(identity)
        if baseline is not None:
            return TelegramResponse(messages.baseline_summary(baseline))
        profile = await self._account_queries.profile(identity)
        if profile is None:
            return TelegramResponse(messages.BASELINE_NOT_READY)
        source = str(profile.get("baseline_source", ""))
        if source.endswith("MANUAL"):
            return TelegramResponse(messages.BASELINE_MANUAL_PENDING)
        if source.endswith("CALIBRATION"):
            return TelegramResponse(messages.BASELINE_CALIBRATION_PENDING)
        return TelegramResponse(messages.BASELINE_NOT_READY)

    async def add_workout(
        self,
        identity: TelegramIdentity,
    ) -> TelegramResponse:
        if not (self._apple_health_enabled or self._tcx_enabled):
            return TelegramResponse(
                messages.validation_error("training_file_import_disabled")
            )
        if not self._workout_feedback_enabled or self._workout_feedback is None:
            return TelegramResponse(messages.ADD_WORKOUT_REQUEST)
        try:
            feedback = await self._workout_feedback.begin_waiting_upload(identity)
        except WorkoutFeedbackError as exc:
            return TelegramResponse(messages.validation_error(exc.code))
        return await self._render_workout_feedback(identity, feedback)

    async def strava(self, identity: TelegramIdentity) -> TelegramResponse:
        if not self._strava_enabled:
            return TelegramResponse(messages.STRAVA_DISABLED)
        lifecycle = await self._account_queries.lifecycle(identity)
        if lifecycle is None:
            return TelegramResponse(messages.NOT_FOUND)
        if not self._strava_allowed(lifecycle["status"]):
            return TelegramResponse(
                messages.PROFILE_INCOMPLETE,
                keyboards.resume_keyboard(),
            )
        user_id = lifecycle["user_id"]
        status = await self._account_queries.strava(identity)
        connected = bool(status and status.get("connected"))
        can_disconnect = bool(status and status.get("can_disconnect"))
        syncing = self._sync_active(
            status.get("sync_status") if status is not None else None
        )
        connect_url: str | None = None
        connect_error: str | None = None
        if not connected:
            try:
                connect_url = await self._strava.issue_connect_url(user_id=user_id)
            except StravaServiceError as exc:
                if not can_disconnect:
                    return TelegramResponse(self._strava_error(exc.error_code))
                connect_error = self._strava_error(exc.error_code)
        text = messages.strava_status(
            status or {"connected": False, "can_disconnect": False}
        )
        if not connected:
            text = f"{text}\n\n{messages.STRAVA_CONNECT_EXPLANATION}"
        if connect_error is not None:
            text = f"{text}\n\n{connect_error}"
        return TelegramResponse(
            text,
            keyboards.strava_keyboard(
                connected=connected,
                can_disconnect=can_disconnect,
                syncing=syncing,
                connect_url=connect_url,
            ),
        )

    async def cancel(self, identity: TelegramIdentity) -> TelegramResponse:
        try:
            snapshot = await self._onboarding.snapshot(identity)
        except OnboardingApplicationError:
            return TelegramResponse(messages.NOT_FOUND)
        if snapshot.kind == "cancelled":
            return TelegramResponse(
                messages.CANCELLED,
                keyboards.resume_keyboard(cancelled=True),
            )
        return TelegramResponse(
            messages.CANCEL_CONFIRM,
            keyboards.cancel_confirmation_keyboard(),
        )

    async def delete_me(self, identity: TelegramIdentity) -> TelegramResponse:
        user_id = await self._account_queries.resolve_user_id(identity)
        if user_id is None:
            return TelegramResponse(messages.NOT_FOUND)
        return TelegramResponse(
            messages.DELETE_CONFIRM,
            keyboards.deletion_confirmation_keyboard(),
        )

    async def _route_callback(
        self,
        identity: TelegramIdentity,
        callback_data: str,
    ) -> TelegramResponse:
        if callback_data.startswith("nav:v1:"):
            return await self._navigation(
                identity,
                callback_data.removeprefix("nav:v1:"),
            )
        if callback_data == "ob:v1:consent":
            return await self._render_onboarding(
                identity,
                await self._onboarding.confirm_consent(identity),
            )
        if callback_data == "ob:v1:profile":
            return await self._render_onboarding(
                identity,
                await self._onboarding.start_profile(identity),
            )
        if callback_data == "ob:v1:goal:confirm":
            return await self._render_onboarding(
                identity,
                await self._onboarding.confirm_goal(identity),
            )
        if callback_data == "ob:v1:goal:add":
            return await self._render_onboarding(
                identity,
                await self._onboarding.add_to_goal(identity),
            )
        if callback_data == "ob:v1:goal:restart":
            return await self._render_onboarding(
                identity,
                await self._onboarding.restart_goal(identity),
            )
        if callback_data.startswith("ob:v1:goal:choice:"):
            return await self._render_onboarding(
                identity,
                await self._onboarding.choose_goal_clarification(
                    identity,
                    callback_data.removeprefix("ob:v1:goal:choice:"),
                ),
            )
        if callback_data.startswith("ob:v1:profile:gender:"):
            return await self._render_onboarding(
                identity,
                await self._onboarding.choose_gender(
                    identity,
                    callback_data.removeprefix("ob:v1:profile:gender:"),
                ),
            )
        if callback_data.startswith("ob:v1:equipment:"):
            choice = callback_data.removeprefix("ob:v1:equipment:")
            if choice not in {"all", "other"}:
                raise OnboardingApplicationError("invalid_action")
            return await self._render_onboarding(
                identity,
                await self._onboarding.choose_equipment(identity, choice),
            )
        if callback_data.startswith("ob:v1:health:"):
            choice = callback_data.removeprefix("ob:v1:health:")
            if choice not in {"none", "describe"}:
                raise OnboardingApplicationError("invalid_action")
            return await self._render_onboarding(
                identity,
                await self._onboarding.choose_health_limitations(identity, choice),
            )
        if callback_data.startswith("wf:v1:"):
            return await self._workout_feedback_callback(
                identity,
                callback_data.removeprefix("wf:v1:"),
            )
        if callback_data == "ob:v1:resume":
            return await self._render_onboarding(
                identity, await self._onboarding.snapshot(identity)
            )
        if callback_data == "ob:v1:restart":
            return await self._render_onboarding(
                identity, await self._onboarding.restart(identity)
            )
        if callback_data == "ob:v1:cancel":
            return TelegramResponse(
                messages.CANCEL_CONFIRM,
                keyboards.cancel_confirmation_keyboard(),
            )
        if callback_data == "ob:v1:cancel:confirm":
            return await self._render_onboarding(
                identity, await self._onboarding.cancel(identity)
            )
        if callback_data == "ob:v1:cancel:keep":
            return await self._render_onboarding(
                identity, await self._onboarding.snapshot(identity)
            )
        if callback_data == "acct:v1:delete:keep":
            return TelegramResponse(messages.ACCOUNT_KEPT)
        if callback_data == "acct:v1:delete:confirm":
            return await self._confirmed_delete(identity)

        if callback_data == "st:v1:disconnect":
            return TelegramResponse(
                messages.STRAVA_DISCONNECT_CONFIRM,
                keyboards.disconnect_confirmation_keyboard(),
            )
        if callback_data == "st:v1:disconnect:keep":
            return TelegramResponse(messages.STRAVA_KEPT)
        if callback_data == "st:v1:disconnect:confirm":
            return await self._confirmed_disconnect(identity)
        if callback_data == "st:v1:sync":
            return await self._manual_sync(identity)
        if callback_data == "st:v1:recalculate":
            return await self._recalculate(identity)
        if callback_data == "st:v1:status":
            return await self.strava(identity)

        if callback_data.startswith("menu:v1:"):
            return await self._menu_action(
                identity,
                callback_data.removeprefix("menu:v1:"),
            )
        raise OnboardingApplicationError("invalid_action")

    async def _navigation(
        self,
        identity: TelegramIdentity,
        screen: str,
    ) -> TelegramResponse:
        snapshot = await self._onboarding.snapshot(identity)
        if screen == "welcome":
            return self._welcome_response()
        if screen == "help":
            return TelegramResponse(
                messages.COACH_HELP,
                keyboards.information_keyboard(),
            )
        if screen == "privacy":
            return TelegramResponse(
                messages.PRIVACY_SAFETY,
                keyboards.information_keyboard(),
            )
        if screen == "consent":
            return await self._render_onboarding(identity, snapshot)
        raise OnboardingApplicationError("invalid_action")

    async def _confirmed_delete(
        self,
        identity: TelegramIdentity,
    ) -> TelegramResponse:
        user_id = await self._account_queries.resolve_user_id(identity)
        if user_id is None:
            return TelegramResponse(messages.NOT_FOUND)
        try:
            await self._strava.revoke_for_deletion(user_id=user_id)
        except StravaServiceError:
            logger.info(
                "External revocation unavailable during deletion user_id=%s",
                user_id,
            )
        deleted = await self._accounts.delete(user_id=user_id)
        return TelegramResponse(
            messages.DELETED if deleted else messages.DELETE_FAILED,
            clear_agent_thread=deleted,
        )

    async def _confirmed_disconnect(
        self,
        identity: TelegramIdentity,
    ) -> TelegramResponse:
        user_id = await self._require_strava_user_id(identity)
        outcome = await self._strava.disconnect(user_id=user_id, confirmed=True)
        text = (
            messages.STRAVA_DISCONNECTED
            if outcome.provider_revoked
            else messages.STRAVA_DISCONNECTED_LOCAL_ONLY
        )
        return TelegramResponse(text)

    async def _manual_sync(
        self,
        identity: TelegramIdentity,
    ) -> TelegramResponse:
        user_id = await self._require_strava_user_id(identity)
        outcome = await self._strava.manual_sync(user_id=user_id)
        return TelegramResponse(messages.strava_sync_outcome(outcome.status))

    async def _recalculate(
        self,
        identity: TelegramIdentity,
    ) -> TelegramResponse:
        user_id = await self._require_strava_user_id(identity)
        await self._strava.recalculate_baseline(user_id=user_id)
        return TelegramResponse(messages.RECALCULATION_COMPLETE)

    async def _menu_action(
        self,
        identity: TelegramIdentity,
        action: str,
    ) -> TelegramResponse:
        if action == "profile":
            return await self.profile(identity)
        if action == "baseline":
            return await self.baseline(identity)
        if action == "add_workout":
            return await self.add_workout(identity)
        if action == "strava":
            return await self.strava(identity)
        if action == "help":
            return TelegramResponse(messages.HELP)
        if action == "manual":
            return await self._select_pending_baseline_source(
                identity,
                BaselineSource.MANUAL,
            )
        if action == "home":
            lifecycle = await self._account_queries.lifecycle(identity)
            if lifecycle is None:
                return TelegramResponse(messages.NOT_FOUND)
            return await self._home_response(identity, lifecycle["status"])
        raise OnboardingApplicationError("invalid_action")

    async def _select_pending_baseline_source(
        self,
        identity: TelegramIdentity,
        source: BaselineSource,
    ) -> TelegramResponse:
        lifecycle = await self._account_queries.lifecycle(identity)
        if lifecycle is None:
            return TelegramResponse(messages.NOT_FOUND)
        await self._profiles.select_pending_baseline_source(
            user_id=cast(UUID, lifecycle["user_id"]),
            source=source,
        )
        home = await self._home_response(identity, UserStatus.BASELINE_PENDING)
        text = (
            messages.BASELINE_MANUAL_PENDING
            if source is BaselineSource.MANUAL
            else messages.BASELINE_CALIBRATION_PENDING
        )
        return TelegramResponse(text, home.keyboard)

    async def _render_import_outcome(
        self,
        identity: TelegramIdentity,
        outcome: TrainingFileImportOutcome,
    ) -> TelegramResponse:
        if outcome.file_format is TrainingFileFormat.APPLE_HEALTH_ZIP:
            text = messages.apple_health_file_result(
                activities_imported=outcome.activities_imported,
                activities_updated=outcome.activities_updated,
                activities_skipped=outcome.activities_skipped,
                baseline_limited=outcome.baseline_limited,
            )
        else:
            text = messages.tcx_workout_result(
                sport=outcome.sport,
                started_at=outcome.started_at,
                duration_seconds=outcome.duration_seconds or 0,
                distance_meters=outcome.distance_meters,
                average_heart_rate=outcome.average_heart_rate,
                baseline_limited=outcome.baseline_limited,
            )
        if (
            outcome.file_format is TrainingFileFormat.TCX
            and outcome.activity_id is not None
            and self._workout_feedback_enabled
            and self._workout_feedback is not None
        ):
            feedback = await self._workout_feedback.start_for_activity(
                user_id=await self._resolved_user_id(identity),
                activity_id=outcome.activity_id,
            )
            return await self._render_workout_feedback(
                identity,
                feedback,
                prefix=text,
            )
        if self._workout_feedback_enabled and self._workout_feedback is not None:
            waiting = await self._workout_feedback.snapshot(identity)
            if (
                waiting is not None
                and waiting.state is WorkoutFlowStep.WAITING_FOR_FILE
            ):
                await self._workout_feedback.cancel(identity)
        return await self._home_with_prefix(identity, text)

    async def _workout_feedback_callback(
        self,
        identity: TelegramIdentity,
        action: str,
    ) -> TelegramResponse:
        service = self._workout_feedback
        if service is None or not self._workout_feedback_enabled:
            raise WorkoutFeedbackError("workout_feedback_disabled")
        if action == "cancel":
            result = await service.cancel(identity)
        elif action.startswith("back:"):
            expected_state = self._workout_flow_step(action.removeprefix("back:"))
            result = await service.back(
                identity,
                expected_state=expected_state,
            )
        elif action == "hr:enter":
            result = await service.choose_manual_heart_rate(
                identity,
                enter=True,
            )
        elif action == "hr:skip":
            snapshot = await service.snapshot(identity)
            if snapshot is None:
                raise WorkoutFeedbackError("workout_flow_not_found")
            if snapshot.state is WorkoutFlowStep.HR_OFFER:
                result = await service.choose_manual_heart_rate(
                    identity,
                    enter=False,
                )
            else:
                result = await service.skip_manual_heart_rate(identity)
        elif action == "hr:confirm":
            result = await service.confirm_manual_heart_rate(identity)
        elif action == "hr:change":
            result = await service.change_manual_heart_rate(identity)
        elif action.startswith("rpe:"):
            value = action.removeprefix("rpe:")
            result = (
                await service.skip_rpe(identity)
                if value == "skip"
                else await service.select_rpe(identity, value)
            )
        elif action.startswith("mobility:"):
            value = action.removeprefix("mobility:")
            mobility_done = {"yes": True, "no": False, "skip": None}.get(value)
            if value not in {"yes", "no", "skip"}:
                raise WorkoutFeedbackError("invalid_action")
            result = await service.select_mobility(identity, mobility_done)
        elif action.startswith("discomfort:"):
            value = action.removeprefix("discomfort:")
            reported = {"yes": True, "no": False, "skip": None}.get(value)
            if value not in {"yes", "no", "skip"}:
                raise WorkoutFeedbackError("invalid_action")
            result = await service.select_discomfort(identity, reported)
        elif action.startswith("area:"):
            value = action.removeprefix("area:")
            result = await service.select_body_area(
                identity,
                None if value == "skip" else value,
            )
        elif action.startswith("description:"):
            value = action.removeprefix("description:")
            actions = {
                "confirm": service.confirm_discomfort_description,
                "change": service.change_discomfort_description,
                "skip": service.skip_discomfort_description,
            }
            try:
                operation = actions[value]
            except KeyError as exc:
                raise WorkoutFeedbackError("invalid_action") from exc
            result = await operation(identity)
        elif action.startswith("severity:"):
            value = action.removeprefix("severity:")
            result = await service.select_severity(
                identity,
                None if value == "skip" else value,
            )
        else:
            raise WorkoutFeedbackError("invalid_action")
        return await self._render_workout_feedback(identity, result)

    async def _render_workout_feedback(
        self,
        identity: TelegramIdentity,
        result: WorkoutFeedbackResult,
        *,
        prefix: str | None = None,
    ) -> TelegramResponse:
        text: str
        keyboard = None
        if result.state is WorkoutFlowStep.WAITING_FOR_FILE:
            text = messages.ADD_WORKOUT_REQUEST
            keyboard = keyboards.add_workout_keyboard()
        elif result.state is WorkoutFlowStep.HR_OFFER:
            text = messages.HEART_RATE_MISSING
            keyboard = keyboards.manual_heart_rate_offer_keyboard()
        elif result.state is WorkoutFlowStep.HR_ENTRY:
            text = messages.HEART_RATE_ENTRY
            keyboard = keyboards.feedback_text_entry_keyboard(
                state=WorkoutFlowStep.HR_ENTRY
            )
        elif result.state is WorkoutFlowStep.HR_CONFIRM:
            pending = result.pending_manual_average_heart_rate
            if pending is None:
                raise WorkoutFeedbackError("manual_heart_rate_missing")
            text = messages.manual_heart_rate_confirmation(pending)
            keyboard = keyboards.manual_heart_rate_confirmation_keyboard()
        elif result.state is WorkoutFlowStep.RPE:
            text = messages.RPE_QUESTION
            keyboard = keyboards.rpe_keyboard()
        elif result.state is WorkoutFlowStep.MOBILITY:
            text = messages.MOBILITY_QUESTION
            keyboard = keyboards.mobility_keyboard()
        elif result.state is WorkoutFlowStep.DISCOMFORT:
            text = messages.DISCOMFORT_QUESTION
            keyboard = keyboards.discomfort_keyboard()
        elif result.state is WorkoutFlowStep.BODY_AREA:
            text = messages.DISCOMFORT_AREA_QUESTION
            keyboard = keyboards.discomfort_area_keyboard()
        elif result.state is WorkoutFlowStep.DESCRIPTION_ENTRY:
            text = messages.DISCOMFORT_DESCRIPTION_REQUEST
            keyboard = keyboards.feedback_text_entry_keyboard(
                state=WorkoutFlowStep.DESCRIPTION_ENTRY
            )
        elif result.state is WorkoutFlowStep.DESCRIPTION_CONFIRM:
            description = result.pending_discomfort_description
            if description is None:
                raise WorkoutFeedbackError("discomfort_description_missing")
            text = messages.discomfort_description_confirmation(description)
            keyboard = keyboards.discomfort_description_confirmation_keyboard()
        elif result.state is WorkoutFlowStep.SEVERITY:
            text = messages.DISCOMFORT_SEVERITY_QUESTION
            keyboard = keyboards.discomfort_severity_keyboard()
        elif result.state is WorkoutFlowStep.CANCELLED:
            text = messages.WORKOUT_FEEDBACK_CANCELLED
        else:
            text = messages.WORKOUT_FEEDBACK_COMPLETE

        if prefix:
            text = f"{prefix}\n\n{text}"
        if result.state in {
            WorkoutFlowStep.COMPLETE,
            WorkoutFlowStep.CANCELLED,
        }:
            return await self._home_with_prefix(identity, text)
        return TelegramResponse(text, keyboard)

    async def _resolved_user_id(
        self,
        identity: TelegramIdentity,
    ) -> UUID:
        user_id = await self._account_queries.resolve_user_id(identity)
        if user_id is None:
            raise WorkoutFeedbackError("user_not_found")
        return user_id

    async def _home_with_prefix(
        self,
        identity: TelegramIdentity,
        prefix: str,
    ) -> TelegramResponse:
        lifecycle = await self._account_queries.lifecycle(identity)
        if lifecycle is None:
            return TelegramResponse(messages.NOT_FOUND)
        return await self._home_response(
            identity,
            lifecycle["status"],
            prefix=prefix,
        )

    async def _render_onboarding(
        self,
        identity: TelegramIdentity,
        result: OnboardingServiceResult,
    ) -> TelegramResponse:
        if result.created:
            return self._welcome_response()
        if result.kind == "setup_introduction":
            return TelegramResponse(
                messages.SETUP_INTRODUCTION,
                keyboards.setup_introduction_keyboard(),
            )
        if result.kind == "goal_intake":
            return TelegramResponse(
                messages.GOAL_INTAKE,
                keyboards.goal_input_keyboard(),
            )
        if result.kind == "goal_addition":
            return TelegramResponse(
                messages.GOAL_ADDITION,
                keyboards.goal_input_keyboard(),
            )
        if result.kind == "goal_off_topic":
            return TelegramResponse(
                messages.GOAL_OFF_TOPIC,
                keyboards.goal_input_keyboard(),
            )
        if result.kind == "goal_confirmation":
            return TelegramResponse(
                messages.goal_confirmation(result.answers),
                keyboards.goal_confirmation_keyboard(),
            )
        if result.kind == "goal_clarification":
            field = result.answers.get("_goal_clarification_field")
            keyboard = keyboards.goal_input_keyboard()
            if field == "main_goal":
                keyboard = keyboards.goal_main_clarification_keyboard()
            elif field == "event_date":
                keyboard = keyboards.goal_date_clarification_keyboard()
            return TelegramResponse(
                messages.goal_clarification(result.answers),
                keyboard,
            )
        if result.kind == "step":
            return TelegramResponse(
                messages.CONSENT,
                keyboards.consent_keyboard(),
            )
        if result.kind == "goal_confirmed":
            return TelegramResponse(
                messages.GOAL_SAVED,
                keyboards.goal_saved_keyboard(),
            )
        if result.kind == "profile_birth_year_intake":
            return TelegramResponse(
                messages.PROFILE_BIRTH_YEAR_INTAKE,
                keyboards.profile_text_input_keyboard(),
            )
        if result.kind == "profile_gender_intake":
            return TelegramResponse(
                messages.PROFILE_GENDER_INTAKE,
                keyboards.profile_gender_keyboard(),
            )
        if result.kind == "profile_weight_intake":
            return TelegramResponse(
                messages.PROFILE_WEIGHT_INTAKE,
                keyboards.profile_text_input_keyboard(),
            )
        if result.kind == "profile_height_intake":
            return TelegramResponse(
                messages.PROFILE_HEIGHT_INTAKE,
                keyboards.profile_text_input_keyboard(),
            )
        if result.kind == "availability_intake":
            return TelegramResponse(
                messages.AVAILABILITY_INTAKE,
                keyboards.profile_text_input_keyboard(),
            )
        if result.kind == "equipment_recommendation":
            recommendation = result.answers.get("equipment_recommendation_text")
            if (
                result.error_code is not None
                or result.answers.get("_context_retry_error") is not None
                or not isinstance(recommendation, str)
            ):
                return TelegramResponse(
                    messages.EQUIPMENT_RECOMMENDATION_RETRY,
                    keyboards.profile_text_input_keyboard(),
                )
            return TelegramResponse(
                messages.equipment_recommendation(recommendation),
                keyboards.equipment_intake_keyboard(),
            )
        if result.kind == "equipment_intake":
            recommendation = result.answers.get("equipment_recommendation_text")
            return TelegramResponse(
                messages.equipment_recommendation(
                    recommendation if isinstance(recommendation, str) else None
                ),
                keyboards.equipment_intake_keyboard(),
            )
        if result.kind == "equipment_details_intake":
            return TelegramResponse(
                messages.EQUIPMENT_DETAILS_INTAKE,
                keyboards.profile_text_input_keyboard(),
            )
        if result.kind == "health_limitations_intake":
            return TelegramResponse(
                messages.HEALTH_LIMITATIONS_INTAKE,
                keyboards.health_limitations_keyboard(),
            )
        if result.kind == "context_validation_error":
            prompt, context_keyboard = self._context_intake_prompt(result)
            return TelegramResponse(
                f"{messages.CONTEXT_VALIDATION_ERROR}\n\n{prompt}",
                context_keyboard,
            )
        if result.kind == "profile_validation_error":
            prompts = {
                OnboardingStep.PROFILE_BIRTH_YEAR_INTAKE: (
                    messages.PROFILE_BIRTH_YEAR_INTAKE
                ),
                OnboardingStep.PROFILE_WEIGHT_INTAKE: messages.PROFILE_WEIGHT_INTAKE,
                OnboardingStep.PROFILE_HEIGHT_INTAKE: messages.PROFILE_HEIGHT_INTAKE,
            }
            prompt = prompts.get(result.current_step, "")
            return TelegramResponse(
                f"{messages.validation_error(result.error_code or 'invalid_action')}"
                f"\n\n{prompt}",
                keyboards.profile_text_input_keyboard(),
            )
        if result.kind == "onboarding_completed":
            return TelegramResponse(messages.ONBOARDING_COMPLETED)
        if result.kind == "onboarding_modification":
            if result.updated_fields:
                return TelegramResponse(
                    messages.onboarding_fields_updated(result.updated_fields)
                )
            return TelegramResponse(messages.ONBOARDING_MODIFICATION_FALLBACK)
        if result.kind == "fallback":
            return TelegramResponse(
                messages.PARSE_FALLBACK,
                keyboards.goal_input_keyboard(),
            )
        if result.kind == "provider_error":
            return TelegramResponse(
                messages.PARSE_PROVIDER_ERROR,
                (
                    None
                    if result.onboarding_status is OnboardingStatus.COMPLETED
                    else keyboards.goal_input_keyboard()
                ),
            )
        if result.kind == "rate_limited":
            return TelegramResponse(
                messages.PARSE_RATE_LIMITED,
                (
                    None
                    if result.onboarding_status is OnboardingStatus.COMPLETED
                    else keyboards.goal_input_keyboard()
                ),
            )
        if result.kind == "cancelled":
            return TelegramResponse(
                messages.CANCELLED,
                keyboards.cancelled_keyboard(),
            )
        raise AssertionError(f"Unhandled onboarding result kind: {result.kind}")

    @staticmethod
    def _context_intake_prompt(
        result: OnboardingServiceResult,
    ) -> tuple[str, InlineKeyboardMarkup | None]:
        """Return the prompt and controls for a recoverable context-input error."""

        if result.current_step is OnboardingStep.AVAILABILITY_INTAKE:
            return (
                messages.AVAILABILITY_INTAKE,
                keyboards.profile_text_input_keyboard(),
            )
        if result.current_step is OnboardingStep.EQUIPMENT_DETAILS_INTAKE:
            return (
                messages.EQUIPMENT_DETAILS_INTAKE,
                keyboards.profile_text_input_keyboard(),
            )
        if result.current_step is OnboardingStep.HEALTH_LIMITATIONS_INTAKE:
            return (
                messages.HEALTH_LIMITATIONS_INTAKE,
                keyboards.health_limitations_keyboard(),
            )
        if result.current_step is OnboardingStep.EQUIPMENT_INTAKE:
            recommendation = result.answers.get("equipment_recommendation_text")
            return (
                messages.equipment_recommendation(
                    recommendation if isinstance(recommendation, str) else None
                ),
                keyboards.equipment_intake_keyboard(),
            )
        return (
            messages.CONTEXT_VALIDATION_ERROR,
            keyboards.profile_text_input_keyboard(),
        )

    @staticmethod
    def _welcome_response() -> TelegramResponse:
        return TelegramResponse(
            messages.WELCOME,
            keyboards.welcome_keyboard(),
        )

    async def _home_response(
        self,
        identity: TelegramIdentity,
        user_status: object,
        *,
        prefix: str | None = None,
    ) -> TelegramResponse:
        strava = await self._account_queries.strava(identity)
        connected = bool(strava and strava.get("connected"))
        syncing = self._sync_active(
            strava.get("sync_status") if strava is not None else None
        )
        if user_status is UserStatus.BASELINE_IMPORTING:
            state = "importing"
            text = messages.IMPORTING_MENU
        elif user_status is UserStatus.BASELINE_READY:
            state = "ready"
            text = messages.READY_MENU
        else:
            state = "setup"
            text = messages.BASELINE_SETUP_MENU
        if prefix:
            text = f"{prefix}\n\n{text}"
        return TelegramResponse(
            text,
            keyboards.state_menu(
                state,
                connected=connected,
                syncing=syncing,
                strava_enabled=self._strava_enabled,
            ),
        )

    @staticmethod
    def _workout_flow_step(value: str) -> WorkoutFlowStep:
        try:
            return WorkoutFlowStep(value.upper())
        except ValueError as exc:
            raise WorkoutFeedbackError("invalid_action") from exc

    @staticmethod
    def _split_callback(value: str, *, parts: int) -> tuple[str, ...]:
        split = tuple(value.split(":", maxsplit=parts - 1))
        if len(split) != parts or any(not item for item in split):
            raise OnboardingApplicationError("invalid_action")
        return split

    @staticmethod
    def _sync_active(status: object) -> bool:
        return status in {SyncStatus.REQUESTED, SyncStatus.RUNNING}

    async def _require_strava_user_id(
        self,
        identity: TelegramIdentity,
    ) -> UUID:
        lifecycle = await self._account_queries.lifecycle(identity)
        if lifecycle is None:
            raise OnboardingApplicationError("user_not_found")
        if not self._strava_allowed(lifecycle["status"]):
            raise OnboardingApplicationError("incomplete_profile")
        return cast(UUID, lifecycle["user_id"])

    @staticmethod
    def _strava_allowed(status: object) -> bool:
        return status in {
            UserStatus.ONBOARDING_COMPLETED,
            UserStatus.PROFILE_COMPLETED,
            UserStatus.BASELINE_PENDING,
            UserStatus.BASELINE_IMPORTING,
            UserStatus.BASELINE_READY,
            UserStatus.BASELINE_FAILED,
        }

    @staticmethod
    def _strava_error(code: str) -> str:
        if code == "strava_sync_in_progress":
            return messages.STRAVA_SYNC_CONCURRENT
        if code == "strava_sync_cooldown":
            return messages.STRAVA_SYNC_COOLDOWN
        if code in {
            "strava_credentials_missing",
            "encryption_key_missing",
            "strava_configuration_missing",
        }:
            return messages.STRAVA_CONFIGURATION_MISSING
        return messages.GENERIC_ERROR
