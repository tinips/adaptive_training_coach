"""Concrete application facade consumed by thin Telegram handlers."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path
from typing import Protocol, cast
from uuid import UUID

from app.bot import keyboards, messages
from app.bot.rendering import TelegramResponse
from app.domain.enums import (
    AppleHealthImportStatus,
    BaselineSource,
    OnboardingStep,
    SyncStatus,
    UserStatus,
)
from app.schemas.common import TelegramIdentity
from app.schemas.onboarding import SummaryEditSection
from app.schemas.onboarding_service import OnboardingServiceResult
from app.services.accounts import AccountQueryService, AccountService
from app.services.apple_health import (
    AppleHealthImportOutcome,
    TelegramDocumentUpload,
)
from app.services.onboarding import OnboardingApplicationError, OnboardingService
from app.services.profiles import (
    BaselineSelectionUnavailableError,
    IncompleteProfileError,
    ProfileService,
)
from app.services.strava.disconnect import DisconnectOutcome
from app.services.strava.exceptions import StravaServiceError
from app.services.strava.sync import SyncOutcome

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


class AppleHealthBotPort(Protocol):
    """Apple Health orchestration used by document and resume actions."""

    async def process_upload(
        self,
        *,
        identity: TelegramIdentity,
        document: TelegramDocumentUpload,
        download: Callable[[Path], Awaitable[None]],
        progress: Callable[[str], Awaitable[None]],
    ) -> AppleHealthImportOutcome: ...

    async def latest_outcome(
        self,
        *,
        user_id: UUID,
    ) -> AppleHealthImportOutcome | None: ...

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
        apple_health: AppleHealthBotPort | None = None,
        strava_enabled: bool = False,
        apple_health_enabled: bool = True,
    ) -> None:
        self._onboarding = onboarding
        self._profiles = profiles
        self._account_queries = account_queries
        self._accounts = accounts
        self._strava = strava
        self._apple_health = apple_health
        self._strava_enabled = strava_enabled
        self._apple_health_enabled = apple_health_enabled

    async def start(self, identity: TelegramIdentity) -> TelegramResponse:
        result = await self._onboarding.start(identity)
        return await self._render_onboarding(identity, result)

    async def handle_text(
        self,
        identity: TelegramIdentity,
        text: str,
    ) -> TelegramResponse:
        try:
            result = await self._onboarding.handle_text(identity, text)
        except OnboardingApplicationError as exc:
            return TelegramResponse(messages.validation_error(exc.code))
        return await self._render_onboarding(identity, result)

    async def handle_document(
        self,
        identity: TelegramIdentity,
        document: TelegramDocumentUpload,
        download: Callable[[Path], Awaitable[None]],
        progress: Callable[[str], Awaitable[None]],
    ) -> TelegramResponse:
        if not self._apple_health_enabled or self._apple_health is None:
            return TelegramResponse(
                messages.validation_error("apple_health_import_disabled")
            )
        try:
            outcome = await self._apple_health.process_upload(
                identity=identity,
                document=document,
                download=download,
                progress=progress,
            )
        except OnboardingApplicationError as exc:
            return TelegramResponse(messages.validation_error(exc.code))
        if outcome.status is AppleHealthImportStatus.FAILED:
            snapshot = await self._onboarding.snapshot(identity)
            return await self._render_onboarding(identity, snapshot)
        snapshot = await self._onboarding.snapshot(identity)
        return await self._render_onboarding(identity, snapshot)

    async def handle_callback(
        self,
        identity: TelegramIdentity,
        callback_data: str,
    ) -> TelegramResponse:
        try:
            response = await self._route_callback(identity, callback_data)
        except OnboardingApplicationError as exc:
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
        if snapshot.kind == "completed":
            return TelegramResponse(messages.PROFILE_ALREADY_COMPLETE)
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
        if callback_data.startswith("ob:v1:apple:"):
            action = callback_data.removeprefix("ob:v1:apple:")
            if action in {"cancel", "choose_other", "back"}:
                snapshot = await self._onboarding.snapshot(identity)
                if self._apple_health is not None:
                    await self._apple_health.cancel_active(user_id=snapshot.user_id)
            return await self._render_onboarding(
                identity,
                await self._onboarding.apple_action(identity, action),
            )
        if callback_data.startswith("ob:v1:set:"):
            payload = callback_data.removeprefix("ob:v1:set:")
            step_value, value = self._split_callback(payload, parts=2)
            step = self._onboarding_step(step_value)
            return await self._render_onboarding(
                identity,
                await self._onboarding.choose(
                    identity,
                    value,
                    expected_step=step,
                ),
            )
        if callback_data.startswith("ob:v1:multi:"):
            payload = callback_data.removeprefix("ob:v1:multi:")
            action, step_value, value = self._split_callback(payload, parts=3)
            if action not in {"add", "remove"}:
                raise OnboardingApplicationError("invalid_action")
            step = self._onboarding_step(step_value)
            return await self._render_onboarding(
                identity,
                await self._onboarding.set_multiselect(
                    identity,
                    value,
                    selected=action == "add",
                    expected_step=step,
                ),
            )
        if callback_data.startswith("ob:v1:continue:"):
            step = self._onboarding_step(callback_data.removeprefix("ob:v1:continue:"))
            return await self._render_onboarding(
                identity,
                await self._onboarding.continue_multiselect(
                    identity,
                    expected_step=step,
                ),
            )
        if callback_data.startswith("ob:v1:skip:"):
            step = self._onboarding_step(callback_data.removeprefix("ob:v1:skip:"))
            return await self._render_onboarding(
                identity, await self._onboarding.skip(identity, expected_step=step)
            )
        if callback_data.startswith("ob:v1:back:"):
            step = self._onboarding_step(callback_data.removeprefix("ob:v1:back:"))
            return await self._render_onboarding(
                identity, await self._onboarding.back(identity, expected_step=step)
            )
        if callback_data.startswith("ob:v1:other:"):
            step = self._onboarding_step(callback_data.removeprefix("ob:v1:other:"))
            return await self._render_onboarding(
                identity,
                await self._onboarding.begin_free_text(
                    identity,
                    expected_step=step,
                ),
            )
        if callback_data == "ob:v1:parsed:confirm":
            return await self._render_onboarding(
                identity, await self._onboarding.confirm_parsed(identity)
            )
        if callback_data == "ob:v1:parsed:retry":
            return await self._render_onboarding(
                identity, await self._onboarding.retry_parsed(identity)
            )
        if callback_data == "ob:v1:parsed:back":
            return await self._render_onboarding(
                identity, await self._onboarding.back_to_options(identity)
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
        if callback_data.startswith("ob:v1:edit:"):
            section = self._edit_section(callback_data.removeprefix("ob:v1:edit:"))
            return await self._render_onboarding(
                identity, await self._onboarding.begin_summary_edit(identity, section)
            )
        if callback_data == "ob:v1:summary:confirm":
            snapshot = await self._onboarding.snapshot(identity)
            await self._profiles.finalize(user_id=snapshot.user_id)
            completed = await self._onboarding.snapshot(identity)
            return await self._render_onboarding(identity, completed)

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
        return TelegramResponse(messages.DELETED if deleted else messages.DELETE_FAILED)

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

    async def _render_onboarding(
        self,
        identity: TelegramIdentity,
        result: OnboardingServiceResult,
    ) -> TelegramResponse:
        prefix = f"{messages.WELCOME_NEW}\n\n" if result.created else ""
        if result.kind == "step":
            if result.current_step is OnboardingStep.APPLE_HEALTH_IMPORT_COMPLETE:
                outcome = (
                    await self._apple_health.latest_outcome(user_id=result.user_id)
                    if self._apple_health is not None
                    else None
                )
                if outcome is not None:
                    return TelegramResponse(
                        messages.apple_health_import_success(
                            workouts_found=outcome.workouts_found,
                            activities_imported=outcome.activities_imported,
                            activities_updated=outcome.activities_updated,
                            activities_skipped=outcome.activities_skipped,
                            heart_rate_records_matched=(
                                outcome.heart_rate_records_matched
                            ),
                            warning_count=outcome.warning_count,
                            discipline_counts=outcome.discipline_counts or {},
                        ),
                        keyboards.keyboard_for_step(
                            result.current_step,
                            result.answers,
                            strava_enabled=self._strava_enabled,
                            apple_health_enabled=self._apple_health_enabled,
                        ),
                    )
            text = messages.step_prompt(result.current_step)
            if result.current_step in {
                OnboardingStep.TRAINING_DAYS,
                OnboardingStep.EQUIPMENT,
                OnboardingStep.POOL_ACCESS,
                OnboardingStep.BIKE_ACCESS,
                OnboardingStep.HEALTH_AREAS,
            }:
                text = f"{text}\n\n{messages.selected_values(self._selection(result))}"
            return TelegramResponse(
                prefix + text,
                keyboards.keyboard_for_step(
                    result.current_step,
                    result.answers,
                    strava_enabled=self._strava_enabled,
                    apple_health_enabled=self._apple_health_enabled,
                ),
            )
        if result.kind == "summary":
            return TelegramResponse(
                prefix + messages.onboarding_summary(result.answers),
                keyboards.summary_keyboard(),
            )
        if result.kind == "awaiting_text":
            text = (
                messages.HEALTH_FREE_TEXT_NOTICE
                if result.current_step is OnboardingStep.HEALTH_DESCRIPTION
                else messages.FREE_TEXT_REQUEST
            )
            return TelegramResponse(prefix + text)
        if result.kind == "interpretation" and result.parse_result is not None:
            return TelegramResponse(
                messages.interpreted_answer(
                    result.parse_result.display_value or messages.PARSE_DISPLAY_MISSING
                ),
                keyboards.parsed_confirmation_keyboard(),
            )
        if result.kind == "clarification":
            return TelegramResponse(
                messages.clarification(result.clarification_question),
                keyboards.free_text_recovery_keyboard(),
            )
        if result.kind == "fallback":
            return TelegramResponse(
                messages.PARSE_FALLBACK,
                keyboards.free_text_recovery_keyboard(),
            )
        if result.kind == "provider_error":
            return TelegramResponse(
                messages.PARSE_PROVIDER_ERROR,
                keyboards.free_text_recovery_keyboard(),
            )
        if result.kind == "rate_limited":
            return TelegramResponse(
                messages.PARSE_RATE_LIMITED,
                keyboards.free_text_recovery_keyboard(),
            )
        if result.kind == "cancelled":
            return TelegramResponse(
                messages.CANCELLED,
                keyboards.resume_keyboard(cancelled=True),
            )
        if result.kind == "completed":
            return await self._home_response(
                identity,
                result.user_status,
                prefix=messages.ONBOARDING_COMPLETE,
            )
        return TelegramResponse(messages.GENERIC_ERROR)

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
    def _selection(result: OnboardingServiceResult) -> list[str]:
        key = result.current_step.value.lower()
        value = result.answers.get(
            f"_selection_{key}",
            result.answers.get(key),
        )
        if isinstance(value, list):
            return [str(item) for item in value]
        if isinstance(value, dict):
            if value.get("type") in {"IRREGULAR", "NO_REGULAR_ACCESS"}:
                return [str(value["type"])]
            days = value.get("days")
            if isinstance(days, list):
                return [str(item) for item in days]
        return []

    @staticmethod
    def _edit_section(value: str) -> SummaryEditSection:
        mapping: dict[str, SummaryEditSection] = {
            "goal": "goal",
            "availability": "availability",
            "equipment": "equipment",
            "limitations": "limitations",
            "coach": "coach_style",
            "baseline": "baseline",
        }
        try:
            return mapping[value]
        except KeyError as exc:
            raise OnboardingApplicationError("invalid_action") from exc

    @staticmethod
    def _onboarding_step(value: str) -> OnboardingStep:
        try:
            return OnboardingStep(value)
        except ValueError as exc:
            raise OnboardingApplicationError("invalid_action") from exc

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
