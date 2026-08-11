"""Focused, durable onboarding through explicit conversational-goal confirmation."""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import cast

from pydantic import JsonValue, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.base import utc_now
from app.db.models import OnboardingSession, ProfileSettingsSession, TrainingGoal, User
from app.domain.enums import (
    AthleteGender,
    LLMUsageStatus,
    OnboardingStatus,
    OnboardingStep,
    ProfileSettingsStep,
    UserStatus,
)
from app.integrations.llm.models import (
    GoalExtractionAction,
    GoalExtractionOutput,
    GoalExtractionPatch,
    GoalFieldName,
)
from app.repositories.equipment import EquipmentRepository
from app.repositories.llm_usage import LLMUsageRepository
from app.repositories.onboarding import OnboardingRepository
from app.repositories.profile_settings import ProfileSettingsRepository
from app.repositories.profiles import ProfileRepository
from app.repositories.users import UserRepository
from app.schemas.common import TelegramIdentity
from app.schemas.equipment import EquipmentReview, EquipmentSuggestionSummary
from app.schemas.onboarding_context import (
    ContextOnboardingWorkflow,
    FreeTextValidationWorkflowResult,
)
from app.schemas.onboarding_goal import (
    GoalExtractionWorkflowResult,
    GoalExtractor,
    UpdatedOnboardingData,
)
from app.schemas.onboarding_service import OnboardingResultKind, OnboardingServiceResult
from app.schemas.profile_settings import ProfileSettingsResult
from app.services.equipment.service import EquipmentRecommendationService
from app.workflows.onboarding_context.graph import create_context_onboarding_workflow

_PARSE_IN_FLIGHT_KEY = "_parse_in_flight"
_PARSE_IN_FLIGHT_TTL = timedelta(minutes=10)
_GOAL_PHASE_KEY = "_goal_intake_phase"
_GOAL_PHASE_COLLECTING = "COLLECTING"
_GOAL_PHASE_CLARIFYING = "CLARIFYING"
_GOAL_PHASE_CONFIRMING = "CONFIRMING"
_GOAL_DRAFT_KEY = "goal_draft"
_RAW_GOAL_TEXT_KEY = "raw_goal_text"
_GOAL_MESSAGES_KEY = "goal_messages"
_GOAL_CLARIFICATION_FIELD_KEY = "_goal_clarification_field"
_GOAL_CLARIFICATION_HINT_KEY = "_goal_clarification_hint"
_BIRTH_YEAR_KEY = "birth_year"
_GENDER_KEY = "gender"
_WEIGHT_KG_KEY = "weight_kg"
_HEIGHT_CM_KEY = "height_cm"
_EQUIPMENT_SELECTION_KEY = "equipment_selection"
_CONTEXT_RETRY_ERROR_KEY = "_context_retry_error"
_INTEGER_PATTERN = re.compile(r"[0-9]+")
_WEIGHT_PATTERN = re.compile(r"[0-9]+(?:\.[0-9]+)?")
_ISO_DATE_PATTERN = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
_ATHLETE_PROFILE_UPDATE_FIELDS = frozenset(
    {"age", "birth_year", "gender", "weight_kg", "height_cm"}
)
_TRAINING_GOAL_UPDATE_FIELDS = frozenset({"main_goal", "target_outcome", "event_date"})
_ATHLETE_PROFILE_CONTEXT_UPDATE_FIELDS = frozenset(
    {"availability_text", "health_limitations_text"}
)
_ONBOARDING_UPDATE_FIELDS = (
    _ATHLETE_PROFILE_UPDATE_FIELDS
    | _TRAINING_GOAL_UPDATE_FIELDS
    | _ATHLETE_PROFILE_CONTEXT_UPDATE_FIELDS
)
_FREE_TEXT_CONTEXT_STEPS = frozenset(
    {
        OnboardingStep.AVAILABILITY_INTAKE,
        OnboardingStep.HEALTH_LIMITATIONS_INTAKE,
    }
)


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
        context_workflow: ContextOnboardingWorkflow | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._goal_extractor = goal_extractor
        self._settings = settings
        # Production composition injects this explicitly.  Keeping a compiled
        # default preserves the service boundary for focused callers and ensures
        # every raw-context path still uses LangGraph structured output.
        self._context_workflow = context_workflow or create_context_onboarding_workflow(
            settings,
        )

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
            equipment_review = None
            if onboarding.current_step is OnboardingStep.EQUIPMENT_INTAKE:
                review = await self._equipment_review(
                    session=session,
                    user_id=user.id,
                )
                raw_selected = dict(onboarding.answers).get(
                    _EQUIPMENT_SELECTION_KEY,
                    [],
                )
                selected = (
                    {value for value in raw_selected if isinstance(value, str)}
                    if isinstance(raw_selected, list)
                    else set()
                )
                if review is not None:
                    equipment_review = self._with_selection(review, selected)
            return self._result(
                user,
                onboarding,
                created=created or onboarding_created,
                equipment_review=equipment_review,
            )

    async def restart(self, identity: TelegramIdentity) -> OnboardingServiceResult:
        """Restart a cancelled session from consent without deleting the account."""

        async with self._session_factory.begin() as session:
            user, current = await self._locked_state(session, identity)
            if current.status is not OnboardingStatus.CANCELLED:
                raise OnboardingApplicationError("restart_not_allowed")
            onboarding = await OnboardingRepository(session).restart(user_id=user.id)
            return self._result(user, onboarding)

    async def seed_development_step(
        self, identity: TelegramIdentity, step_name: str
    ) -> OnboardingServiceResult:
        """Prepare deterministic synthetic onboarding state for a dev user."""
        await self.start(identity)
        steps = {
            "availability": OnboardingStep.AVAILABILITY_INTAKE,
            "equipment": OnboardingStep.EQUIPMENT_RECOMMENDATION,
            "limitations": OnboardingStep.HEALTH_LIMITATIONS_INTAKE,
            "completed": OnboardingStep.HEALTH_LIMITATIONS_INTAKE,
        }
        step = steps.get(step_name)
        if step is None:
            raise OnboardingApplicationError("invalid_development_step")
        async with self._session_factory.begin() as session:
            user, onboarding = await self._locked_state(session, identity)
            profiles = ProfileRepository(session)
            await profiles.upsert_mandatory_athlete_profile(
                user_id=user.id,
                birth_year=1990,
                gender=AthleteGender.FEMALE,
                weight_kg=70.0,
                height_cm=175.0,
            )
            await profiles.upsert_conversational_training_goal(
                user_id=user.id,
                main_goal="Complete an Ironman 70.3",
                event_date=None,
                target_outcome="Finish comfortably",
                secondary_priority=None,
                original_description="Development test goal",
            )
            context: dict[str, str | None] = {
                "availability_text": None,
                "health_limitations_text": None,
            }
            if step_name in {"equipment", "limitations", "completed"}:
                context["availability_text"] = "Weekdays one hour; weekends two hours."
            if step_name == "completed":
                context["health_limitations_text"] = "NONE_REPORTED"
            await profiles.update_athlete_profile_context_fields(
                user_id=user.id, payload=context
            )
            onboarding.status = (
                OnboardingStatus.COMPLETED
                if step_name == "completed"
                else OnboardingStatus.ACTIVE
            )
            onboarding.current_step = step
            onboarding.answers = {
                "consent": True,
            }
            user.status = (
                UserStatus.ONBOARDING_COMPLETED
                if step_name == "completed"
                else UserStatus.ONBOARDING_IN_PROGRESS
            )
            await session.flush()
            return self._result(user, onboarding)

    async def reset_development_onboarding(
        self, identity: TelegramIdentity
    ) -> OnboardingServiceResult:
        """Return a development user to the first onboarding prompt only."""
        await self.start(identity)
        async with self._session_factory.begin() as session:
            user, onboarding = await self._locked_state(session, identity)
            onboarding.status = OnboardingStatus.ACTIVE
            onboarding.current_step = OnboardingStep.CONSENT
            onboarding.answers = {}
            user.status = UserStatus.ONBOARDING_IN_PROGRESS
            await session.flush()
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
        """Advance from the setup introduction to mandatory profile intake."""

        async with self._session_factory.begin() as session:
            user, onboarding = await self._locked_state(session, identity)
            self._require_active(onboarding)
            answers = self._answers(onboarding)
            if answers.get("consent") is not True:
                raise OnboardingApplicationError("stale_action")
            if onboarding.current_step is OnboardingStep.PROFILE_BIRTH_YEAR_INTAKE:
                return self._result(user, onboarding)
            if onboarding.current_step is not OnboardingStep.SETUP_INTRODUCTION:
                raise OnboardingApplicationError("stale_action")
            onboarding = await OnboardingRepository(session).save_progress(
                user_id=user.id,
                current_step=OnboardingStep.PROFILE_BIRTH_YEAR_INTAKE,
                answers=cast(dict[str, object], answers),
            )
            return self._result(user, onboarding)

    async def confirm_goal(
        self,
        identity: TelegramIdentity,
    ) -> OnboardingServiceResult:
        """Persist a confirmed goal, then advance to raw-context intake."""

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
            profile_exists = (
                await ProfileRepository(session).get_athlete_profile(user_id=user.id)
                is not None
            )
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
                current_step=(
                    OnboardingStep.AVAILABILITY_INTAKE
                    if profile_exists
                    else OnboardingStep.PROFILE_BIRTH_YEAR_INTAKE
                ),
                answers=cast(dict[str, object], answers),
            )
            return self._result(user, onboarding)

    async def choose_gender(
        self,
        identity: TelegramIdentity,
        choice: str,
    ) -> OnboardingServiceResult:
        """Persist one deterministic competition-category callback selection."""

        try:
            gender = AthleteGender(choice)
        except ValueError as exc:
            raise OnboardingApplicationError("invalid_action") from exc
        async with self._session_factory.begin() as session:
            user, onboarding = await self._locked_state(session, identity)
            self._require_active(onboarding)
            if onboarding.current_step is not OnboardingStep.PROFILE_GENDER_INTAKE:
                raise OnboardingApplicationError("stale_action")
            answers = self._answers(onboarding)
            answers[_GENDER_KEY] = gender.value
            onboarding = await OnboardingRepository(session).save_progress(
                user_id=user.id,
                current_step=OnboardingStep.PROFILE_WEIGHT_INTAKE,
                answers=cast(dict[str, object], answers),
            )
            return self._result(user, onboarding)

    async def choose_equipment(
        self,
        identity: TelegramIdentity,
        choice: str,
    ) -> OnboardingServiceResult:
        """Handle deterministic equipment callbacks without calling an LLM."""

        async with self._session_factory.begin() as session:
            user, onboarding = await self._locked_state(session, identity)
            self._require_active(onboarding)
            if onboarding.current_step is not OnboardingStep.EQUIPMENT_INTAKE:
                raise OnboardingApplicationError("stale_action")
            answers = self._answers(onboarding)
            review = await self._equipment_review(session=session, user_id=user.id)
            if review is None:
                raise OnboardingApplicationError("stale_action")
            resource_ids = {str(item.id) for item in review.options}
            raw_selected = answers.get(_EQUIPMENT_SELECTION_KEY, [])
            if not isinstance(raw_selected, list):
                raw_selected = []
            selected = {str(value) for value in raw_selected if isinstance(value, str)}
            selected.intersection_update(resource_ids)
            summary: EquipmentSuggestionSummary | None = None
            if choice == "done":
                if not resource_ids:
                    raise OnboardingApplicationError("stale_action")
                summary = await EquipmentRecommendationService().save_and_summarize(
                    repository=EquipmentRepository(session),
                    athlete_id=user.id,
                    review=review,
                    selected_ids={uuid.UUID(value) for value in selected},
                )
                current_step = OnboardingStep.HEALTH_LIMITATIONS_INTAKE
            elif choice in resource_ids:
                if choice in selected:
                    selected.remove(choice)
                else:
                    selected.add(choice)
                answers[_EQUIPMENT_SELECTION_KEY] = cast(JsonValue, sorted(selected))
                current_step = OnboardingStep.EQUIPMENT_INTAKE
            else:
                raise OnboardingApplicationError("invalid_action")
            onboarding = await OnboardingRepository(session).save_progress(
                user_id=user.id,
                current_step=current_step,
                answers=cast(dict[str, object], answers),
            )
            return self._result(
                user,
                onboarding,
                equipment_review=(
                    self._with_selection(review, selected)
                    if current_step is OnboardingStep.EQUIPMENT_INTAKE
                    else None
                ),
                equipment_summary=(
                    summary
                    if current_step is OnboardingStep.HEALTH_LIMITATIONS_INTAKE
                    else None
                ),
            )

    async def choose_health_limitations(
        self,
        identity: TelegramIdentity,
        choice: str,
    ) -> OnboardingServiceResult:
        """Handle deterministic health-limitations callbacks without an LLM."""

        if choice != "none":
            raise OnboardingApplicationError("invalid_action")
        async with self._session_factory.begin() as session:
            user, onboarding = await self._locked_state(session, identity)
            self._require_active(onboarding)
            if onboarding.current_step is not OnboardingStep.HEALTH_LIMITATIONS_INTAKE:
                raise OnboardingApplicationError("stale_action")
            answers = self._answers(onboarding)
            await ProfileRepository(session).update_athlete_profile_context_fields(
                user_id=user.id,
                payload={"health_limitations_text": "NONE_REPORTED"},
            )
            return await self._complete_context_onboarding(
                session=session,
                user=user,
                onboarding=onboarding,
                answers=answers,
            )

    async def profile_settings_snapshot(
        self, identity: TelegramIdentity
    ) -> ProfileSettingsResult | None:
        """Return active completed-profile edit state without using an LLM."""

        async with self._session_factory() as session:
            user = await self._require_user(session, identity)
            state = await ProfileSettingsRepository(session).get(user_id=user.id)
            if state is None or state.current_step is ProfileSettingsStep.MENU:
                return None
            review = (
                await self._equipment_review(session=session, user_id=user.id)
                if state.current_step is ProfileSettingsStep.EQUIPMENT
                else None
            )
            raw_selected = dict(state.pending_answers).get("selected", [])
            selected = (
                {str(value) for value in raw_selected if isinstance(value, str)}
                if isinstance(raw_selected, list)
                else set()
            )
            return ProfileSettingsResult(
                step=state.current_step,
                pending=cast(dict[str, JsonValue], state.pending_answers),
                current_value=await self._profile_setting_current_value(
                    session=session,
                    user_id=user.id,
                    step=state.current_step,
                ),
                equipment_review=(
                    self._with_selection(review, selected)
                    if review is not None
                    else None
                ),
            )

    async def open_profile_settings(
        self, identity: TelegramIdentity
    ) -> ProfileSettingsResult:
        async with self._session_factory.begin() as session:
            user, onboarding = await self._locked_state(session, identity)
            if onboarding.status is not OnboardingStatus.COMPLETED:
                raise OnboardingApplicationError("stale_action")
            state = await ProfileSettingsRepository(session).save(
                user_id=user.id, step=ProfileSettingsStep.MENU, pending={}
            )
            return ProfileSettingsResult(step=state.current_step)

    async def choose_profile_settings(
        self, identity: TelegramIdentity, action: str
    ) -> ProfileSettingsResult:
        """Apply a stable ps:v1 callback. It never calls a model."""

        async with self._session_factory.begin() as session:
            user, onboarding = await self._locked_state(session, identity)
            if onboarding.status is not OnboardingStatus.COMPLETED:
                raise OnboardingApplicationError("stale_action")
            settings_repo = ProfileSettingsRepository(session)
            state = await settings_repo.get_or_create(user_id=user.id)
            step = state.current_step
            pending = dict(state.pending_answers)
            target = {
                "section:goal": ProfileSettingsStep.GOAL_MENU,
                "section:availability": ProfileSettingsStep.AVAILABILITY,
                "section:health": ProfileSettingsStep.HEALTH,
                "section:personal": ProfileSettingsStep.PERSONAL_MENU,
                "personal:birth_year": ProfileSettingsStep.PERSONAL_BIRTH_YEAR,
                "personal:weight": ProfileSettingsStep.PERSONAL_WEIGHT,
                "personal:height": ProfileSettingsStep.PERSONAL_HEIGHT,
            }.get(action)
            if action == "done":
                state = await settings_repo.save(
                    user_id=user.id, step=ProfileSettingsStep.MENU, pending={}
                )
                return ProfileSettingsResult(
                    step=state.current_step, saved_field="__closed__"
                )
            if action == "back":
                state = await settings_repo.save(
                    user_id=user.id, step=ProfileSettingsStep.MENU, pending={}
                )
                return ProfileSettingsResult(
                    step=state.current_step, saved_field="__closed__"
                )
            if target is not None:
                state = await settings_repo.save(
                    user_id=user.id,
                    step=target,
                    pending={} if target is ProfileSettingsStep.GOAL_MAIN else pending,
                )
                return ProfileSettingsResult(
                    step=state.current_step,
                    current_value=await self._profile_setting_current_value(
                        session=session,
                        user_id=user.id,
                        step=state.current_step,
                    ),
                )
            goal_target = {
                "goal:main": ProfileSettingsStep.GOAL_MAIN,
                "goal:outcome": ProfileSettingsStep.GOAL_OUTCOME,
                "goal:date": ProfileSettingsStep.GOAL_DATE,
                "goal:secondary": ProfileSettingsStep.GOAL_SECONDARY,
            }.get(action)
            if goal_target is not None:
                if step is not ProfileSettingsStep.GOAL_MENU:
                    raise OnboardingApplicationError("stale_action")
                goal = await ProfileRepository(session).get_training_goal(
                    user_id=user.id
                )
                if goal is None:
                    raise OnboardingApplicationError("stale_action")
                pending = self._profile_goal_pending(goal)
                state = await settings_repo.save(
                    user_id=user.id,
                    step=goal_target,
                    pending=pending,
                )
                return ProfileSettingsResult(
                    step=state.current_step,
                    pending=cast(dict[str, JsonValue], pending),
                    current_value=await self._profile_setting_current_value(
                        session=session,
                        user_id=user.id,
                        step=state.current_step,
                    ),
                )
            if action == "goal:back":
                if step not in {
                    ProfileSettingsStep.GOAL_MENU,
                    ProfileSettingsStep.GOAL_MAIN,
                    ProfileSettingsStep.GOAL_OUTCOME,
                    ProfileSettingsStep.GOAL_DATE,
                    ProfileSettingsStep.GOAL_SECONDARY,
                }:
                    raise OnboardingApplicationError("stale_action")
                next_step = (
                    ProfileSettingsStep.MENU
                    if step is ProfileSettingsStep.GOAL_MENU
                    else ProfileSettingsStep.GOAL_MENU
                )
                state = await settings_repo.save(
                    user_id=user.id,
                    step=next_step,
                    pending={},
                )
                return ProfileSettingsResult(step=state.current_step)
            if action == "personal:gender":
                if step is not ProfileSettingsStep.PERSONAL_MENU:
                    raise OnboardingApplicationError("stale_action")
                state = await settings_repo.save(
                    user_id=user.id,
                    step=ProfileSettingsStep.PERSONAL_GENDER,
                    pending=pending,
                )
                return ProfileSettingsResult(
                    step=state.current_step,
                    current_value=await self._profile_setting_current_value(
                        session=session,
                        user_id=user.id,
                        step=state.current_step,
                    ),
                )
            if action.startswith("personal:gender:"):
                if step is not ProfileSettingsStep.PERSONAL_GENDER:
                    raise OnboardingApplicationError("stale_action")
                try:
                    gender = AthleteGender(action.rsplit(":", 1)[1])
                except ValueError as exc:
                    raise OnboardingApplicationError("invalid_action") from exc
                await ProfileRepository(session).update_athlete_profile_fields(
                    user_id=user.id, payload={"gender": gender}
                )
                state = await settings_repo.save(
                    user_id=user.id, step=ProfileSettingsStep.PERSONAL_MENU, pending={}
                )
                return ProfileSettingsResult(
                    step=state.current_step, saved_field="Category"
                )
            if action == "health:none":
                if step is not ProfileSettingsStep.HEALTH:
                    raise OnboardingApplicationError("stale_action")
                await ProfileRepository(session).update_athlete_profile_context_fields(
                    user_id=user.id,
                    payload={"health_limitations_text": "NONE_REPORTED"},
                )
                state = await settings_repo.save(
                    user_id=user.id, step=ProfileSettingsStep.MENU, pending={}
                )
                return ProfileSettingsResult(
                    step=state.current_step, saved_field="Health limitations"
                )
            if action == "section:equipment":
                return await self._open_profile_equipment(
                    session, user.id, settings_repo
                )
            if action.startswith("equipment:"):
                return await self._choose_profile_equipment(
                    session,
                    user.id,
                    settings_repo,
                    state,
                    action.removeprefix("equipment:"),
                )
            if action == "goal:no-date":
                if step is not ProfileSettingsStep.GOAL_DATE:
                    raise OnboardingApplicationError("stale_action")
                pending["event_date"] = None
                return await self._save_profile_goal(
                    session=session,
                    user_id=user.id,
                    repo=settings_repo,
                    pending=pending,
                )
            if action == "goal:no-secondary":
                if step is not ProfileSettingsStep.GOAL_SECONDARY:
                    raise OnboardingApplicationError("stale_action")
                pending["secondary_priority"] = None
                return await self._save_profile_goal(
                    session=session,
                    user_id=user.id,
                    repo=settings_repo,
                    pending=pending,
                )
            raise OnboardingApplicationError("invalid_action")

    async def submit_profile_settings_text(
        self, identity: TelegramIdentity, text: str
    ) -> ProfileSettingsResult | None:
        """Persist text only when the athlete selected the matching mini-flow."""

        async with self._session_factory.begin() as session:
            user, onboarding = await self._locked_state(session, identity)
            if onboarding.status is not OnboardingStatus.COMPLETED:
                return None
            repo = ProfileSettingsRepository(session)
            state = await repo.get_or_create(user_id=user.id)
            step, pending = state.current_step, dict(state.pending_answers)
            if step is ProfileSettingsStep.MENU:
                return None
            if step is ProfileSettingsStep.GOAL_MAIN:
                if not text or len(text) > 500:
                    raise OnboardingApplicationError("invalid_goal")
                pending["main_goal"] = text
                return await self._save_profile_goal(
                    session=session,
                    user_id=user.id,
                    repo=repo,
                    pending=pending,
                )
            if step is ProfileSettingsStep.GOAL_OUTCOME:
                if not text or len(text) > 500:
                    raise OnboardingApplicationError("invalid_goal")
                pending["target_outcome"] = text
                return await self._save_profile_goal(
                    session=session,
                    user_id=user.id,
                    repo=repo,
                    pending=pending,
                )
            if step is ProfileSettingsStep.GOAL_DATE:
                try:
                    event_date = date.fromisoformat(text)
                except ValueError as exc:
                    raise OnboardingApplicationError("invalid_event_date") from exc
                pending["event_date"] = event_date.isoformat()
                return await self._save_profile_goal(
                    session=session,
                    user_id=user.id,
                    repo=repo,
                    pending=pending,
                )
            if step is ProfileSettingsStep.GOAL_SECONDARY:
                if not text or len(text) > 500:
                    raise OnboardingApplicationError("invalid_goal")
                pending["secondary_priority"] = text
                return await self._save_profile_goal(
                    session=session,
                    user_id=user.id,
                    repo=repo,
                    pending=pending,
                )
            if step is ProfileSettingsStep.AVAILABILITY:
                await ProfileRepository(session).update_athlete_profile_context_fields(
                    user_id=user.id, payload={"availability_text": text}
                )
                state = await repo.save(
                    user_id=user.id, step=ProfileSettingsStep.MENU, pending={}
                )
                return ProfileSettingsResult(
                    step=state.current_step, saved_field="Availability"
                )
            if step is ProfileSettingsStep.HEALTH:
                await ProfileRepository(session).update_athlete_profile_context_fields(
                    user_id=user.id, payload={"health_limitations_text": text}
                )
                state = await repo.save(
                    user_id=user.id, step=ProfileSettingsStep.MENU, pending={}
                )
                return ProfileSettingsResult(
                    step=state.current_step, saved_field="Health limitations"
                )
            numeric = {
                ProfileSettingsStep.PERSONAL_BIRTH_YEAR: (
                    "birth_year",
                    1940,
                    utc_now().year - 16,
                    "Birth year",
                ),
                ProfileSettingsStep.PERSONAL_WEIGHT: ("weight_kg", 35, 250, "Weight"),
                ProfileSettingsStep.PERSONAL_HEIGHT: ("height_cm", 120, 230, "Height"),
            }.get(step)
            if numeric is not None:
                field, minimum, maximum, label = numeric
                value = (
                    self._parse_weight(text)
                    if field == "weight_kg"
                    else self._parse_integer(text, minimum=minimum, maximum=maximum)
                )
                if value is None:
                    raise OnboardingApplicationError("invalid_action")
                payload: dict[str, object] = {field: value}
                if field == "birth_year":
                    payload["age"] = utc_now().year - int(value)
                await ProfileRepository(session).update_athlete_profile_fields(
                    user_id=user.id, payload=payload
                )
                state = await repo.save(
                    user_id=user.id, step=ProfileSettingsStep.PERSONAL_MENU, pending={}
                )
                return ProfileSettingsResult(step=state.current_step, saved_field=label)
            raise OnboardingApplicationError("stale_action")

    @staticmethod
    async def _profile_setting_current_value(
        *,
        session: AsyncSession,
        user_id: uuid.UUID,
        step: ProfileSettingsStep,
    ) -> str | int | float | None:
        profiles = ProfileRepository(session)
        if step in {
            ProfileSettingsStep.GOAL_MAIN,
            ProfileSettingsStep.GOAL_OUTCOME,
            ProfileSettingsStep.GOAL_DATE,
            ProfileSettingsStep.GOAL_SECONDARY,
        }:
            goal = await profiles.get_training_goal(user_id=user_id)
            if goal is None:
                return None
            if step is ProfileSettingsStep.GOAL_MAIN:
                return goal.main_goal
            if step is ProfileSettingsStep.GOAL_OUTCOME:
                return goal.target_outcome
            if step is ProfileSettingsStep.GOAL_DATE:
                return (
                    goal.event_date.isoformat() if goal.event_date is not None else None
                )
            if step is ProfileSettingsStep.GOAL_SECONDARY:
                return goal.secondary_priority
        profile = await profiles.get_athlete_profile(user_id=user_id)
        if profile is None:
            return None
        values: dict[ProfileSettingsStep, str | int | float | None] = {
            ProfileSettingsStep.AVAILABILITY: profile.availability_text,
            ProfileSettingsStep.HEALTH: profile.health_limitations_text,
            ProfileSettingsStep.PERSONAL_BIRTH_YEAR: profile.birth_year,
            ProfileSettingsStep.PERSONAL_GENDER: (
                profile.gender.value if profile.gender is not None else None
            ),
            ProfileSettingsStep.PERSONAL_WEIGHT: (
                float(profile.weight_kg) if profile.weight_kg is not None else None
            ),
            ProfileSettingsStep.PERSONAL_HEIGHT: (
                float(profile.height_cm) if profile.height_cm is not None else None
            ),
        }
        return values.get(step)

    @staticmethod
    def _profile_goal_pending(goal: TrainingGoal) -> dict[str, object]:
        return {
            "main_goal": goal.main_goal,
            "target_outcome": goal.target_outcome,
            "event_date": (
                goal.event_date.isoformat() if goal.event_date is not None else None
            ),
            "secondary_priority": goal.secondary_priority,
        }

    async def _save_profile_goal(
        self,
        *,
        session: AsyncSession,
        user_id: uuid.UUID,
        repo: ProfileSettingsRepository,
        pending: dict[str, object],
    ) -> ProfileSettingsResult:
        main_goal, outcome = pending.get("main_goal"), pending.get("target_outcome")
        secondary = pending.get("secondary_priority")
        raw_event_date = pending.get("event_date")
        event_date = (
            date.fromisoformat(raw_event_date)
            if isinstance(raw_event_date, str)
            else None
        )
        if (
            not isinstance(main_goal, str)
            or not isinstance(outcome, str)
            or (secondary is not None and not isinstance(secondary, str))
        ):
            raise OnboardingApplicationError("stale_action")
        profiles = ProfileRepository(session)
        previous = await profiles.get_training_goal(user_id=user_id)
        if previous is None:
            raise OnboardingApplicationError("stale_action")
        changed = (
            previous.main_goal,
            previous.target_outcome,
            previous.event_date,
            previous.secondary_priority,
        ) != (main_goal, outcome, event_date, secondary)
        equipment_context_changed = (
            previous.main_goal,
            previous.target_outcome,
            previous.secondary_priority,
        ) != (main_goal, outcome, secondary)
        await profiles.update_training_goal_fields(
            user_id=user_id,
            payload={
                "main_goal": main_goal,
                "target_outcome": outcome,
                "event_date": event_date,
                "secondary_priority": secondary,
            },
        )
        if changed and equipment_context_changed:
            goal = await profiles.get_training_goal(user_id=user_id)
            return await self._open_profile_equipment(
                session, user_id, repo, goal=goal, saved_field="Goal"
            )
        state = await repo.save(
            user_id=user_id, step=ProfileSettingsStep.MENU, pending={}
        )
        return ProfileSettingsResult(step=state.current_step, saved_field="Goal")

    async def _open_profile_equipment(
        self,
        session: AsyncSession,
        user_id: uuid.UUID,
        repo: ProfileSettingsRepository,
        goal: TrainingGoal | None = None,
        saved_field: str | None = None,
    ) -> ProfileSettingsResult:
        goal = goal or await ProfileRepository(session).get_training_goal(
            user_id=user_id
        )
        if goal is None:
            raise OnboardingApplicationError("stale_action")
        review = await EquipmentRecommendationService().review(
            repository=EquipmentRepository(session),
            athlete_id=user_id,
            main_goal=goal.main_goal,
            target_outcome=goal.target_outcome,
            secondary_priority=goal.secondary_priority,
        )
        if review is None:
            state = await repo.save(
                user_id=user_id, step=ProfileSettingsStep.MENU, pending={}
            )
            return ProfileSettingsResult(
                step=state.current_step, saved_field=saved_field or "Equipment & access"
            )
        selected = [str(item.id) for item in review.options if item.selected]
        pending: dict[str, object] = {
            "selected": selected,
        }
        state = await repo.save(
            user_id=user_id, step=ProfileSettingsStep.EQUIPMENT, pending=pending
        )
        return ProfileSettingsResult(
            step=state.current_step,
            pending=cast(dict[str, JsonValue], pending),
            saved_field=saved_field,
            equipment_review=review,
        )

    async def _choose_profile_equipment(
        self,
        session: AsyncSession,
        user_id: uuid.UUID,
        repo: ProfileSettingsRepository,
        state: ProfileSettingsSession,
        choice: str,
    ) -> ProfileSettingsResult:
        if state.current_step is not ProfileSettingsStep.EQUIPMENT:
            raise OnboardingApplicationError("stale_action")
        pending = dict(state.pending_answers)
        review = await self._equipment_review(session=session, user_id=user_id)
        if review is None:
            raise OnboardingApplicationError("stale_action")
        ids = {str(item.id) for item in review.options}
        raw_selected = pending.get("selected", [])
        if not isinstance(raw_selected, list):
            raise OnboardingApplicationError("stale_action")
        selected = {value for value in raw_selected if isinstance(value, str)}
        selected.intersection_update(ids)
        if choice == "done":
            summary = await EquipmentRecommendationService().save_and_summarize(
                repository=EquipmentRepository(session),
                athlete_id=user_id,
                review=review,
                selected_ids={uuid.UUID(value) for value in selected},
            )
            saved = await repo.save(
                user_id=user_id, step=ProfileSettingsStep.MENU, pending={}
            )
            return ProfileSettingsResult(
                step=saved.current_step,
                saved_field="Equipment & access",
                equipment_summary=summary,
            )
        if choice not in ids:
            raise OnboardingApplicationError("invalid_action")
        selected.symmetric_difference_update({choice})
        pending["selected"] = sorted(selected)
        saved = await repo.save(
            user_id=user_id, step=ProfileSettingsStep.EQUIPMENT, pending=pending
        )
        return ProfileSettingsResult(
            step=saved.current_step,
            pending=cast(dict[str, JsonValue], pending),
            equipment_review=self._with_selection(review, selected),
        )

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
                if choice == "NOT_YET":
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
        """Route text to deterministic profile validation or focused goal extraction."""

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
            if (
                goal_phase == _GOAL_PHASE_CLARIFYING
                and dict(onboarding.answers).get(_GOAL_CLARIFICATION_FIELD_KEY)
                == "event_date"
            ):
                return await self._handle_goal_event_date(
                    identity=identity,
                    user_id=user.id,
                    text=text,
                )
            return await self._extract_goal(
                identity=identity,
                user_id=user.id,
                text=text,
            )
        if onboarding.status is OnboardingStatus.ACTIVE and onboarding.current_step in {
            OnboardingStep.PROFILE_BIRTH_YEAR_INTAKE,
            OnboardingStep.PROFILE_WEIGHT_INTAKE,
            OnboardingStep.PROFILE_HEIGHT_INTAKE,
        }:
            return await self._handle_profile_text(identity, text)
        if (
            onboarding.status is OnboardingStatus.ACTIVE
            and onboarding.current_step in _FREE_TEXT_CONTEXT_STEPS
        ):
            return await self._handle_context_text(
                identity=identity,
                user_id=user.id,
                text=text,
                step=onboarding.current_step,
            )
        if (
            onboarding.status is OnboardingStatus.ACTIVE
            and onboarding.current_step is OnboardingStep.EQUIPMENT_RECOMMENDATION
        ):
            # A normal message is a safe resume affordance for an interrupted
            # deterministic catalog lookup.
            return await self._prepare_equipment_review(user_id=user.id)
        if onboarding.status is OnboardingStatus.COMPLETED:
            # Completed-profile changes must enter the explicit ps:v1 mini-flow;
            # never infer or persist an update from an unprompted message.
            raise OnboardingApplicationError("profile_settings_required")
        raise OnboardingApplicationError("invalid_action")

    async def _handle_goal_event_date(
        self,
        *,
        identity: TelegramIdentity,
        user_id: uuid.UUID,
        text: str,
    ) -> OnboardingServiceResult:
        """Apply the date clarification without sending it to the LLM."""

        event_date: date | None = None
        if _ISO_DATE_PATTERN.fullmatch(text) is not None:
            try:
                parsed_date = date.fromisoformat(text)
            except ValueError:
                parsed_date = None
            if parsed_date is not None and parsed_date > utc_now().date():
                event_date = parsed_date

        async with self._session_factory.begin() as session:
            user, onboarding = await self._locked_state(session, identity)
            self._require_active(onboarding)
            if user.id != user_id:
                raise OnboardingApplicationError("user_not_found")
            answers = self._answers(onboarding)
            self._require_goal_phase(
                onboarding,
                answers,
                {_GOAL_PHASE_CLARIFYING},
            )
            if answers.get(_GOAL_CLARIFICATION_FIELD_KEY) != "event_date":
                raise OnboardingApplicationError("stale_action")
            if event_date is None:
                return self._result(
                    user,
                    onboarding,
                    kind="goal_clarification",
                    error_code="invalid_event_date",
                )
            draft = cast(
                GoalExtractionOutput,
                self._goal_draft_from_answers(answers, required=True),
            )
            updated = draft.model_copy(
                update={
                    "event_date": event_date,
                    "missing_fields": [
                        item for item in draft.missing_fields if item != "event_date"
                    ],
                    "ambiguous_fields": [
                        item for item in draft.ambiguous_fields if item != "event_date"
                    ],
                }
            )
            self._stage_goal_draft(answers, updated)
            onboarding = await OnboardingRepository(session).save_progress(
                user_id=user.id,
                current_step=OnboardingStep.GOAL_INTAKE,
                answers=cast(dict[str, object], answers),
            )
            return self._result(user, onboarding)

    async def update_onboarding_data(
        self,
        *,
        user_id: uuid.UUID,
        payload: Mapping[str, JsonValue],
    ) -> UpdatedOnboardingData:
        """Validate and route one sparse athlete-data update across owned tables."""

        clean_payload = self._validate_onboarding_update_payload(payload)
        athlete_profile_payload = {
            key: value
            for key, value in clean_payload.items()
            if key in _ATHLETE_PROFILE_UPDATE_FIELDS
        }
        training_goal_payload = {
            key: value
            for key, value in clean_payload.items()
            if key in _TRAINING_GOAL_UPDATE_FIELDS
        }
        athlete_profile_context_payload = {
            key: value
            for key, value in clean_payload.items()
            if key in _ATHLETE_PROFILE_CONTEXT_UPDATE_FIELDS
        }
        repository_goal_payload: dict[str, object] = dict(training_goal_payload)
        event_date_value = repository_goal_payload.get("event_date")
        if isinstance(event_date_value, str):
            repository_goal_payload["event_date"] = date.fromisoformat(event_date_value)
        refresh_equipment_review = False
        async with self._session_factory.begin() as session:
            profiles = ProfileRepository(session)
            owner = await profiles.lock_owner(user_id=user_id)
            profile = await profiles.get_athlete_profile(user_id=user_id)
            onboarding_repository = OnboardingRepository(session)
            onboarding = await onboarding_repository.get_for_user(
                user_id=user_id,
                for_update=True,
            )
            if athlete_profile_payload:
                if profile is None:
                    if (
                        onboarding is None
                        or onboarding.status is not OnboardingStatus.ACTIVE
                    ):
                        raise OnboardingApplicationError("invalid_onboarding_update")
                    unsupported_staged = set(athlete_profile_payload) - {
                        _BIRTH_YEAR_KEY,
                        _GENDER_KEY,
                        _WEIGHT_KG_KEY,
                        _HEIGHT_CM_KEY,
                    }
                    if unsupported_staged:
                        raise OnboardingApplicationError("invalid_onboarding_update")
                    answers = self._answers(onboarding)
                    answers.update(athlete_profile_payload)
                    await onboarding_repository.save_progress(
                        user_id=user_id,
                        current_step=onboarding.current_step,
                        answers=cast(dict[str, object], answers),
                    )
                else:
                    repository_profile_payload: dict[str, object] = dict(
                        athlete_profile_payload
                    )
                    birth_year_value = repository_profile_payload.get("birth_year")
                    if isinstance(birth_year_value, int):
                        repository_profile_payload["age"] = (
                            utc_now().year - birth_year_value
                        )
                    gender_value = repository_profile_payload.get("gender")
                    if isinstance(gender_value, str):
                        repository_profile_payload["gender"] = AthleteGender(
                            gender_value
                        )
                    await profiles.update_athlete_profile_fields(
                        user_id=user_id,
                        payload=repository_profile_payload,
                    )
            if athlete_profile_context_payload:
                if profile is None:
                    raise OnboardingApplicationError("invalid_onboarding_update")
                await profiles.update_athlete_profile_context_fields(
                    user_id=user_id,
                    payload=dict(athlete_profile_context_payload),
                )
            if training_goal_payload:
                existing_goal = await profiles.get_training_goal(user_id=user_id)
                if existing_goal is None:
                    raise OnboardingApplicationError("invalid_onboarding_update")
                refresh_equipment_review = self._training_goal_changed(
                    goal=existing_goal,
                    payload=repository_goal_payload,
                )
                await profiles.update_training_goal_fields(
                    user_id=user_id,
                    payload=repository_goal_payload,
                )
            if refresh_equipment_review:
                if onboarding is None:
                    onboarding, _ = await onboarding_repository.get_or_create(
                        user_id=user_id,
                    )
                answers = self._answers(onboarding)
                answers.pop(_CONTEXT_RETRY_ERROR_KEY, None)
                onboarding.status = OnboardingStatus.ACTIVE
                await onboarding_repository.save_progress(
                    user_id=user_id,
                    current_step=OnboardingStep.EQUIPMENT_RECOMMENDATION,
                    answers=cast(dict[str, object], answers),
                )
                await UserRepository(session).update_status(
                    user_id=owner.id,
                    status=UserStatus.ONBOARDING_IN_PROGRESS,
                )
        if refresh_equipment_review:
            # Commit the goal change before rebuilding the durable review.
            await self._prepare_equipment_review(user_id=user_id)
        return UpdatedOnboardingData(updated_fields=clean_payload)

    @classmethod
    def _validate_onboarding_update_payload(
        cls,
        payload: Mapping[str, JsonValue],
    ) -> dict[str, JsonValue]:
        if not payload:
            raise OnboardingApplicationError("empty_onboarding_update")
        unknown_fields = set(payload) - _ONBOARDING_UPDATE_FIELDS
        if unknown_fields:
            raise OnboardingApplicationError("invalid_onboarding_update")

        clean_payload: dict[str, JsonValue] = {}
        for key, value in payload.items():
            if value is None:
                continue
            if key in {"main_goal", "target_outcome"}:
                if not isinstance(value, str):
                    raise OnboardingApplicationError("invalid_onboarding_update")
                clean_payload[key] = cls._normalize_update_text(
                    value,
                    maximum=500,
                )
            elif key == "age":
                if (
                    not isinstance(value, int)
                    or isinstance(value, bool)
                    or not 16 <= value <= 100
                ):
                    raise OnboardingApplicationError("invalid_onboarding_update")
                clean_payload[key] = value
            elif key == "birth_year":
                if (
                    not isinstance(value, int)
                    or isinstance(value, bool)
                    or not 1940 <= value <= 2008
                ):
                    raise OnboardingApplicationError("invalid_onboarding_update")
                clean_payload[key] = value
            elif key == "gender":
                if not isinstance(value, str):
                    raise OnboardingApplicationError("invalid_onboarding_update")
                try:
                    clean_payload[key] = AthleteGender(value).value
                except ValueError as exc:
                    raise OnboardingApplicationError(
                        "invalid_onboarding_update"
                    ) from exc
            elif key == "weight_kg":
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    raise OnboardingApplicationError("invalid_onboarding_update")
                weight_kg = float(value)
                if not 35.0 <= weight_kg <= 250.0:
                    raise OnboardingApplicationError("invalid_onboarding_update")
                clean_payload[key] = weight_kg
            elif key == "height_cm":
                if (
                    not isinstance(value, int)
                    or isinstance(value, bool)
                    or not 120 <= value <= 230
                ):
                    raise OnboardingApplicationError("invalid_onboarding_update")
                clean_payload[key] = value
            elif key in _ATHLETE_PROFILE_CONTEXT_UPDATE_FIELDS:
                if not isinstance(value, str):
                    raise OnboardingApplicationError("invalid_onboarding_update")
                # These fields retain the user's literal context rather than a
                # normalized/extracted representation.
                if not value.strip() or len(value) > 4096:
                    raise OnboardingApplicationError("invalid_onboarding_update")
                clean_payload[key] = value
            elif key == "event_date":
                if not isinstance(value, str):
                    raise OnboardingApplicationError("invalid_onboarding_update")
                try:
                    parsed_event_date = date.fromisoformat(value)
                except ValueError as exc:
                    raise OnboardingApplicationError(
                        "invalid_onboarding_update"
                    ) from exc
                clean_payload[key] = parsed_event_date.isoformat()
        if not clean_payload:
            raise OnboardingApplicationError("empty_onboarding_update")
        return clean_payload

    @staticmethod
    def _normalize_update_text(value: str, *, maximum: int) -> str:
        normalized = " ".join(value.split())
        if not normalized or len(normalized) > maximum:
            raise OnboardingApplicationError("invalid_onboarding_update")
        return normalized

    @staticmethod
    def _training_goal_changed(
        *,
        goal: object,
        payload: Mapping[str, object],
    ) -> bool:
        """Return whether a sparse conversational goal patch actually changed it."""

        return any(getattr(goal, field) != value for field, value in payload.items())

    async def _handle_profile_text(
        self,
        identity: TelegramIdentity,
        text: str,
    ) -> OnboardingServiceResult:
        """Validate and save mandatory profile values before goal intake."""

        async with self._session_factory.begin() as session:
            user, onboarding = await self._locked_state(session, identity)
            self._require_active(onboarding)
            answers = self._answers(onboarding)
            step = onboarding.current_step

            if step is OnboardingStep.PROFILE_BIRTH_YEAR_INTAKE:
                birth_year = self._parse_integer(
                    text,
                    minimum=1940,
                    maximum=2008,
                )
                if birth_year is None:
                    return self._result(
                        user,
                        onboarding,
                        kind="profile_validation_error",
                        error_code="invalid_birth_year",
                    )
                answers[_BIRTH_YEAR_KEY] = birth_year
                onboarding = await OnboardingRepository(session).save_progress(
                    user_id=user.id,
                    current_step=OnboardingStep.PROFILE_GENDER_INTAKE,
                    answers=cast(dict[str, object], answers),
                )
                return self._result(user, onboarding)

            if step is OnboardingStep.PROFILE_WEIGHT_INTAKE:
                weight_kg = self._parse_weight(text)
                if weight_kg is None:
                    return self._result(
                        user,
                        onboarding,
                        kind="profile_validation_error",
                        error_code="invalid_weight_kg",
                    )
                answers[_WEIGHT_KG_KEY] = weight_kg
                onboarding = await OnboardingRepository(session).save_progress(
                    user_id=user.id,
                    current_step=OnboardingStep.PROFILE_HEIGHT_INTAKE,
                    answers=cast(dict[str, object], answers),
                )
                return self._result(user, onboarding)

            if step is OnboardingStep.PROFILE_HEIGHT_INTAKE:
                height_cm = self._parse_integer(
                    text,
                    minimum=120,
                    maximum=230,
                )
                if height_cm is None:
                    return self._result(
                        user,
                        onboarding,
                        kind="profile_validation_error",
                        error_code="invalid_height_cm",
                    )
                staged_birth_year = answers.get(_BIRTH_YEAR_KEY)
                raw_gender = answers.get(_GENDER_KEY)
                staged_weight_kg = answers.get(_WEIGHT_KG_KEY)
                if (
                    not isinstance(staged_birth_year, int)
                    or isinstance(staged_birth_year, bool)
                    or not isinstance(raw_gender, str)
                    or not isinstance(staged_weight_kg, (int, float))
                    or isinstance(staged_weight_kg, bool)
                ):
                    raise OnboardingApplicationError("incomplete_profile")
                try:
                    gender = AthleteGender(raw_gender)
                except ValueError as exc:
                    raise OnboardingApplicationError("incomplete_profile") from exc
                answers[_HEIGHT_CM_KEY] = height_cm
                await ProfileRepository(session).upsert_mandatory_athlete_profile(
                    user_id=user.id,
                    birth_year=staged_birth_year,
                    gender=gender,
                    weight_kg=float(staged_weight_kg),
                    height_cm=float(height_cm),
                )
                goal = await ProfileRepository(session).get_training_goal(
                    user_id=user.id
                )
                has_confirmed_goal = goal is not None
                if not has_confirmed_goal:
                    answers[_GOAL_PHASE_KEY] = _GOAL_PHASE_COLLECTING
                onboarding = await OnboardingRepository(session).save_progress(
                    user_id=user.id,
                    current_step=(
                        OnboardingStep.AVAILABILITY_INTAKE
                        if has_confirmed_goal
                        else OnboardingStep.GOAL_INTAKE
                    ),
                    answers=cast(dict[str, object], answers),
                )
                return self._result(user, onboarding)

            raise OnboardingApplicationError("invalid_action")

    async def _handle_context_text(
        self,
        *,
        identity: TelegramIdentity,
        user_id: uuid.UUID,
        text: str,
        step: OnboardingStep,
    ) -> OnboardingServiceResult:
        """Validate raw context with LangGraph while retaining the literal input."""

        async with self._session_factory.begin() as session:
            user, onboarding = await self._locked_state(session, identity)
            self._require_active(onboarding)
            if user.id != user_id or onboarding.current_step is not step:
                raise OnboardingApplicationError("stale_action")
        try:
            validation = await self._context_workflow.validate_free_text(
                step=step,
                user_text=text,
            )
        except Exception:
            validation = FreeTextValidationWorkflowResult(
                outcome="provider_error",
                error_code="workflow_failure",
            )

        if validation.outcome != "accepted":
            async with self._session_factory.begin() as session:
                user, onboarding = await self._locked_state(session, identity)
                self._require_active(onboarding)
                if onboarding.current_step is not step:
                    raise OnboardingApplicationError("stale_action")
                # Keep the message itself out of answers, errors, usage records,
                # and observability metadata.  The athlete can simply retry.
                return self._result(
                    user,
                    onboarding,
                    kind="context_validation_error",
                    error_code=validation.error_code,
                )

        async with self._session_factory.begin() as session:
            user, onboarding = await self._locked_state(session, identity)
            self._require_active(onboarding)
            if onboarding.current_step is not step:
                raise OnboardingApplicationError("stale_action")
            profiles = ProfileRepository(session)
            answers = self._answers(onboarding)
            field_by_step = {
                OnboardingStep.AVAILABILITY_INTAKE: "availability_text",
                OnboardingStep.HEALTH_LIMITATIONS_INTAKE: "health_limitations_text",
            }
            next_step_by_step = {
                OnboardingStep.AVAILABILITY_INTAKE: (
                    OnboardingStep.EQUIPMENT_RECOMMENDATION
                ),
            }
            field = field_by_step.get(step)
            if field is None:
                raise OnboardingApplicationError("invalid_action")
            await profiles.update_athlete_profile_context_fields(
                user_id=user.id,
                # The original text is retained exactly; the workflow result is
                # only a go/no-go validation signal.
                payload={field: text},
            )
            if step is OnboardingStep.HEALTH_LIMITATIONS_INTAKE:
                return await self._complete_context_onboarding(
                    session=session,
                    user=user,
                    onboarding=onboarding,
                    answers=answers,
                )
            onboarding = await OnboardingRepository(session).save_progress(
                user_id=user.id,
                current_step=next_step_by_step[step],
                answers=cast(dict[str, object], answers),
            )
            saved_result = self._result(user, onboarding)

        if step is OnboardingStep.AVAILABILITY_INTAKE:
            # Availability is committed before the deterministic catalog lookup.
            return await self._prepare_equipment_review(user_id=user_id)
        return saved_result

    async def _prepare_equipment_review(
        self,
        *,
        user_id: uuid.UUID,
    ) -> OnboardingServiceResult:
        """Resolve the goal to a deterministic catalog review."""

        async with self._session_factory.begin() as session:
            user = await UserRepository(session).require_by_id(user_id=user_id)
            onboarding = await OnboardingRepository(session).lock_for_user(
                user_id=user.id
            )
            self._require_active(onboarding)
            if onboarding.current_step is not OnboardingStep.EQUIPMENT_RECOMMENDATION:
                raise OnboardingApplicationError("stale_action")
            goal = await ProfileRepository(session).get_training_goal(user_id=user.id)
            answers = self._answers(onboarding)
            if goal is None:
                raise OnboardingApplicationError("stale_action")
            review = await EquipmentRecommendationService().review(
                repository=EquipmentRepository(session),
                athlete_id=user.id,
                main_goal=goal.main_goal,
                target_outcome=goal.target_outcome,
                secondary_priority=goal.secondary_priority,
            )
            if review is None:
                answers.pop(_EQUIPMENT_SELECTION_KEY, None)
                onboarding = await OnboardingRepository(session).save_progress(
                    user_id=user.id,
                    current_step=OnboardingStep.HEALTH_LIMITATIONS_INTAKE,
                    answers=cast(dict[str, object], answers),
                )
                return self._result(user, onboarding, kind="equipment_unmatched")
            answers[_EQUIPMENT_SELECTION_KEY] = [
                str(item.id) for item in review.options if item.selected
            ]
            answers.pop(_CONTEXT_RETRY_ERROR_KEY, None)
            onboarding = await OnboardingRepository(session).save_progress(
                user_id=user.id,
                current_step=OnboardingStep.EQUIPMENT_INTAKE,
                answers=cast(dict[str, object], answers),
            )
            return self._result(user, onboarding, equipment_review=review)

    async def _equipment_review(
        self, *, session: AsyncSession, user_id: uuid.UUID
    ) -> EquipmentReview | None:
        goal = await ProfileRepository(session).get_training_goal(user_id=user_id)
        if goal is None:
            return None
        return await EquipmentRecommendationService().review(
            repository=EquipmentRepository(session),
            athlete_id=user_id,
            main_goal=goal.main_goal,
            target_outcome=goal.target_outcome,
            secondary_priority=goal.secondary_priority,
        )

    @staticmethod
    def _with_selection(review: EquipmentReview, selected: set[str]) -> EquipmentReview:
        return review.model_copy(
            update={
                "options": tuple(
                    item.model_copy(update={"selected": str(item.id) in selected})
                    for item in review.options
                )
            }
        )

    async def _complete_context_onboarding(
        self,
        *,
        session: AsyncSession,
        user: User,
        onboarding: OnboardingSession,
        answers: dict[str, JsonValue],
    ) -> OnboardingServiceResult:
        """Complete only after availability, equipment, and limitations are saved."""

        onboarding = await OnboardingRepository(session).save_progress(
            user_id=user.id,
            current_step=OnboardingStep.HEALTH_LIMITATIONS_INTAKE,
            answers=cast(dict[str, object], answers),
        )
        onboarding.status = OnboardingStatus.COMPLETED
        user = await UserRepository(session).update_status(
            user_id=user.id,
            status=UserStatus.ONBOARDING_COMPLETED,
        )
        await session.flush()
        return self._result(user, onboarding)

    @staticmethod
    def _parse_integer(text: str, *, minimum: int, maximum: int) -> int | None:
        candidate = text.strip()
        if _INTEGER_PATTERN.fullmatch(candidate) is None:
            return None
        value = int(candidate)
        return value if minimum <= value <= maximum else None

    @staticmethod
    def _parse_weight(text: str) -> float | None:
        candidate = text.strip()
        if _WEIGHT_PATTERN.fullmatch(candidate) is None:
            return None
        try:
            value = Decimal(candidate)
        except InvalidOperation:
            return None
        if not value.is_finite() or not Decimal("40.0") <= value <= Decimal("200.0"):
            return None
        return float(value)

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
            review = None
            if onboarding.current_step is OnboardingStep.EQUIPMENT_INTAKE:
                current = await self._equipment_review(
                    session=session,
                    user_id=user.id,
                )
                raw_selected = dict(onboarding.answers).get(
                    _EQUIPMENT_SELECTION_KEY,
                    [],
                )
                selected = (
                    {value for value in raw_selected if isinstance(value, str)}
                    if isinstance(raw_selected, list)
                    else set()
                )
                if current is not None:
                    review = self._with_selection(current, selected)
            return self._result(user, onboarding, equipment_review=review)

    async def _modify_onboarding_data(
        self,
        *,
        user_id: uuid.UUID,
        text: str,
    ) -> OnboardingServiceResult:
        """Invoke the agent while keeping the ownership-scoped write in this service."""

        async with self._session_factory.begin() as session:
            user = await UserRepository(session).require_by_id(user_id=user_id)
            onboarding = await OnboardingRepository(session).require_for_user(
                user_id=user_id
            )
            if onboarding.status is not OnboardingStatus.COMPLETED:
                raise OnboardingApplicationError("stale_action")
            usage_repository = LLMUsageRepository(session)
            if self._settings.llm_mode == "live":
                attempts = await usage_repository.count_since(
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
            usage = await usage_repository.record(
                user_id=user_id,
                onboarding_step=OnboardingStep.GOAL_CONFIRMED,
                provider_mode=self._settings.llm_mode,
                model=self._settings.llm_model,
                status=LLMUsageStatus.PROVIDER_ERROR,
            )
            usage_id = usage.id

        try:
            workflow = await self._goal_extractor.modify_onboarding_data(
                user_id=user_id,
                user_text=text,
                onboarding_updater=self.update_onboarding_data,
            )
        except Exception:
            workflow = None

        async with self._session_factory.begin() as session:
            user = await UserRepository(session).require_by_id(user_id=user_id)
            onboarding = await OnboardingRepository(session).require_for_user(
                user_id=user_id
            )
            usage_status = LLMUsageStatus.PROVIDER_ERROR
            if workflow is not None:
                if workflow.outcome == "onboarding_modified":
                    usage_status = LLMUsageStatus.SUCCEEDED
                elif workflow.outcome == "no_onboarding_update":
                    usage_status = LLMUsageStatus.CLARIFICATION
            await LLMUsageRepository(session).update_outcome(
                user_id=user_id,
                usage_id=usage_id,
                status=usage_status,
                prompt_tokens=None,
                completion_tokens=None,
            )
            if workflow is None or workflow.outcome == "provider_error":
                return self._result(
                    user,
                    onboarding,
                    kind="provider_error",
                    error_code=(
                        workflow.error_code
                        if workflow is not None
                        else "workflow_failure"
                    ),
                )
            if onboarding.status is OnboardingStatus.ACTIVE:
                # A goal change reopens equipment review.  Return its durable
                # checkpoint instead of a generic edit acknowledgement.
                return self._result(user, onboarding)
            return self._result(
                user,
                onboarding,
                kind="onboarding_modification",
                confirmation=workflow.confirmation,
                updated_fields=workflow.updated_fields,
            )

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
                    _GOAL_PHASE_CONFIRMING,
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
                current_date=date.today().isoformat(),
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
        confirmation: str | None = None,
        updated_fields: tuple[str, ...] = (),
        created: bool = False,
        equipment_review: EquipmentReview | None = None,
        equipment_summary: EquipmentSuggestionSummary | None = None,
    ) -> OnboardingServiceResult:
        answers = cls._answers(onboarding)
        if kind is None:
            if onboarding.status is OnboardingStatus.COMPLETED:
                kind = "onboarding_completed"
            elif onboarding.status is OnboardingStatus.CANCELLED:
                kind = "cancelled"
            elif onboarding.current_step is OnboardingStep.CONSENT:
                kind = "step"
            elif onboarding.current_step is OnboardingStep.SETUP_INTRODUCTION:
                kind = "setup_introduction"
            elif onboarding.current_step is OnboardingStep.GOAL_CONFIRMED:
                kind = "goal_confirmed"
            elif onboarding.current_step is OnboardingStep.PROFILE_BIRTH_YEAR_INTAKE:
                kind = "profile_birth_year_intake"
            elif onboarding.current_step is OnboardingStep.PROFILE_GENDER_INTAKE:
                kind = "profile_gender_intake"
            elif onboarding.current_step is OnboardingStep.PROFILE_WEIGHT_INTAKE:
                kind = "profile_weight_intake"
            elif onboarding.current_step is OnboardingStep.PROFILE_HEIGHT_INTAKE:
                kind = "profile_height_intake"
            elif onboarding.current_step is OnboardingStep.AVAILABILITY_INTAKE:
                kind = "availability_intake"
            elif onboarding.current_step is OnboardingStep.EQUIPMENT_RECOMMENDATION:
                kind = "equipment_recommendation"
            elif onboarding.current_step is OnboardingStep.EQUIPMENT_INTAKE:
                kind = "equipment_intake"
            elif onboarding.current_step is OnboardingStep.HEALTH_LIMITATIONS_INTAKE:
                kind = "health_limitations_intake"
            else:
                raw_phase = answers.get(_GOAL_PHASE_KEY)
                phase = raw_phase if isinstance(raw_phase, str) else None
                phase_kinds: dict[str, OnboardingResultKind] = {
                    _GOAL_PHASE_CLARIFYING: "goal_clarification",
                    _GOAL_PHASE_CONFIRMING: "goal_confirmation",
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
            confirmation=confirmation,
            updated_fields=updated_fields,
            created=created,
            equipment_review=equipment_review,
            equipment_summary=equipment_summary,
        )
