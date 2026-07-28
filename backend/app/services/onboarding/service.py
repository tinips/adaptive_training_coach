"""Stateful onboarding use cases backed by PostgreSQL sessions."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import cast

from pydantic import JsonValue, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.base import utc_now
from app.db.models import OnboardingSession, User
from app.domain.enums import (
    LLMUsageStatus,
    OnboardingStatus,
    OnboardingStep,
    UserStatus,
)
from app.repositories.llm_usage import LLMUsageRepository
from app.repositories.onboarding import OnboardingRepository
from app.repositories.users import UserRepository
from app.schemas.common import TelegramIdentity
from app.schemas.onboarding import (
    OnboardingParseResult,
    OnboardingTextParser,
    SummaryEditSection,
)
from app.schemas.onboarding_service import OnboardingServiceResult
from app.services.onboarding.state_machine import (
    OnboardingStateMachine,
    OnboardingStateMachineError,
    answer_key,
)

_DIRECT_TEXT_STEPS = {
    OnboardingStep.EVENT_NAME,
    OnboardingStep.EVENT_DATE,
    OnboardingStep.AGE,
    OnboardingStep.HEIGHT,
    OnboardingStep.WEIGHT,
}
_MULTI_STEPS = {
    OnboardingStep.TRAINING_DAYS,
    OnboardingStep.EQUIPMENT,
    OnboardingStep.POOL_ACCESS,
    OnboardingStep.BIKE_ACCESS,
    OnboardingStep.HEALTH_AREAS,
}
_MULTI_FREE_TEXT_STEPS = {
    OnboardingStep.EQUIPMENT,
    OnboardingStep.HEALTH_AREAS,
}
_EXPLICIT_FREE_TEXT_STEPS = {
    OnboardingStep.PRIMARY_SPORT,
    OnboardingStep.GOAL_TYPE,
    OnboardingStep.GOAL_PRIORITY,
    OnboardingStep.EQUIPMENT,
    OnboardingStep.HEALTH_AREAS,
    OnboardingStep.HEALTH_DESCRIPTION,
}
_PARSE_IN_FLIGHT_KEY = "_parse_in_flight"
_PARSE_IN_FLIGHT_TTL = timedelta(minutes=10)


class OnboardingApplicationError(RuntimeError):
    """Safe, code-bearing onboarding application failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class OnboardingService:
    """Coordinate deterministic state, optional graph parsing, and persistence."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        text_parser: OnboardingTextParser,
        settings: Settings,
    ) -> None:
        self._session_factory = session_factory
        self._text_parser = text_parser
        self._settings = settings

    async def start(
        self,
        identity: TelegramIdentity,
    ) -> OnboardingServiceResult:
        """Create or resume one user and one durable onboarding session."""

        async with self._session_factory.begin() as session:
            users = UserRepository(session)
            user, created = await users.get_or_create(
                telegram_user_id=identity.telegram_user_id,
                telegram_username=identity.telegram_username,
                first_name=identity.first_name,
                language_code=identity.language_code,
            )
            onboarding, onboarding_created = await OnboardingRepository(
                session
            ).get_or_create(user_id=user.id)
            if onboarding.status is OnboardingStatus.ACTIVE:
                user = await users.update_status(
                    user_id=user.id,
                    status=UserStatus.ONBOARDING_IN_PROGRESS,
                )
            return self._result(
                user,
                onboarding,
                created=created or onboarding_created,
            )

    async def restart(
        self,
        identity: TelegramIdentity,
    ) -> OnboardingServiceResult:
        async with self._session_factory.begin() as session:
            user, current = await self._locked_state(session, identity)
            if current.status is not OnboardingStatus.CANCELLED:
                raise OnboardingApplicationError("restart_not_allowed")
            onboarding = await OnboardingRepository(session).restart(user_id=user.id)
            user = await UserRepository(session).update_status(
                user_id=user.id,
                status=UserStatus.ONBOARDING_IN_PROGRESS,
            )
            return self._result(user, onboarding)

    async def choose(
        self,
        identity: TelegramIdentity,
        value: object,
        *,
        expected_step: OnboardingStep | None = None,
    ) -> OnboardingServiceResult:
        """Apply one predefined deterministic answer."""

        async with self._session_factory.begin() as session:
            user, onboarding = await self._locked_state(session, identity)
            self._require_active(onboarding)
            self._require_expected_step(onboarding, expected_step)
            self._require_no_pending(onboarding)
            try:
                transition = OnboardingStateMachine.advance(
                    current_step=onboarding.current_step,
                    answers=self._answers(onboarding),
                    value=value,
                    return_to_summary=onboarding.return_to_summary,
                )
            except OnboardingStateMachineError as exc:
                raise OnboardingApplicationError(exc.code) from exc
            onboarding = await OnboardingRepository(session).save_progress(
                user_id=user.id,
                current_step=transition.current_step,
                answers=cast(dict[str, object], transition.answers),
                return_to_summary=transition.return_to_summary,
            )
            return self._result(user, onboarding)

    async def skip(
        self,
        identity: TelegramIdentity,
        *,
        expected_step: OnboardingStep | None = None,
    ) -> OnboardingServiceResult:
        """Skip only an explicitly optional deterministic field."""

        async with self._session_factory() as session:
            user = await self._require_user(session, identity)
            onboarding = await OnboardingRepository(session).require_for_user(
                user_id=user.id
            )
            step = onboarding.current_step
        if expected_step is not None and step is not expected_step:
            raise OnboardingApplicationError("stale_action")
        if step not in {
            OnboardingStep.HEIGHT,
            OnboardingStep.WEIGHT,
            OnboardingStep.HEALTH_DESCRIPTION,
        }:
            raise OnboardingApplicationError("invalid_action")
        return await self.choose(identity, None, expected_step=step)

    async def toggle(
        self,
        identity: TelegramIdentity,
        option: object,
    ) -> OnboardingServiceResult:
        """Toggle a selection for non-Telegram callers and focused state tests."""

        return await self._update_multiselect(
            identity,
            option,
            selected=None,
            expected_step=None,
        )

    async def set_multiselect(
        self,
        identity: TelegramIdentity,
        option: object,
        *,
        selected: bool,
        expected_step: OnboardingStep,
    ) -> OnboardingServiceResult:
        """Set one selection idempotently for replay-safe Telegram callbacks."""

        return await self._update_multiselect(
            identity,
            option,
            selected=selected,
            expected_step=expected_step,
        )

    async def _update_multiselect(
        self,
        identity: TelegramIdentity,
        option: object,
        *,
        selected: bool | None,
        expected_step: OnboardingStep | None,
    ) -> OnboardingServiceResult:
        async with self._session_factory.begin() as session:
            user, onboarding = await self._locked_state(session, identity)
            self._require_active(onboarding)
            self._require_expected_step(onboarding, expected_step)
            self._require_no_pending(onboarding)
            step = onboarding.current_step
            if step not in _MULTI_STEPS:
                raise OnboardingApplicationError("invalid_action")
            answers = self._answers(onboarding)
            temporary_key = self._temporary_selection_key(step)
            current = self._current_selection(step, answers)
            already_selected = isinstance(option, str) and option in current
            if selected is not None and already_selected is selected:
                return self._result(user, onboarding)
            try:
                update = OnboardingStateMachine.toggle_multiselect(
                    step=step,
                    current_values=current,
                    option=option,
                )
            except OnboardingStateMachineError as exc:
                raise OnboardingApplicationError(exc.code) from exc
            answers[temporary_key] = cast(JsonValue, update.values)
            onboarding = await OnboardingRepository(session).save_progress(
                user_id=user.id,
                current_step=step,
                answers=cast(dict[str, object], answers),
            )
            return self._result(user, onboarding)

    async def continue_multiselect(
        self,
        identity: TelegramIdentity,
        *,
        expected_step: OnboardingStep | None = None,
    ) -> OnboardingServiceResult:
        """Confirm all current selections and advance once."""

        async with self._session_factory.begin() as session:
            user, onboarding = await self._locked_state(session, identity)
            self._require_active(onboarding)
            self._require_expected_step(onboarding, expected_step)
            self._require_no_pending(onboarding)
            step = onboarding.current_step
            if step not in _MULTI_STEPS:
                raise OnboardingApplicationError("invalid_action")
            answers = self._answers(onboarding)
            temporary_key = self._temporary_selection_key(step)
            selected = self._current_selection(step, answers)
            value: object = selected
            if (
                step
                in {
                    OnboardingStep.POOL_ACCESS,
                    OnboardingStep.BIKE_ACCESS,
                }
                and len(selected) == 1
                and selected[0]
                in {
                    "IRREGULAR",
                    "NO_REGULAR_ACCESS",
                }
            ):
                value = selected[0]
            try:
                transition = OnboardingStateMachine.advance(
                    current_step=step,
                    answers=answers,
                    value=value,
                    return_to_summary=onboarding.return_to_summary,
                )
            except OnboardingStateMachineError as exc:
                raise OnboardingApplicationError(exc.code) from exc
            next_answers = dict(transition.answers)
            next_answers.pop(temporary_key, None)
            onboarding = await OnboardingRepository(session).save_progress(
                user_id=user.id,
                current_step=transition.current_step,
                answers=cast(dict[str, object], next_answers),
                return_to_summary=transition.return_to_summary,
            )
            return self._result(user, onboarding)

    async def begin_free_text(
        self,
        identity: TelegramIdentity,
        *,
        expected_step: OnboardingStep | None = None,
    ) -> OnboardingServiceResult:
        """Persist entry into an explicit model-backed path without invoking it."""

        async with self._session_factory.begin() as session:
            user, onboarding = await self._locked_state(session, identity)
            self._require_active(onboarding)
            self._require_expected_step(onboarding, expected_step)
            if (
                onboarding.current_step not in _EXPLICIT_FREE_TEXT_STEPS
                or not OnboardingStateMachine.requires_free_text(
                    onboarding.current_step,
                    "OTHER",
                )
            ):
                raise OnboardingApplicationError("invalid_action")
            if onboarding.pending_free_text_step is onboarding.current_step:
                return self._result(user, onboarding)
            onboarding = await OnboardingRepository(session).begin_free_text(
                user_id=user.id,
                onboarding_step=onboarding.current_step,
            )
            return self._result(user, onboarding, kind="awaiting_text")

    async def handle_text(
        self,
        identity: TelegramIdentity,
        text: str,
    ) -> OnboardingServiceResult:
        """Parse deterministic text or invoke the graph for an explicit path."""

        async with self._session_factory() as session:
            user = await self._require_user(session, identity)
            onboarding = await OnboardingRepository(session).require_for_user(
                user_id=user.id
            )
            pending_step = onboarding.pending_free_text_step
            current_step = onboarding.current_step
        if pending_step is not None:
            return await self._parse_free_text(
                identity=identity,
                user_id=user.id,
                step=pending_step,
                text=text,
            )
        if current_step not in _DIRECT_TEXT_STEPS:
            raise OnboardingApplicationError("invalid_action")
        return await self.choose(identity, text)

    async def confirm_parsed(
        self,
        identity: TelegramIdentity,
    ) -> OnboardingServiceResult:
        """Confirm a safe pending parse, then stage or advance deterministically."""

        async with self._session_factory.begin() as session:
            user, onboarding = await self._locked_state(session, identity)
            self._require_active(onboarding)
            if (
                onboarding.pending_free_text_step is None
                or onboarding.pending_parsed_value is None
                or onboarding.pending_free_text_step is not onboarding.current_step
            ):
                raise OnboardingApplicationError("parsed_value_missing")
            try:
                parsed = OnboardingParseResult.model_validate(
                    onboarding.pending_parsed_value
                )
            except ValidationError as exc:
                raise OnboardingApplicationError("parsed_value_invalid") from exc
            if parsed.normalized_value is None:
                raise OnboardingApplicationError("parsed_value_invalid")

            step = onboarding.current_step
            answers = self._answers(onboarding)
            self._preserve_other_display(step, answers, parsed)
            repository = OnboardingRepository(session)
            if step in _MULTI_FREE_TEXT_STEPS:
                temporary_key = self._temporary_selection_key(step)
                selected = self._current_selection(step, answers)
                parsed_values = (
                    parsed.normalized_value
                    if isinstance(parsed.normalized_value, list)
                    else [parsed.normalized_value]
                )
                for value in parsed_values:
                    if isinstance(value, str) and value not in selected:
                        selected.append(value)
                answers[temporary_key] = cast(JsonValue, selected)
                await repository.clear_pending_parse(user_id=user.id)
                onboarding = await repository.save_progress(
                    user_id=user.id,
                    current_step=step,
                    answers=cast(dict[str, object], answers),
                )
                return self._result(user, onboarding)

            try:
                transition = OnboardingStateMachine.advance(
                    current_step=step,
                    answers=answers,
                    value=parsed.normalized_value,
                    return_to_summary=onboarding.return_to_summary,
                )
            except OnboardingStateMachineError as exc:
                raise OnboardingApplicationError("parsed_value_invalid") from exc
            await repository.clear_pending_parse(user_id=user.id)
            onboarding = await repository.save_progress(
                user_id=user.id,
                current_step=transition.current_step,
                answers=cast(dict[str, object], transition.answers),
                return_to_summary=transition.return_to_summary,
            )
            return self._result(user, onboarding)

    async def retry_parsed(
        self,
        identity: TelegramIdentity,
    ) -> OnboardingServiceResult:
        """Discard the interpretation but keep awaiting explicit new text."""

        async with self._session_factory.begin() as session:
            user, onboarding = await self._locked_state(session, identity)
            self._require_active(onboarding)
            if onboarding.pending_free_text_step is None:
                raise OnboardingApplicationError("stale_action")
            if self._parse_is_in_flight(onboarding):
                raise OnboardingApplicationError("parse_in_progress")
            step = onboarding.pending_free_text_step
            onboarding = await OnboardingRepository(session).begin_free_text(
                user_id=user.id,
                onboarding_step=step,
            )
            return self._result(user, onboarding, kind="awaiting_text")

    async def back_to_options(
        self,
        identity: TelegramIdentity,
    ) -> OnboardingServiceResult:
        """Discard all pending interpretation state."""

        async with self._session_factory.begin() as session:
            user, onboarding = await self._locked_state(session, identity)
            self._require_active(onboarding)
            if onboarding.pending_free_text_step is None:
                raise OnboardingApplicationError("stale_action")
            if self._parse_is_in_flight(onboarding):
                raise OnboardingApplicationError("parse_in_progress")
            answers = self._answers(onboarding)
            answers.pop(_PARSE_IN_FLIGHT_KEY, None)
            repository = OnboardingRepository(session)
            await repository.save_progress(
                user_id=user.id,
                current_step=onboarding.current_step,
                answers=cast(dict[str, object], answers),
            )
            onboarding = await repository.clear_pending_parse(user_id=user.id)
            return self._result(user, onboarding)

    async def begin_summary_edit(
        self,
        identity: TelegramIdentity,
        section: SummaryEditSection,
    ) -> OnboardingServiceResult:
        async with self._session_factory.begin() as session:
            user, onboarding = await self._locked_state(session, identity)
            self._require_active(onboarding)
            self._require_no_pending(onboarding)
            if onboarding.current_step is not OnboardingStep.SUMMARY:
                raise OnboardingApplicationError("invalid_action")
            transition = OnboardingStateMachine.begin_summary_edit(
                section=section,
                answers=self._answers(onboarding),
            )
            onboarding = await OnboardingRepository(session).save_progress(
                user_id=user.id,
                current_step=transition.current_step,
                answers=cast(dict[str, object], transition.answers),
                return_to_summary=True,
            )
            return self._result(user, onboarding)

    async def back(
        self,
        identity: TelegramIdentity,
        *,
        expected_step: OnboardingStep | None = None,
    ) -> OnboardingServiceResult:
        async with self._session_factory.begin() as session:
            user, onboarding = await self._locked_state(session, identity)
            self._require_active(onboarding)
            self._require_expected_step(onboarding, expected_step)
            self._require_no_pending(onboarding)
            try:
                transition = OnboardingStateMachine.back(
                    current_step=onboarding.current_step,
                    answers=self._answers(onboarding),
                    return_to_summary=onboarding.return_to_summary,
                )
            except OnboardingStateMachineError as exc:
                raise OnboardingApplicationError(exc.code) from exc
            onboarding = await OnboardingRepository(session).save_progress(
                user_id=user.id,
                current_step=transition.current_step,
                answers=cast(dict[str, object], transition.answers),
                return_to_summary=transition.return_to_summary,
            )
            return self._result(user, onboarding)

    async def apple_action(
        self,
        identity: TelegramIdentity,
        action: str,
    ) -> OnboardingServiceResult:
        """Apply a deterministic Apple Health onboarding transition."""

        async with self._session_factory.begin() as session:
            user, onboarding = await self._locked_state(session, identity)
            self._require_active(onboarding)
            self._require_no_pending(onboarding)
            current = onboarding.current_step
            transitions = {
                (
                    OnboardingStep.APPLE_HEALTH_PRIVACY_NOTICE,
                    "continue",
                ): OnboardingStep.APPLE_HEALTH_WAITING_FOR_FILE,
                (
                    OnboardingStep.APPLE_HEALTH_PRIVACY_NOTICE,
                    "back",
                ): OnboardingStep.BASELINE_SOURCE,
                (
                    OnboardingStep.APPLE_HEALTH_WAITING_FOR_FILE,
                    "back",
                ): OnboardingStep.APPLE_HEALTH_PRIVACY_NOTICE,
                (
                    OnboardingStep.APPLE_HEALTH_WAITING_FOR_FILE,
                    "cancel",
                ): OnboardingStep.BASELINE_SOURCE,
                (
                    OnboardingStep.APPLE_HEALTH_IMPORT_FAILED,
                    "retry",
                ): OnboardingStep.APPLE_HEALTH_WAITING_FOR_FILE,
                (
                    OnboardingStep.APPLE_HEALTH_IMPORT_FAILED,
                    "back",
                ): OnboardingStep.BASELINE_SOURCE,
                (
                    OnboardingStep.APPLE_HEALTH_IMPORT_COMPLETE,
                    "continue",
                ): OnboardingStep.SUMMARY,
            }
            next_step: OnboardingStep | None
            if action == "choose_other" and current in {
                OnboardingStep.APPLE_HEALTH_PRIVACY_NOTICE,
                OnboardingStep.APPLE_HEALTH_WAITING_FOR_FILE,
                OnboardingStep.APPLE_HEALTH_IMPORT_FAILED,
            }:
                next_step = OnboardingStep.BASELINE_SOURCE
            else:
                next_step = transitions.get((current, action))
            if next_step is None:
                raise OnboardingApplicationError("invalid_action")
            answers = self._answers(onboarding)
            if next_step is OnboardingStep.BASELINE_SOURCE:
                answers.pop(answer_key(OnboardingStep.BASELINE_SOURCE), None)
            onboarding = await OnboardingRepository(session).save_progress(
                user_id=user.id,
                current_step=next_step,
                answers=cast(dict[str, object], answers),
                return_to_summary=(
                    False
                    if next_step is OnboardingStep.SUMMARY
                    else onboarding.return_to_summary
                ),
            )
            return self._result(user, onboarding)

    async def cancel(
        self,
        identity: TelegramIdentity,
    ) -> OnboardingServiceResult:
        async with self._session_factory.begin() as session:
            user, onboarding = await self._locked_state(session, identity)
            self._require_active(onboarding)
            onboarding = await OnboardingRepository(session).cancel(user_id=user.id)
            return self._result(user, onboarding, kind="cancelled")

    async def snapshot(
        self,
        identity: TelegramIdentity,
    ) -> OnboardingServiceResult:
        async with self._session_factory() as session:
            user = await self._require_user(session, identity)
            onboarding = await OnboardingRepository(session).require_for_user(
                user_id=user.id
            )
            return self._result(user, onboarding)

    async def _parse_free_text(
        self,
        *,
        identity: TelegramIdentity,
        user_id: uuid.UUID,
        step: OnboardingStep,
        text: str,
    ) -> OnboardingServiceResult:
        confirmed_context: dict[str, object]
        parse_run_id = str(uuid.uuid4())
        async with self._session_factory.begin() as session:
            user, onboarding = await self._locked_state(session, identity)
            self._require_active(onboarding)
            if user.id != user_id:
                raise OnboardingApplicationError("user_not_found")
            if (
                onboarding.pending_free_text_step is not step
                or onboarding.current_step is not step
            ):
                raise OnboardingApplicationError("stale_action")
            if self._parse_is_in_flight(onboarding):
                raise OnboardingApplicationError("parse_in_progress")
            confirmed_context = dict(onboarding.answers)
            if self._settings.llm_mode == "live":
                attempts = await LLMUsageRepository(session).count_since(
                    user_id=user_id,
                    since=utc_now() - timedelta(hours=1),
                    provider_mode="live",
                )
                if attempts >= self._settings.llm_other_requests_per_hour:
                    return self._result(
                        user,
                        onboarding,
                        kind="rate_limited",
                        error_code="llm_rate_limited",
                    )
            answers = self._answers(onboarding)
            answers[_PARSE_IN_FLIGHT_KEY] = cast(
                JsonValue,
                {
                    "run_id": parse_run_id,
                    "started_at": utc_now().isoformat(),
                },
            )
            await OnboardingRepository(session).save_progress(
                user_id=user_id,
                current_step=step,
                answers=cast(dict[str, object], answers),
            )
            usage = await LLMUsageRepository(session).record(
                user_id=user_id,
                onboarding_step=step,
                provider_mode=self._settings.llm_mode,
                model=self._settings.llm_model,
                status=LLMUsageStatus.PROVIDER_ERROR,
            )
            usage_id = usage.id

        try:
            workflow = await self._text_parser.parse(
                user_id=user_id,
                step=step,
                user_text=text,
                confirmed_context=confirmed_context,
            )
        except Exception:
            async with self._session_factory.begin() as session:
                user, onboarding = await self._locked_state(session, identity)
                if self._owns_parse_run(onboarding, parse_run_id):
                    await LLMUsageRepository(session).update_outcome(
                        user_id=user.id,
                        usage_id=usage_id,
                        status=LLMUsageStatus.PROVIDER_ERROR,
                        prompt_tokens=None,
                        completion_tokens=None,
                    )
                    answers = self._answers(onboarding)
                    answers.pop(_PARSE_IN_FLIGHT_KEY, None)
                    repository = OnboardingRepository(session)
                    await repository.save_progress(
                        user_id=user.id,
                        current_step=onboarding.current_step,
                        answers=cast(dict[str, object], answers),
                    )
                    onboarding = await repository.begin_free_text(
                        user_id=user.id,
                        onboarding_step=step,
                    )
                    return self._result(
                        user,
                        onboarding,
                        kind="provider_error",
                        error_code="llm_provider_error",
                    )
            raise OnboardingApplicationError("stale_action") from None

        async with self._session_factory.begin() as session:
            user, onboarding = await self._locked_state(session, identity)
            if (
                onboarding.pending_free_text_step is not step
                or onboarding.current_step is not step
                or not self._owns_parse_run(onboarding, parse_run_id)
            ):
                raise OnboardingApplicationError("stale_action")
            usage_status = {
                "confirmation_required": LLMUsageStatus.SUCCEEDED,
                "clarification_required": LLMUsageStatus.CLARIFICATION,
                "fallback_required": LLMUsageStatus.FALLBACK,
                "provider_error": LLMUsageStatus.PROVIDER_ERROR,
            }[workflow.outcome]
            await LLMUsageRepository(session).update_outcome(
                user_id=user.id,
                usage_id=usage_id,
                status=usage_status,
                prompt_tokens=workflow.prompt_tokens,
                completion_tokens=workflow.completion_tokens,
            )
            repository = OnboardingRepository(session)
            answers = self._answers(onboarding)
            answers.pop(_PARSE_IN_FLIGHT_KEY, None)
            await repository.save_progress(
                user_id=user.id,
                current_step=step,
                answers=cast(dict[str, object], answers),
            )
            if (
                workflow.outcome == "confirmation_required"
                and workflow.parse_result is not None
            ):
                onboarding = await repository.set_pending_parse(
                    user_id=user.id,
                    onboarding_step=step,
                    parsed_value=workflow.parse_result.model_dump(mode="json"),
                )
                return self._result(
                    user,
                    onboarding,
                    kind="interpretation",
                    parse_result=workflow.parse_result,
                )

            onboarding = await repository.begin_free_text(
                user_id=user.id,
                onboarding_step=step,
            )
            kind = {
                "clarification_required": "clarification",
                "fallback_required": "fallback",
                "provider_error": "provider_error",
            }[workflow.outcome]
            return self._result(
                user,
                onboarding,
                kind=kind,
                clarification_question=(
                    workflow.parse_result.clarification_question
                    if workflow.parse_result is not None
                    else None
                ),
                error_code=workflow.error_code,
            )

    @staticmethod
    async def _require_user(
        session: AsyncSession,
        identity: TelegramIdentity,
    ) -> User:
        user = await UserRepository(session).get_by_telegram_id(
            identity.telegram_user_id
        )
        if user is None:
            raise OnboardingApplicationError("user_not_found")
        return user

    async def _locked_state(
        self,
        session: AsyncSession,
        identity: TelegramIdentity,
    ) -> tuple[User, OnboardingSession]:
        user = await self._require_user(session, identity)
        onboarding = await OnboardingRepository(session).lock_for_user(user_id=user.id)
        return user, onboarding

    @staticmethod
    def _require_active(onboarding: OnboardingSession) -> None:
        if onboarding.status is not OnboardingStatus.ACTIVE:
            raise OnboardingApplicationError("onboarding_not_active")

    @staticmethod
    def _require_expected_step(
        onboarding: OnboardingSession,
        expected_step: OnboardingStep | None,
    ) -> None:
        if expected_step is not None and onboarding.current_step is not expected_step:
            raise OnboardingApplicationError("stale_action")

    @staticmethod
    def _require_no_pending(onboarding: OnboardingSession) -> None:
        if onboarding.pending_free_text_step is not None:
            raise OnboardingApplicationError("stale_action")

    @staticmethod
    def _parse_is_in_flight(onboarding: OnboardingSession) -> bool:
        marker = dict(onboarding.answers).get(_PARSE_IN_FLIGHT_KEY)
        if not isinstance(marker, dict):
            return False
        started_at = marker.get("started_at")
        if not isinstance(started_at, str):
            return True
        try:
            started = datetime.fromisoformat(started_at)
        except ValueError:
            return True
        return utc_now() - started < _PARSE_IN_FLIGHT_TTL

    @staticmethod
    def _owns_parse_run(
        onboarding: OnboardingSession,
        parse_run_id: str,
    ) -> bool:
        marker = dict(onboarding.answers).get(_PARSE_IN_FLIGHT_KEY)
        return isinstance(marker, dict) and marker.get("run_id") == parse_run_id

    @staticmethod
    def _answers(
        onboarding: OnboardingSession,
    ) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], dict(onboarding.answers))

    @classmethod
    def _current_selection(
        cls,
        step: OnboardingStep,
        answers: dict[str, JsonValue],
    ) -> list[str]:
        temporary = answers.get(cls._temporary_selection_key(step))
        if isinstance(temporary, list):
            return [str(value) for value in temporary]
        existing = answers.get(answer_key(step))
        if isinstance(existing, list):
            return [str(value) for value in existing]
        if isinstance(existing, dict):
            access_type = existing.get("type")
            days = existing.get("days")
            if access_type in {"IRREGULAR", "NO_REGULAR_ACCESS"}:
                return [str(access_type)]
            if isinstance(days, list):
                return [str(value) for value in days]
        return []

    @staticmethod
    def _temporary_selection_key(step: OnboardingStep) -> str:
        return f"_selection_{answer_key(step)}"

    @staticmethod
    def _preserve_other_display(
        step: OnboardingStep,
        answers: dict[str, JsonValue],
        parsed: OnboardingParseResult,
    ) -> None:
        normalized = parsed.normalized_value
        contains_other = normalized == "OTHER" or (
            isinstance(normalized, list) and "OTHER" in normalized
        )
        if contains_other and parsed.display_value:
            answers[f"{answer_key(step)}_other_description"] = parsed.display_value

    @classmethod
    def _result(
        cls,
        user: User,
        onboarding: OnboardingSession,
        *,
        kind: str | None = None,
        parse_result: OnboardingParseResult | None = None,
        clarification_question: str | None = None,
        error_code: str | None = None,
        created: bool = False,
    ) -> OnboardingServiceResult:
        if kind is None:
            if onboarding.status is OnboardingStatus.CANCELLED:
                kind = "cancelled"
            elif onboarding.status is OnboardingStatus.COMPLETED:
                kind = "completed"
            elif onboarding.pending_parsed_value:
                kind = "interpretation"
            elif onboarding.pending_free_text_step is not None:
                kind = "awaiting_text"
            elif onboarding.current_step is OnboardingStep.SUMMARY:
                kind = "summary"
            else:
                kind = "step"
        if parse_result is None and onboarding.pending_parsed_value:
            try:
                parse_result = OnboardingParseResult.model_validate(
                    onboarding.pending_parsed_value
                )
            except ValidationError:
                parse_result = None
        return OnboardingServiceResult(
            kind=cast(
                object,
                kind,
            ),  # runtime validation ensures only declared result kinds
            user_id=user.id,
            user_status=user.status,
            onboarding_status=onboarding.status,
            current_step=onboarding.current_step,
            answers=cls._answers(onboarding),
            parse_result=parse_result,
            clarification_question=clarification_question,
            error_code=error_code,
            created=created,
        )
