"""Orchestrates screenshot extraction, athlete confirmation, and persistence.

Deliberately not part of ``CoachBotApplicationService``'s LangGraph-routed
facade: confirmation here is two plain buttons, not a conversation state, so
it stays a standalone service with its own narrow protocol, wired into the
bot's handlers directly (see ``app/bot/handlers.py``).
"""

from __future__ import annotations

import re
import secrets
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.models import Workout
from app.domain.enums import OnboardingStep
from app.integrations.llm.vision import (
    DeepSeekWorkoutScreenshotExtractor,
    ScreenshotExtractionError,
)
from app.observability.noop import NoOpAIWorkflowObserver
from app.observability.protocol import (
    AIWorkflowObserver,
    AIWorkflowRunError,
    AIWorkflowRunMetadata,
    AIWorkflowRunResult,
)
from app.repositories.activities import (
    ActivityImportValidationError,
    ActivityUpsertOutcome,
    TrainingActivityRepository,
)
from app.repositories.errors import OwnedRecordNotFoundError
from app.repositories.users import UserRepository
from app.repositories.weekly_plans import WeeklyTrainingPlanRepository
from app.schemas.manual_import import ManualWorkoutImportRequest
from app.schemas.weekly_plans import FirstWeekPlan
from app.services.activities.adapters.manual_screenshot import (
    from_manual_screenshot,
)
from app.services.weekly_evaluation.classification import has_linkable_plan_for_workout
from app.services.weekly_evaluation.eligibility import local_date
from app.services.weekly_evaluation.errors import (
    PlannedSessionNotFoundError,
    PlanNotLinkableError,
    WorkoutAlreadyLinkedError,
    WorkoutNotEligibleError,
)
from app.services.weekly_evaluation.linking import LinkingService
from app.services.workout_screenshot.validation import (
    ScreenshotEvaluatorEvidenceRequiredError,
    require_evaluator_capture_metrics,
)

# A draft is small and short-lived (confirmed or abandoned within minutes),
# so a bounded in-memory map is enough - no new table for something that
# outlives its usefulness within one bot restart cycle anyway.
_DRAFT_TTL_SECONDS = 30 * 60
_MAX_PENDING_DRAFTS = 500


class WorkoutScreenshotDisabledError(RuntimeError):
    """Raised when the feature flag is off."""


class WorkoutScreenshotNotFoundError(RuntimeError):
    """Raised for an unknown/expired draft, or an unrecognized athlete."""


class WorkoutScreenshotHeartRateRequiredError(RuntimeError):
    """Raised when a screenshot draft is missing required HR evidence."""


class WorkoutScreenshotSessionLinkRequiredError(RuntimeError):
    """Raised when a screenshot cannot be explicitly linked to a menu session."""


@dataclass(frozen=True, slots=True)
class ScreenshotLinkOption:
    """One athlete-selectable first-week session, never an auto-match."""

    index: int
    discipline: str
    purpose: str


@dataclass(frozen=True, slots=True)
class ScreenshotDraft:
    """What the bot shows the athlete before it commits anything."""

    token: str
    request: ManualWorkoutImportRequest
    link_options: tuple[ScreenshotLinkOption, ...] = ()
    selected_link_option: int | None = None


@dataclass(frozen=True, slots=True)
class ScreenshotConfirmation:
    """The persisted workout plus the plan the athlete explicitly selected."""

    workout: Workout
    outcome: ActivityUpsertOutcome
    plan_id: uuid.UUID


@dataclass(slots=True)
class _PendingDraft:
    telegram_user_id: int
    request: ManualWorkoutImportRequest
    awaiting_heart_rate: bool = False
    link_plan_id: uuid.UUID | None = None
    link_session_ids: tuple[uuid.UUID, ...] = ()
    link_session_labels: tuple[tuple[str, str], ...] = ()
    selected_link_option: int | None = None
    created_at: float = field(default_factory=time.monotonic)


class WorkoutScreenshotService:
    """Extract, hold for confirmation, then persist one screenshot workout."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        extractor: DeepSeekWorkoutScreenshotExtractor,
        observer: AIWorkflowObserver | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._extractor = extractor
        self._observer = observer or NoOpAIWorkflowObserver()
        self._pending: dict[str, _PendingDraft] = {}

    async def extract_draft(
        self,
        *,
        telegram_user_id: int,
        image_bytes: bytes,
        image_media_type: str = "image/jpeg",
    ) -> ScreenshotDraft:
        """Read the screenshot and hold the result for the athlete to confirm."""

        self._require_enabled()
        async with self._session_factory() as session:
            user = await UserRepository(session).get_by_telegram_id(telegram_user_id)
        if user is None:
            raise WorkoutScreenshotNotFoundError("athlete not recognized")

        started_at = datetime.now(UTC)
        metadata = AIWorkflowRunMetadata(
            workflow_name="workout_screenshot_extraction",
            run_id=uuid.uuid4(),
            # Screenshot extraction shares the history-import product feature.
            onboarding_step=OnboardingStep.TRAINING_HISTORY_IMPORT,
            provider_mode="live",
            model_name=self._settings.llm_vision_model,
            started_at=started_at,
        )
        await self._observer.on_run_started(metadata)
        try:
            request = await self._extractor.extract(
                image_bytes=image_bytes,
                image_media_type=image_media_type,
            )
        except ScreenshotExtractionError:
            failed_at = datetime.now(UTC)
            await self._observer.on_run_failed(
                AIWorkflowRunError(
                    metadata=metadata,
                    failed_at=failed_at,
                    latency_ms=int((failed_at - started_at).total_seconds() * 1000),
                    error_code="screenshot_extraction_failed",
                )
            )
            raise

        try:
            require_evaluator_capture_metrics(request)
        except ScreenshotEvaluatorEvidenceRequiredError:
            failed_at = datetime.now(UTC)
            await self._observer.on_run_failed(
                AIWorkflowRunError(
                    metadata=metadata,
                    failed_at=failed_at,
                    latency_ms=int((failed_at - started_at).total_seconds() * 1000),
                    error_code="screenshot_evaluator_evidence_incomplete",
                )
            )
            raise

        completed_at = datetime.now(UTC)
        await self._observer.on_run_completed(
            AIWorkflowRunResult(
                metadata=metadata,
                outcome="confirmation_required",
                completed_at=completed_at,
                latency_ms=int((completed_at - started_at).total_seconds() * 1000),
            )
        )

        token = self._store(telegram_user_id, request)
        pending = self._pending[token]
        await self._load_link_options(pending)
        return self._as_draft(token, pending)

    async def confirm(
        self,
        *,
        telegram_user_id: int,
        token: str,
    ) -> ScreenshotConfirmation:
        """Persist a previously extracted draft for its original athlete only."""

        self._require_enabled()
        draft = self._pending.get(token)
        if draft is None or draft.telegram_user_id != telegram_user_id:
            raise WorkoutScreenshotNotFoundError("draft not found or expired")
        self._require_confirmable_draft(draft)

        async with self._session_factory() as session, session.begin():
            user = await UserRepository(session).get_by_telegram_id(telegram_user_id)
            if user is None:
                raise WorkoutScreenshotNotFoundError("athlete not recognized")

            try:
                incoming = from_manual_screenshot(draft.request)
                workout, outcome = await TrainingActivityRepository(
                    session
                ).import_activity(
                    user_id=user.id,
                    incoming=incoming,
                )
                option_index = draft.selected_link_option
                assert (
                    option_index is not None
                )  # checked before opening the transaction
                assert draft.link_plan_id is not None
                await LinkingService(session).link_workout_to_session(
                    athlete_id=user.id,
                    plan_id=draft.link_plan_id,
                    plan_session_id=draft.link_session_ids[option_index],
                    workout_id=workout.id,
                )
            except ActivityImportValidationError:
                raise
            except (
                OwnedRecordNotFoundError,
                PlanNotLinkableError,
                PlannedSessionNotFoundError,
                WorkoutAlreadyLinkedError,
                WorkoutNotEligibleError,
            ) as error:
                raise WorkoutScreenshotSessionLinkRequiredError(
                    "selected first-week session is no longer available"
                ) from error
            except ValueError as error:
                raise ActivityImportValidationError(
                    "screenshot draft could not be normalized"
                ) from error
        self._pending.pop(token, None)
        return ScreenshotConfirmation(
            workout=workout,
            outcome=outcome,
            plan_id=draft.link_plan_id,
        )

    def cancel(self, *, telegram_user_id: int, token: str) -> bool:
        """Discard a draft; returns whether one actually existed."""

        draft = self._pending.get(token)
        if draft is None or draft.telegram_user_id != telegram_user_id:
            return False
        self._pending.pop(token, None)
        return True

    def request_heart_rate(self, *, telegram_user_id: int, token: str) -> bool:
        draft = self._pending.get(token)
        if draft is None or draft.telegram_user_id != telegram_user_id:
            return False
        for pending_draft in self._pending.values():
            if pending_draft.telegram_user_id == telegram_user_id:
                pending_draft.awaiting_heart_rate = False
        draft.awaiting_heart_rate = True
        return True

    def provide_heart_rate(
        self, *, telegram_user_id: int, text: str
    ) -> ScreenshotDraft | None:
        draft = next(
            (
                item
                for item in self._pending.values()
                if item.telegram_user_id == telegram_user_id
                and item.awaiting_heart_rate
            ),
            None,
        )
        if draft is None:
            return None
        match = re.fullmatch(r"\s*(\d{2,3})\s*[/,]\s*(\d{2,3})\s*", text)
        if match is None:
            raise ValueError("invalid heart rate")
        average, maximum = (int(match.group(1)), int(match.group(2)))
        if average > maximum or maximum > 300:
            raise ValueError("invalid heart rate")
        draft.request = draft.request.model_copy(
            update={"average_heart_rate": average, "max_heart_rate": maximum}
        )
        draft.awaiting_heart_rate = False
        token = next(key for key, item in self._pending.items() if item is draft)
        return self._as_draft(token, draft)

    def select_link_option(
        self, *, telegram_user_id: int, token: str, option_index: int
    ) -> ScreenshotDraft | None:
        """Record the athlete's explicit choice, never a heuristic match."""

        draft = self._pending.get(token)
        if (
            draft is None
            or draft.telegram_user_id != telegram_user_id
            or option_index < 0
            or option_index >= len(draft.link_session_ids)
        ):
            return None
        draft.selected_link_option = option_index
        return self._as_draft(token, draft)

    def _require_enabled(self) -> None:
        if not self._settings.screenshot_import_enabled:
            raise WorkoutScreenshotDisabledError("screenshot import is disabled")

    @staticmethod
    def _require_confirmable_draft(draft: _PendingDraft) -> None:
        """Apply capture-confirm gates before any persistence work begins.

        Keep independent confirmation requirements here so later gates (such as
        screenshot volume validation) can be added without weakening or
        entangling the mandatory-HR invariant.
        """

        request = draft.request
        if request.average_heart_rate is None or request.max_heart_rate is None:
            raise WorkoutScreenshotHeartRateRequiredError(
                "average and maximum heart rate are required before confirmation"
            )
        if (
            draft.link_plan_id is None
            or draft.selected_link_option is None
            or draft.selected_link_option >= len(draft.link_session_ids)
        ):
            raise WorkoutScreenshotSessionLinkRequiredError(
                "an explicit first-week session link is required before confirmation"
            )

    async def _load_link_options(self, draft: _PendingDraft) -> None:
        """Load eligible menu sessions, without choosing one for the athlete."""

        async with self._session_factory() as session:
            user = await UserRepository(session).get_by_telegram_id(
                draft.telegram_user_id
            )
            if user is None:
                return
            workout_date = local_date(draft.request.started_at, user.timezone)
            week_start = workout_date - timedelta(days=workout_date.weekday())
            plan = await WeeklyTrainingPlanRepository(session).get_for_week(
                athlete_id=user.id,
                week_start=week_start,
            )
            if plan is None:
                return
            try:
                first_week_plan = FirstWeekPlan.model_validate(plan.plan_jsonb)
            except ValueError:
                return
            if not has_linkable_plan_for_workout(
                plan=first_week_plan,
                plan_schema_version=plan.plan_schema_version,
                workout_started_at=draft.request.started_at,
                timezone=user.timezone,
            ):
                return
            draft.link_plan_id = plan.id
            draft.link_session_ids = tuple(
                session_item.id for session_item in first_week_plan.sessions
            )
            draft.link_session_labels = tuple(
                (session_item.discipline.value.title(), session_item.purpose)
                for session_item in first_week_plan.sessions
            )

    @staticmethod
    def _as_draft(token: str, draft: _PendingDraft) -> ScreenshotDraft:
        options = tuple(
            ScreenshotLinkOption(index=index, discipline=discipline, purpose=purpose)
            for index, (discipline, purpose) in enumerate(draft.link_session_labels)
        )
        return ScreenshotDraft(
            token=token,
            request=draft.request,
            link_options=options,
            selected_link_option=draft.selected_link_option,
        )

    def _store(self, telegram_user_id: int, request: ManualWorkoutImportRequest) -> str:
        self._evict_expired()
        if len(self._pending) >= _MAX_PENDING_DRAFTS:
            oldest_token = min(
                self._pending, key=lambda key: self._pending[key].created_at
            )
            self._pending.pop(oldest_token, None)
        token = uuid.UUID(bytes=secrets.token_bytes(16)).hex[:16]
        self._pending[token] = _PendingDraft(
            telegram_user_id=telegram_user_id,
            request=request,
        )
        return token

    def _evict_expired(self) -> None:
        deadline = time.monotonic() - _DRAFT_TTL_SECONDS
        expired = [
            token
            for token, draft in self._pending.items()
            if draft.created_at < deadline
        ]
        for token in expired:
            self._pending.pop(token, None)


__all__ = [
    "ActivityImportValidationError",
    "ScreenshotConfirmation",
    "ScreenshotDraft",
    "ScreenshotEvaluatorEvidenceRequiredError",
    "ScreenshotLinkOption",
    "WorkoutScreenshotDisabledError",
    "WorkoutScreenshotHeartRateRequiredError",
    "WorkoutScreenshotNotFoundError",
    "WorkoutScreenshotService",
    "WorkoutScreenshotSessionLinkRequiredError",
]
