"""Focused, durable onboarding through explicit conversational-goal confirmation."""

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
from app.integrations.llm.models import (
    GoalExtractionAction,
    GoalExtractionOutput,
    GoalExtractionPatch,
    GoalFieldName,
)
from app.repositories.llm_usage import LLMUsageRepository
from app.repositories.onboarding import OnboardingRepository
from app.repositories.profiles import ProfileRepository
from app.repositories.users import UserRepository
from app.schemas.common import TelegramIdentity
from app.schemas.onboarding_goal import GoalExtractionWorkflowResult, GoalExtractor
from app.schemas.onboarding_service import OnboardingResultKind, OnboardingServiceResult

_PARSE_IN_FLIGHT_KEY = "_parse_in_flight"
_PARSE_IN_FLIGHT_TTL = timedelta(minutes=10)
_GOAL_PHASE_KEY = "_goal_intake_phase"
_GOAL_PHASE_COLLECTING = "COLLECTING"
_GOAL_PHASE_CLARIFYING = "CLARIFYING"
_GOAL_PHASE_CONFIRMING = "CONFIRMING"
_GOAL_PHASE_ADDING = "ADDING"
_GOAL_DRAFT_KEY = "goal_draft"
_RAW_GOAL_TEXT_KEY = "raw_goal_text"
_GOAL_MESSAGES_KEY = "goal_messages"
_GOAL_CLARIFICATION_FIELD_KEY = "_goal_clarification_field"
_GOAL_CLARIFICATION_HINT_KEY = "_goal_clarification_hint"


class OnboardingApplicationError(RuntimeError):
    """Safe, code-bearing onboarding application failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class OnboardingService:
    """Own the only supported onboarding flow and its durable checkpoints."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        goal_extractor: GoalExtractor,
        settings: Settings,
    ) -> None:
        self._session_factory = session_factory
        self._goal_extractor = goal_extractor
        self._settings = settings

    async def start(self, identity: TelegramIdentity) -> OnboardingServiceResult:
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
            if user.status is UserStatus.NEW:
                user = await users.update_status(
                    user_id=user.id,
                    status=UserStatus.ONBOARDING_IN_PROGRESS,
                )
            return self._result(
                user,
                onboarding,
                created=created or onboarding_created,
            )

    async def restart(self, identity: TelegramIdentity) -> OnboardingServiceResult:
        """Restart a cancelled session from consent without deleting the account."""

        async with self._session_factory.begin() as session:
            user, current = await self._locked_state(session, identity)
            if current.status is not OnboardingStatus.CANCELLED:
                raise OnboardingApplicationError("restart_not_allowed")
            onboarding = await OnboardingRepository(session).restart(user_id=user.id)
            return self._result(user, onboarding)

    async def confirm_consent(
        self,
        identity: TelegramIdentity,
    ) -> OnboardingServiceResult:
        """Persist explicit consent and advance to the setup introduction."""

        async with self._session_factory.begin() as session:
            user, onboarding = await self._locked_state(session, identity)
            self._require_active(onboarding)
            answers = self._answers(onboarding)
            if answers.get("consent") is True:
                return self._result(user, onboarding)
            if onboarding.current_step is not OnboardingStep.CONSENT:
                raise OnboardingApplicationError("stale_action")
            answers["consent"] = True
            onboarding = await OnboardingRepository(session).save_progress(
                user_id=user.id,
                current_step=OnboardingStep.SETUP_INTRODUCTION,
                answers=cast(dict[str, object], answers),
            )
            return self._result(user, onboarding)

    async def start_profile(
        self,
        identity: TelegramIdentity,
    ) -> OnboardingServiceResult:
        """Advance from the setup introduction to conversational goal intake."""

        async with self._session_factory.begin() as session:
            user, onboarding = await self._locked_state(session, identity)
            self._require_active(onboarding)
            answers = self._answers(onboarding)
            if answers.get("consent") is not True:
                raise OnboardingApplicationError("stale_action")
            if onboarding.current_step is OnboardingStep.GOAL_INTAKE:
                return self._result(user, onboarding)
            if onboarding.current_step is not OnboardingStep.SETUP_INTRODUCTION:
                raise OnboardingApplicationError("stale_action")
            answers[_GOAL_PHASE_KEY] = _GOAL_PHASE_COLLECTING
            onboarding = await OnboardingRepository(session).save_progress(
                user_id=user.id,
                current_step=OnboardingStep.GOAL_INTAKE,
                answers=cast(dict[str, object], answers),
            )
            return self._result(user, onboarding)

    async def add_to_goal(
        self,
        identity: TelegramIdentity,
    ) -> OnboardingServiceResult:
        """Request one more free-text update to the accumulated draft."""

        async with self._session_factory.begin() as session:
            user, onboarding = await self._locked_state(session, identity)
            self._require_active(onboarding)
            answers = self._answers(onboarding)
            self._require_goal_phase(
                onboarding,
                answers,
                {_GOAL_PHASE_CONFIRMING},
            )
            self._goal_draft_from_answers(answers, required=True)
            answers[_GOAL_PHASE_KEY] = _GOAL_PHASE_ADDING
            answers.pop(_GOAL_CLARIFICATION_FIELD_KEY, None)
            answers.pop(_GOAL_CLARIFICATION_HINT_KEY, None)
            onboarding = await OnboardingRepository(session).save_progress(
                user_id=user.id,
                current_step=OnboardingStep.GOAL_INTAKE,
                answers=cast(dict[str, object], answers),
            )
            return self._result(user, onboarding)

    async def restart_goal(
        self,
        identity: TelegramIdentity,
    ) -> OnboardingServiceResult:
        """Clear only the temporary goal draft while preserving consent."""

        async with self._session_factory.begin() as session:
            user, onboarding = await self._locked_state(session, identity)
            self._require_active(onboarding)
            answers = self._answers(onboarding)
            self._require_goal_phase(
                onboarding,
                answers,
                {
                    _GOAL_PHASE_COLLECTING,
                    _GOAL_PHASE_CLARIFYING,
                    _GOAL_PHASE_CONFIRMING,
                    _GOAL_PHASE_ADDING,
                },
            )
            for key in (
                _GOAL_DRAFT_KEY,
                _RAW_GOAL_TEXT_KEY,
                _GOAL_MESSAGES_KEY,
                _GOAL_CLARIFICATION_FIELD_KEY,
                _GOAL_CLARIFICATION_HINT_KEY,
                _PARSE_IN_FLIGHT_KEY,
            ):
                answers.pop(key, None)
            answers[_GOAL_PHASE_KEY] = _GOAL_PHASE_COLLECTING
            onboarding = await OnboardingRepository(session).save_progress(
                user_id=user.id,
                current_step=OnboardingStep.GOAL_INTAKE,
                answers=cast(dict[str, object], answers),
            )
            return self._result(user, onboarding)

    async def confirm_goal(
        self,
        identity: TelegramIdentity,
    ) -> OnboardingServiceResult:
        """Persist the canonical goal and stop at GOAL_CONFIRMED."""

        async with self._session_factory.begin() as session:
            user, onboarding = await self._locked_state(session, identity)
            self._require_active(onboarding)
            answers = self._answers(onboarding)
            self._require_goal_phase(
                onboarding,
                answers,
                {_GOAL_PHASE_CONFIRMING},
            )
            draft = cast(
                GoalExtractionOutput,
                self._goal_draft_from_answers(answers, required=True),
            )
            ready, _ = self._goal_readiness(draft)
            if not ready or draft.main_goal is None or draft.target_outcome is None:
                raise OnboardingApplicationError("goal_draft_incomplete")
            original = answers.get(_RAW_GOAL_TEXT_KEY)
            if not isinstance(original, str) or not original:
                raise OnboardingApplicationError("goal_draft_incomplete")
            await ProfileRepository(session).upsert_conversational_training_goal(
                user_id=user.id,
                main_goal=draft.main_goal,
                event_date=draft.event_date,
                target_outcome=draft.target_outcome,
                secondary_priority=draft.secondary_priority,
                original_description=original,
            )
            for key in (
                _GOAL_PHASE_KEY,
                _GOAL_DRAFT_KEY,
                _GOAL_CLARIFICATION_FIELD_KEY,
                _GOAL_CLARIFICATION_HINT_KEY,
                _PARSE_IN_FLIGHT_KEY,
            ):
                answers.pop(key, None)
            onboarding = await OnboardingRepository(session).save_progress(
                user_id=user.id,
                current_step=OnboardingStep.GOAL_CONFIRMED,
                answers=cast(dict[str, object], answers),
            )
            return self._result(user, onboarding)

    async def choose_goal_clarification(
        self,
        identity: TelegramIdentity,
        choice: str,
    ) -> OnboardingServiceResult:
        """Apply a narrow clarification button without invoking the LLM."""

        async with self._session_factory.begin() as session:
            user, onboarding = await self._locked_state(session, identity)
            self._require_active(onboarding)
            answers = self._answers(onboarding)
            self._require_goal_phase(
                onboarding,
                answers,
                {_GOAL_PHASE_CLARIFYING},
            )
            draft = cast(
                GoalExtractionOutput,
                self._goal_draft_from_answers(answers, required=True),
            )
            field = answers.get(_GOAL_CLARIFICATION_FIELD_KEY)
            if field == "event_date":
                if choice == "HAS_DATE":
                    answers[_GOAL_CLARIFICATION_HINT_KEY] = "exact_date"
                elif choice == "NOT_YET":
                    draft = draft.model_copy(
                        update={
                            "event_date": None,
                            "missing_fields": [
                                item
                                for item in draft.missing_fields
                                if item != "event_date"
                            ],
                            "ambiguous_fields": [
                                item
                                for item in draft.ambiguous_fields
                                if item != "event_date"
                            ],
                        }
                    )
                    self._stage_goal_draft(answers, draft)
                else:
                    raise OnboardingApplicationError("invalid_action")
            elif field == "main_goal":
                hints = {
                    "PREPARE_RACE": ("Prepare for a race", "race"),
                    "SPECIFIC_DISTANCE": (
                        "Reach a specific running distance",
                        "distance",
                    ),
                    "IMPROVE_PACE": ("Improve running pace", "pace"),
                    "SOMETHING_ELSE": (None, "other"),
                }
                if choice == "RUN_CONSISTENTLY":
                    draft = draft.model_copy(
                        update={
                            "main_goal": "Build a consistent running habit",
                            "target_outcome": "Train consistently",
                            "event_date": None,
                            "missing_fields": [],
                            "ambiguous_fields": [],
                            "message_status": "COMPLETE",
                        }
                    )
                    self._stage_goal_draft(answers, draft)
                elif choice in hints:
                    main_goal, hint = hints[choice]
                    if main_goal is not None:
                        draft = draft.model_copy(update={"main_goal": main_goal})
                        answers[_GOAL_DRAFT_KEY] = cast(
                            JsonValue,
                            draft.model_dump(mode="json"),
                        )
                    answers[_GOAL_CLARIFICATION_HINT_KEY] = hint
                else:
                    raise OnboardingApplicationError("invalid_action")
            else:
                raise OnboardingApplicationError("invalid_action")
            onboarding = await OnboardingRepository(session).save_progress(
                user_id=user.id,
                current_step=OnboardingStep.GOAL_INTAKE,
                answers=cast(dict[str, object], answers),
            )
            return self._result(user, onboarding)

    async def handle_text(
        self,
        identity: TelegramIdentity,
        text: str,
    ) -> OnboardingServiceResult:
        """Invoke goal extraction only while the goal-intake checkpoint expects text."""

        async with self._session_factory() as session:
            user = await self._require_user(session, identity)
            onboarding = await OnboardingRepository(session).require_for_user(
                user_id=user.id
            )
            goal_phase = dict(onboarding.answers).get(_GOAL_PHASE_KEY)
        if (
            onboarding.status is OnboardingStatus.ACTIVE
            and onboarding.current_step is OnboardingStep.GOAL_INTAKE
            and isinstance(goal_phase, str)
        ):
            return await self._extract_goal(
                identity=identity,
                user_id=user.id,
                text=text,
            )
        raise OnboardingApplicationError("invalid_action")

    async def cancel(self, identity: TelegramIdentity) -> OnboardingServiceResult:
        """Cancel without deleting consent, goal staging, or canonical data."""

        async with self._session_factory.begin() as session:
            user, onboarding = await self._locked_state(session, identity)
            self._require_active(onboarding)
            onboarding = await OnboardingRepository(session).cancel(user_id=user.id)
            return self._result(user, onboarding)

    async def snapshot(self, identity: TelegramIdentity) -> OnboardingServiceResult:
        """Load the current durable checkpoint for resume or navigation."""

        async with self._session_factory() as session:
            user = await self._require_user(session, identity)
            onboarding = await OnboardingRepository(session).require_for_user(
                user_id=user.id
            )
            return self._result(user, onboarding)

    async def _extract_goal(
        self,
        *,
        identity: TelegramIdentity,
        user_id: uuid.UUID,
        text: str,
    ) -> OnboardingServiceResult:
        """Persist raw input, invoke the focused graph, and stage a merged draft."""

        parse_run_id = str(uuid.uuid4())
        existing_draft: GoalExtractionOutput | None
        action: GoalExtractionAction
        async with self._session_factory.begin() as session:
            user, onboarding = await self._locked_state(session, identity)
            self._require_active(onboarding)
            if user.id != user_id:
                raise OnboardingApplicationError("user_not_found")
            answers = self._answers(onboarding)
            self._require_goal_phase(
                onboarding,
                answers,
                {
                    _GOAL_PHASE_COLLECTING,
                    _GOAL_PHASE_CLARIFYING,
                    _GOAL_PHASE_ADDING,
                },
            )
            if self._parse_is_in_flight(onboarding):
                raise OnboardingApplicationError("parse_in_progress")
            existing_draft = self._goal_draft_from_answers(answers)
            action = (
                "UPDATE_EXISTING_GOAL" if existing_draft is not None else "CREATE_GOAL"
            )
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
            self._append_goal_message(answers, text)
            answers[_PARSE_IN_FLIGHT_KEY] = cast(
                JsonValue,
                {
                    "run_id": parse_run_id,
                    "started_at": utc_now().isoformat(),
                },
            )
            await OnboardingRepository(session).save_progress(
                user_id=user_id,
                current_step=OnboardingStep.GOAL_INTAKE,
                answers=cast(dict[str, object], answers),
            )
            usage = await LLMUsageRepository(session).record(
                user_id=user_id,
                onboarding_step=OnboardingStep.GOAL_INTAKE,
                provider_mode=self._settings.llm_mode,
                model=self._settings.llm_model,
                status=LLMUsageStatus.PROVIDER_ERROR,
            )
            usage_id = usage.id

        try:
            workflow = await self._goal_extractor.extract(
                user_id=user_id,
                action=action,
                user_text=text,
                existing_draft=existing_draft,
            )
        except Exception:
            workflow = GoalExtractionWorkflowResult(
                outcome="provider_error",
                error_code="llm_provider_error",
            )

        async with self._session_factory.begin() as session:
            user, onboarding = await self._locked_state(session, identity)
            if not self._owns_parse_run(onboarding, parse_run_id):
                raise OnboardingApplicationError("stale_action")
            usage_status = LLMUsageStatus.PROVIDER_ERROR
            if workflow.outcome == "fallback_required":
                usage_status = LLMUsageStatus.FALLBACK
            elif workflow.outcome == "extracted":
                usage_status = (
                    LLMUsageStatus.SUCCEEDED
                    if workflow.goal_patch is not None
                    and workflow.goal_patch.message_status == "COMPLETE"
                    else LLMUsageStatus.CLARIFICATION
                )
            await LLMUsageRepository(session).update_outcome(
                user_id=user.id,
                usage_id=usage_id,
                status=usage_status,
                prompt_tokens=workflow.prompt_tokens,
                completion_tokens=workflow.completion_tokens,
            )
            answers = self._answers(onboarding)
            answers.pop(_PARSE_IN_FLIGHT_KEY, None)
            repository = OnboardingRepository(session)
            if workflow.outcome != "extracted" or workflow.goal_patch is None:
                onboarding = await repository.save_progress(
                    user_id=user.id,
                    current_step=OnboardingStep.GOAL_INTAKE,
                    answers=cast(dict[str, object], answers),
                )
                return self._result(
                    user,
                    onboarding,
                    kind=(
                        "fallback"
                        if workflow.outcome == "fallback_required"
                        else "provider_error"
                    ),
                    error_code=workflow.error_code,
                )
            try:
                patch = GoalExtractionPatch.model_validate(
                    workflow.goal_patch.model_dump(mode="json")
                )
            except ValidationError:
                onboarding = await repository.save_progress(
                    user_id=user.id,
                    current_step=OnboardingStep.GOAL_INTAKE,
                    answers=cast(dict[str, object], answers),
                )
                return self._result(
                    user,
                    onboarding,
                    kind="fallback",
                    error_code="malformed_structured_output",
                )
            if patch.message_status == "OFF_TOPIC":
                self._remove_last_goal_message(answers)
                onboarding = await repository.save_progress(
                    user_id=user.id,
                    current_step=OnboardingStep.GOAL_INTAKE,
                    answers=cast(dict[str, object], answers),
                )
                return self._result(user, onboarding, kind="goal_off_topic")

            merged = self._merge_goal_patch(
                existing=existing_draft,
                patch=patch,
            )
            self._stage_goal_draft(answers, merged)
            onboarding = await repository.save_progress(
                user_id=user.id,
                current_step=OnboardingStep.GOAL_INTAKE,
                answers=cast(dict[str, object], answers),
            )
            return self._result(user, onboarding)

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
    def _require_goal_phase(
        onboarding: OnboardingSession,
        answers: dict[str, JsonValue],
        allowed_phases: set[str],
    ) -> None:
        phase = answers.get(_GOAL_PHASE_KEY)
        if (
            onboarding.current_step is not OnboardingStep.GOAL_INTAKE
            or not isinstance(phase, str)
            or phase not in allowed_phases
        ):
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
    def _owns_parse_run(onboarding: OnboardingSession, parse_run_id: str) -> bool:
        marker = dict(onboarding.answers).get(_PARSE_IN_FLIGHT_KEY)
        return isinstance(marker, dict) and marker.get("run_id") == parse_run_id

    @staticmethod
    def _answers(onboarding: OnboardingSession) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], dict(onboarding.answers))

    @staticmethod
    def _goal_draft_from_answers(
        answers: dict[str, JsonValue],
        *,
        required: bool = False,
    ) -> GoalExtractionOutput | None:
        raw = answers.get(_GOAL_DRAFT_KEY)
        if raw is None and not required:
            return None
        try:
            return GoalExtractionOutput.model_validate(raw)
        except ValidationError as exc:
            raise OnboardingApplicationError("goal_draft_invalid") from exc

    @staticmethod
    def _append_goal_message(answers: dict[str, JsonValue], text: str) -> None:
        existing = answers.get(_GOAL_MESSAGES_KEY)
        messages = (
            [item for item in existing if isinstance(item, str)]
            if isinstance(existing, list)
            else []
        )
        messages.append(text)
        answers[_GOAL_MESSAGES_KEY] = cast(JsonValue, messages)
        if not isinstance(answers.get(_RAW_GOAL_TEXT_KEY), str):
            answers[_RAW_GOAL_TEXT_KEY] = text

    @staticmethod
    def _remove_last_goal_message(answers: dict[str, JsonValue]) -> None:
        existing = answers.get(_GOAL_MESSAGES_KEY)
        messages = (
            [item for item in existing if isinstance(item, str)]
            if isinstance(existing, list)
            else []
        )
        if messages:
            messages.pop()
        if messages:
            answers[_GOAL_MESSAGES_KEY] = cast(JsonValue, messages)
            answers[_RAW_GOAL_TEXT_KEY] = messages[0]
        else:
            answers.pop(_GOAL_MESSAGES_KEY, None)
            answers.pop(_RAW_GOAL_TEXT_KEY, None)

    @staticmethod
    def _merge_goal_patch(
        *,
        existing: GoalExtractionOutput | None,
        patch: GoalExtractionPatch,
    ) -> GoalExtractionOutput:
        main_goal = patch.main_goal or (existing.main_goal if existing else None)
        target_outcome = patch.target_outcome or (
            existing.target_outcome if existing else None
        )
        secondary_priority = patch.secondary_priority or (
            existing.secondary_priority if existing else None
        )
        event_date = patch.event_date or (existing.event_date if existing else None)

        missing = [
            field for field in patch.missing_fields if field != "secondary_priority"
        ]
        ambiguous = [
            field for field in patch.ambiguous_fields if field != "secondary_priority"
        ]
        if main_goal is not None:
            missing = [field for field in missing if field != "main_goal"]
        if target_outcome is not None:
            missing = [field for field in missing if field != "target_outcome"]
        if event_date is not None:
            missing = [field for field in missing if field != "event_date"]

        return GoalExtractionOutput(
            main_goal=main_goal,
            event_date=event_date,
            target_outcome=target_outcome,
            secondary_priority=secondary_priority,
            missing_fields=missing,
            ambiguous_fields=ambiguous,
            message_status=patch.message_status,
        )

    @classmethod
    def _stage_goal_draft(
        cls,
        answers: dict[str, JsonValue],
        draft: GoalExtractionOutput,
    ) -> None:
        ready, clarification_field = cls._goal_readiness(draft)
        status = "COMPLETE" if ready else "NEEDS_CLARIFICATION"
        normalized = draft.model_copy(update={"message_status": status})
        answers[_GOAL_DRAFT_KEY] = cast(
            JsonValue,
            normalized.model_dump(mode="json"),
        )
        answers.pop(_GOAL_CLARIFICATION_HINT_KEY, None)
        if ready:
            answers[_GOAL_PHASE_KEY] = _GOAL_PHASE_CONFIRMING
            answers.pop(_GOAL_CLARIFICATION_FIELD_KEY, None)
        else:
            if clarification_field is None:
                raise OnboardingApplicationError("goal_draft_invalid")
            answers[_GOAL_PHASE_KEY] = _GOAL_PHASE_CLARIFYING
            answers[_GOAL_CLARIFICATION_FIELD_KEY] = cast(
                JsonValue,
                clarification_field,
            )

    @classmethod
    def _goal_readiness(
        cls,
        draft: GoalExtractionOutput,
    ) -> tuple[bool, GoalFieldName | None]:
        missing_or_ambiguous = set(draft.missing_fields) | set(draft.ambiguous_fields)
        if (
            draft.main_goal is None
            or cls._is_vague_main_goal(draft.main_goal)
            or "main_goal" in missing_or_ambiguous
        ):
            return False, "main_goal"
        if draft.target_outcome is None or "target_outcome" in missing_or_ambiguous:
            return False, "target_outcome"
        if "event_date" in missing_or_ambiguous:
            return False, "event_date"
        return True, None

    @staticmethod
    def _is_vague_main_goal(value: str) -> bool:
        folded = " ".join(value.casefold().strip(" .!?\n\t").split())
        return folded in {
            "run",
            "running",
            "training",
            "train to run",
            "i want to train to run",
            "improve running",
            "get better at running",
            "prepare for a race",
            "reach a specific running distance",
        }

    @classmethod
    def _result(
        cls,
        user: User,
        onboarding: OnboardingSession,
        *,
        kind: OnboardingResultKind | None = None,
        error_code: str | None = None,
        created: bool = False,
    ) -> OnboardingServiceResult:
        answers = cls._answers(onboarding)
        if kind is None:
            if onboarding.status is OnboardingStatus.CANCELLED:
                kind = "cancelled"
            elif onboarding.current_step is OnboardingStep.CONSENT:
                kind = "step"
            elif onboarding.current_step is OnboardingStep.SETUP_INTRODUCTION:
                kind = "setup_introduction"
            elif onboarding.current_step is OnboardingStep.GOAL_CONFIRMED:
                kind = "goal_confirmed"
            else:
                raw_phase = answers.get(_GOAL_PHASE_KEY)
                phase = raw_phase if isinstance(raw_phase, str) else None
                phase_kinds: dict[str, OnboardingResultKind] = {
                    _GOAL_PHASE_CLARIFYING: "goal_clarification",
                    _GOAL_PHASE_CONFIRMING: "goal_confirmation",
                    _GOAL_PHASE_ADDING: "goal_addition",
                }
                kind = phase_kinds.get(phase or "", "goal_intake")
        return OnboardingServiceResult(
            kind=kind,
            user_id=user.id,
            user_status=user.status,
            onboarding_status=onboarding.status,
            current_step=onboarding.current_step,
            answers=answers,
            error_code=error_code,
            created=created,
        )
