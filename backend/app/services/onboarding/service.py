"""Focused, durable onboarding through deterministic, menu-driven steps."""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import cast

from pydantic import JsonValue
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.base import utc_now
from app.db.models import OnboardingSession, ProfileSettingsSession, TrainingGoal, User
from app.domain.enums import (
    AthleteGender,
    GoalTemplateKind,
    OnboardingStatus,
    OnboardingStep,
    ProfileSettingsStep,
    UserStatus,
)
from app.repositories.apple_health import AppleHealthRepository
from app.repositories.athlete_capabilities import AthleteCapabilityRepository
from app.repositories.onboarding import OnboardingRepository
from app.repositories.profile_settings import ProfileSettingsRepository
from app.repositories.profiles import ProfileRepository
from app.repositories.training_catalog import TrainingCatalogRepository
from app.repositories.users import UserRepository
from app.schemas.capabilities import CapabilityReview, GoalExecutionAssessment
from app.schemas.common import TelegramIdentity
from app.schemas.onboarding_service import (
    OnboardingResultKind,
    OnboardingServiceResult,
    UpdatedOnboardingData,
)
from app.schemas.profile_settings import ProfileSettingsResult
from app.services.capabilities import CapabilityAssessmentService
from app.services.training_catalog.grouping import (
    GoalOption,
    GoalSport,
    group_goals_by_sport,
)

_GOAL_SPORT_KEY = "goal_sport"
_MAIN_GOAL_CHANGED_KEY = "main_goal_changed"
_BIRTH_YEAR_KEY = "birth_year"
_GENDER_KEY = "gender"
_WEIGHT_KG_KEY = "weight_kg"
_HEIGHT_CM_KEY = "height_cm"
_CAPABILITY_SELECTION_KEY = "capability_selection"
_CONTEXT_RETRY_ERROR_KEY = "_context_retry_error"
_INTEGER_PATTERN = re.compile(r"[0-9]+")
_WEIGHT_PATTERN = re.compile(r"[0-9]+(?:\.[0-9]+)?")
_ATHLETE_PROFILE_UPDATE_FIELDS = frozenset(
    {"age", "birth_year", "gender", "weight_kg", "height_cm"}
)
_TRAINING_GOAL_UPDATE_FIELDS = frozenset({"target_outcome", "event_date"})
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
# Availability and health text is stored exactly as the athlete typed it, so the
# only check is that it is neither empty nor absurd. A model was previously asked
# whether the answer was sensible; it judged nothing that mattered and changed
# nothing that was stored.
_CONTEXT_TEXT_MIN_LENGTH = 3
_CONTEXT_TEXT_MAX_LENGTH = 2000


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
        settings: Settings,
    ) -> None:
        self._session_factory = session_factory
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
            capability_review = None
            if onboarding.current_step is OnboardingStep.EQUIPMENT_INTAKE:
                review = await self._capability_review(
                    session=session,
                    user_id=user.id,
                )
                raw_selected = dict(onboarding.answers).get(
                    _CAPABILITY_SELECTION_KEY,
                    [],
                )
                selected = (
                    {value for value in raw_selected if isinstance(value, str)}
                    if isinstance(raw_selected, list)
                    else set()
                )
                if review is not None:
                    capability_review = self._with_selection(review, selected)
            return self._result(
                user,
                onboarding,
                created=created or onboarding_created,
                capability_review=capability_review,
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
            "history": OnboardingStep.TRAINING_HISTORY_IMPORT,
            "completed": OnboardingStep.TRAINING_HISTORY_IMPORT,
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
            development_template = await TrainingCatalogRepository(
                session
            ).active_goal_by_code(
                code="TRIATHLON_HALF_DISTANCE",
                kind=GoalTemplateKind.PRIMARY,
            )
            if development_template is None:
                raise OnboardingApplicationError("goal_template_not_found")
            await profiles.upsert_training_goal(
                user_id=user.id,
                main_goal="Complete an Ironman 70.3",
                event_date=None,
                target_outcome="Finish comfortably",
                secondary_priority=None,
                original_description="Development test goal",
                goal_template_id=development_template.id,
            )
            context: dict[str, str | None] = {
                "availability_text": None,
                "health_limitations_text": None,
            }
            if step_name in {"equipment", "limitations", "history", "completed"}:
                context["availability_text"] = "Weekdays one hour; weekends two hours."
            if step_name in {"history", "completed"}:
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

    async def reset_development_goal_and_equipment(
        self, identity: TelegramIdentity
    ) -> OnboardingServiceResult:
        """Reset just one development athlete's goal and capability answers."""

        await self.start(identity)
        async with self._session_factory.begin() as session:
            user, onboarding = await self._locked_state(session, identity)
            active_job = await AppleHealthRepository(session).get_active_job(
                user_id=user.id
            )
            if active_job is not None:
                raise OnboardingApplicationError("import_already_active")
            await ProfileRepository(session).delete_training_goal(user_id=user.id)
            await AthleteCapabilityRepository(session).clear_for_athlete(
                athlete_id=user.id
            )
            await ProfileSettingsRepository(session).save(
                user_id=user.id,
                step=ProfileSettingsStep.MENU,
                pending={},
            )
            onboarding.status = OnboardingStatus.ACTIVE
            onboarding.current_step = OnboardingStep.GOAL_INTAKE
            onboarding.answers = {"consent": True}
            user = await UserRepository(session).update_status(
                user_id=user.id,
                status=UserStatus.ONBOARDING_IN_PROGRESS,
            )
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

    async def choose_goal_sport(
        self,
        identity: TelegramIdentity,
        choice: str,
    ) -> OnboardingServiceResult:
        """Remember which sport the athlete is choosing a goal within."""

        try:
            sport = GoalSport(choice)
        except ValueError as exc:
            raise OnboardingApplicationError("invalid_action") from exc
        async with self._session_factory.begin() as session:
            user, onboarding = await self._locked_state(session, identity)
            self._require_active(onboarding)
            if onboarding.current_step is not OnboardingStep.GOAL_INTAKE:
                raise OnboardingApplicationError("stale_action")
            answers = self._answers(onboarding)
            answers[_GOAL_SPORT_KEY] = sport.value
            onboarding = await OnboardingRepository(session).save_progress(
                user_id=user.id,
                current_step=OnboardingStep.GOAL_INTAKE,
                answers=cast(dict[str, object], answers),
            )
            return self._result(user, onboarding)

    async def reopen_goal_sports(
        self, identity: TelegramIdentity
    ) -> OnboardingServiceResult:
        """Step back from the goal list to the sport list."""

        async with self._session_factory.begin() as session:
            user, onboarding = await self._locked_state(session, identity)
            self._require_active(onboarding)
            if onboarding.current_step is not OnboardingStep.GOAL_INTAKE:
                raise OnboardingApplicationError("stale_action")
            answers = self._answers(onboarding)
            answers.pop(_GOAL_SPORT_KEY, None)
            onboarding = await OnboardingRepository(session).save_progress(
                user_id=user.id,
                current_step=OnboardingStep.GOAL_INTAKE,
                answers=cast(dict[str, object], answers),
            )
            return self._result(user, onboarding)

    async def choose_goal_template(
        self,
        identity: TelegramIdentity,
        code: str,
    ) -> OnboardingServiceResult:
        """Persist the chosen primary goal, validated against the catalog."""

        async with self._session_factory.begin() as session:
            user, onboarding = await self._locked_state(session, identity)
            self._require_active(onboarding)
            if onboarding.current_step is not OnboardingStep.GOAL_INTAKE:
                raise OnboardingApplicationError("stale_action")
            template = await TrainingCatalogRepository(session).active_goal_by_code(
                code=code
            )
            # Callback data is not trustworthy. An unvalidated code would write
            # a dangling foreign key into training_goals.
            if template is None or template.kind is not GoalTemplateKind.PRIMARY:
                raise OnboardingApplicationError("invalid_action")
            await ProfileRepository(session).upsert_training_goal(
                user_id=user.id,
                main_goal=template.display_name,
                event_date=None,
                target_outcome=template.display_name,
                secondary_priority=None,
                original_description=template.display_name,
                goal_template_id=template.id,
                supporting_goal_template_id=None,
            )
            onboarding = await OnboardingRepository(session).save_progress(
                user_id=user.id,
                # Task 5b inserts a race-date step (GOAL_EVENT_DATE) between the
                # template choice and the supporting-goal offer. That step does
                # not exist yet, so this advances straight to GOAL_CONFIRMED;
                # Task 5b changes this to GOAL_EVENT_DATE once it lands.
                current_step=OnboardingStep.GOAL_CONFIRMED,
                answers=cast(dict[str, object], self._answers(onboarding)),
            )
            return self._result(user, onboarding)

    async def choose_supporting_goal(
        self,
        identity: TelegramIdentity,
        code: str | None,
    ) -> OnboardingServiceResult:
        """Attach an optional supporting goal, then leave the goal steps."""

        async with self._session_factory.begin() as session:
            user, onboarding = await self._locked_state(session, identity)
            self._require_active(onboarding)
            if onboarding.current_step is not OnboardingStep.GOAL_CONFIRMED:
                raise OnboardingApplicationError("stale_action")
            profiles = ProfileRepository(session)
            goal = await profiles.get_training_goal(user_id=user.id)
            if goal is None:
                raise OnboardingApplicationError("stale_action")
            supporting_id: uuid.UUID | None = None
            # Mirrors the primary goal's own display, which is stored as
            # main_goal/target_outcome above: the profile view has nothing
            # else to show for a supporting goal chosen from a fixed menu.
            secondary_priority: str | None = None
            if code is not None:
                template = await TrainingCatalogRepository(session).active_goal_by_code(
                    code=code
                )
                if template is None or template.kind is not GoalTemplateKind.SUPPORTING:
                    raise OnboardingApplicationError("invalid_action")
                supporting_id = template.id
                secondary_priority = template.display_name
            await profiles.upsert_training_goal(
                user_id=user.id,
                main_goal=goal.main_goal,
                event_date=goal.event_date,
                target_outcome=goal.target_outcome,
                secondary_priority=secondary_priority,
                original_description=goal.original_description,
                goal_template_id=goal.goal_template_id,
                supporting_goal_template_id=supporting_id,
            )
            onboarding = await OnboardingRepository(session).save_progress(
                user_id=user.id,
                # Every path into GOAL_INTAKE (fresh onboarding, and the
                # goal/equipment dev reset) already has a complete athlete
                # profile, so the next step is availability, not birth year.
                current_step=OnboardingStep.AVAILABILITY_INTAKE,
                answers=cast(dict[str, object], self._answers(onboarding)),
            )
            return self._result(user, onboarding)

    async def goal_sport_options(self) -> tuple[str, ...]:
        """Sports with at least one active primary goal, in a fixed order."""

        grouped = await self._grouped_primary_goals()
        return tuple(sport.value for sport in GoalSport if sport in grouped)

    async def goal_template_options(self, sport: str) -> tuple[tuple[str, str], ...]:
        """(code, display_name) pairs for the primary goals within one sport."""

        try:
            goal_sport = GoalSport(sport)
        except ValueError as exc:
            raise OnboardingApplicationError("invalid_action") from exc
        grouped = await self._grouped_primary_goals()
        return tuple(
            (option.code, option.display_name) for option in grouped.get(goal_sport, ())
        )

    async def supporting_goal_options(self) -> tuple[tuple[str, str], ...]:
        """(code, display_name) pairs for every active supporting goal."""

        async with self._session_factory() as session:
            templates = await TrainingCatalogRepository(session).active_goal_templates()
        return tuple(
            (item.code, item.display_name)
            for item in templates
            if item.kind is GoalTemplateKind.SUPPORTING
        )

    async def _grouped_primary_goals(self) -> dict[GoalSport, tuple[GoalOption, ...]]:
        async with self._session_factory() as session:
            rows = await TrainingCatalogRepository(
                session
            ).active_primary_goal_target_disciplines()
        options = tuple(
            GoalOption(code=code, display_name=display_name, disciplines=disciplines)
            for code, display_name, disciplines in rows
        )
        return group_goals_by_sport(options)

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
            goal = await ProfileRepository(session).get_training_goal(user_id=user.id)
            if goal is None:
                raise OnboardingApplicationError("stale_action")
            review = await self._capability_review(session=session, user_id=user.id)
            if review is None:
                raise OnboardingApplicationError("stale_action")
            resource_ids = {str(item.id) for item in review.options}
            raw_selected = answers.get(_CAPABILITY_SELECTION_KEY, [])
            if not isinstance(raw_selected, list):
                raw_selected = []
            selected = {str(value) for value in raw_selected if isinstance(value, str)}
            selected.intersection_update(resource_ids)
            assessment: GoalExecutionAssessment | None = None
            if choice == "done":
                if not resource_ids:
                    raise OnboardingApplicationError("stale_action")
                assessment = await CapabilityAssessmentService().save_and_assess(
                    catalog=TrainingCatalogRepository(session),
                    athlete_capabilities=AthleteCapabilityRepository(session),
                    athlete_id=user.id,
                    goal_template_id=goal.goal_template_id,
                    supporting_goal_template_id=goal.supporting_goal_template_id,
                    review=review,
                    selected_ids={uuid.UUID(value) for value in selected},
                )
                current_step = OnboardingStep.HEALTH_LIMITATIONS_INTAKE
            elif choice in resource_ids:
                if choice in selected:
                    selected.remove(choice)
                else:
                    selected.add(choice)
                answers[_CAPABILITY_SELECTION_KEY] = cast(JsonValue, sorted(selected))
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
                capability_review=(
                    self._with_selection(review, selected)
                    if current_step is OnboardingStep.EQUIPMENT_INTAKE
                    else None
                ),
                execution_assessment=(
                    assessment
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
            return await self._advance_to_training_history(
                session=session,
                user=user,
                onboarding=onboarding,
                answers=answers,
            )

    async def skip_training_history(
        self,
        identity: TelegramIdentity,
    ) -> OnboardingServiceResult:
        """Complete onboarding after an explicit, deterministic history skip."""

        async with self._session_factory.begin() as session:
            user, onboarding = await self._locked_state(session, identity)
            self._require_active(onboarding)
            if onboarding.current_step is not OnboardingStep.TRAINING_HISTORY_IMPORT:
                raise OnboardingApplicationError("stale_action")
            active_job = await AppleHealthRepository(session).get_active_job(
                user_id=user.id
            )
            if active_job is not None:
                raise OnboardingApplicationError("import_already_active")
            onboarding.status = OnboardingStatus.COMPLETED
            user = await UserRepository(session).update_status(
                user_id=user.id,
                status=UserStatus.ONBOARDING_COMPLETED,
            )
            await session.flush()
            return self._result(user, onboarding, training_history_skipped=True)

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
                await self._capability_review(session=session, user_id=user.id)
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
                capability_review=(
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
            goal_target = {
                "goal:outcome": ProfileSettingsStep.GOAL_OUTCOME,
                "goal:date": ProfileSettingsStep.GOAL_DATE,
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
            if action in {"goal:main", "goal:secondary"}:
                # Both open the deterministic catalog menu fresh (no prefilled
                # pending text, unlike goal:outcome/goal:date above): the
                # primary and supporting goal are chosen from a fixed list,
                # not typed.
                if step is not ProfileSettingsStep.GOAL_MENU:
                    raise OnboardingApplicationError("stale_action")
                goal = await ProfileRepository(session).get_training_goal(
                    user_id=user.id
                )
                if goal is None:
                    raise OnboardingApplicationError("stale_action")
                next_step = (
                    ProfileSettingsStep.GOAL_MAIN
                    if action == "goal:main"
                    else ProfileSettingsStep.GOAL_SECONDARY
                )
                state = await settings_repo.save(
                    user_id=user.id,
                    step=next_step,
                    pending={},
                )
                return ProfileSettingsResult(step=state.current_step)
            if action.startswith("goal:sport:"):
                if step is not ProfileSettingsStep.GOAL_MAIN:
                    raise OnboardingApplicationError("stale_action")
                try:
                    sport = GoalSport(action.removeprefix("goal:sport:"))
                except ValueError as exc:
                    raise OnboardingApplicationError("invalid_action") from exc
                pending[_GOAL_SPORT_KEY] = sport.value
                state = await settings_repo.save(
                    user_id=user.id,
                    step=ProfileSettingsStep.GOAL_MAIN,
                    pending=pending,
                )
                return ProfileSettingsResult(
                    step=state.current_step,
                    pending=cast(dict[str, JsonValue], pending),
                )
            if action == "goal:main:back":
                # Steps from the template screen back to the sport screen,
                # staying inside GOAL_MAIN. `goal:back` below remains the
                # separate escape hatch out of the whole goal-editing flow.
                if step is not ProfileSettingsStep.GOAL_MAIN:
                    raise OnboardingApplicationError("stale_action")
                pending.pop(_GOAL_SPORT_KEY, None)
                state = await settings_repo.save(
                    user_id=user.id,
                    step=ProfileSettingsStep.GOAL_MAIN,
                    pending=pending,
                )
                return ProfileSettingsResult(step=state.current_step)
            if action.startswith("goal:template:"):
                if step is not ProfileSettingsStep.GOAL_MAIN:
                    raise OnboardingApplicationError("stale_action")
                code = action.removeprefix("goal:template:")
                template = await TrainingCatalogRepository(session).active_goal_by_code(
                    code=code
                )
                # Callback data is not trustworthy, the same reason onboarding's
                # choose_goal_template validates it.
                if template is None or template.kind is not GoalTemplateKind.PRIMARY:
                    raise OnboardingApplicationError("invalid_action")
                goal = await ProfileRepository(session).get_training_goal(
                    user_id=user.id
                )
                if goal is None:
                    raise OnboardingApplicationError("stale_action")
                main_changed = template.id != goal.goal_template_id
                await ProfileRepository(session).upsert_training_goal(
                    user_id=user.id,
                    main_goal=template.display_name,
                    event_date=goal.event_date,
                    target_outcome=template.display_name,
                    secondary_priority=goal.secondary_priority,
                    original_description=template.display_name,
                    goal_template_id=template.id,
                    supporting_goal_template_id=goal.supporting_goal_template_id,
                )
                # Carried into GOAL_SECONDARY's pending so the terminal
                # goal:support: branch below knows to reopen equipment review
                # even if the supporting goal itself doesn't change there.
                state = await settings_repo.save(
                    user_id=user.id,
                    step=ProfileSettingsStep.GOAL_SECONDARY,
                    pending={_MAIN_GOAL_CHANGED_KEY: main_changed},
                )
                return ProfileSettingsResult(
                    step=state.current_step,
                    saved_field="Main goal" if main_changed else None,
                )
            if action.startswith("goal:support:"):
                if step is not ProfileSettingsStep.GOAL_SECONDARY:
                    raise OnboardingApplicationError("stale_action")
                raw = action.removeprefix("goal:support:")
                goal = await ProfileRepository(session).get_training_goal(
                    user_id=user.id
                )
                if goal is None:
                    raise OnboardingApplicationError("stale_action")
                supporting_id: uuid.UUID | None = None
                secondary_priority: str | None = None
                if raw != "none":
                    template = await TrainingCatalogRepository(
                        session
                    ).active_goal_by_code(code=raw)
                    if (
                        template is None
                        or template.kind is not GoalTemplateKind.SUPPORTING
                    ):
                        raise OnboardingApplicationError("invalid_action")
                    supporting_id = template.id
                    secondary_priority = template.display_name
                support_changed = supporting_id != goal.supporting_goal_template_id
                # Carried from goal:template: above: the primary goal may
                # already have changed earlier in this same session, even if
                # the supporting goal picked here is the same as before.
                main_changed = bool(pending.get(_MAIN_GOAL_CHANGED_KEY, False))
                await ProfileRepository(session).upsert_training_goal(
                    user_id=user.id,
                    main_goal=goal.main_goal,
                    event_date=goal.event_date,
                    target_outcome=goal.target_outcome,
                    secondary_priority=secondary_priority,
                    original_description=goal.original_description,
                    goal_template_id=goal.goal_template_id,
                    supporting_goal_template_id=supporting_id,
                )
                if support_changed or main_changed:
                    # Training contexts may have changed (new sport, new
                    # template, or a supporting goal added/removed), so the
                    # equipment & access review needs to reopen, mirroring
                    # the deleted _save_profile_classification's behavior.
                    updated_goal = await ProfileRepository(session).get_training_goal(
                        user_id=user.id
                    )
                    return await self._open_profile_equipment(
                        session,
                        user.id,
                        settings_repo,
                        goal=updated_goal,
                        saved_field=(
                            "Secondary priority" if support_changed else "Main goal"
                        ),
                    )
                state = await settings_repo.save(
                    user_id=user.id,
                    step=ProfileSettingsStep.GOAL_MENU,
                    pending={},
                )
                return ProfileSettingsResult(step=state.current_step, saved_field=None)
            if action == "goal:back":
                if step not in {
                    ProfileSettingsStep.GOAL_MENU,
                    ProfileSettingsStep.GOAL_OUTCOME,
                    ProfileSettingsStep.GOAL_DATE,
                    ProfileSettingsStep.GOAL_MAIN,
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
            ProfileSettingsStep.GOAL_OUTCOME,
            ProfileSettingsStep.GOAL_DATE,
        }:
            goal = await profiles.get_training_goal(user_id=user_id)
            if goal is None:
                return None
            if step is ProfileSettingsStep.GOAL_OUTCOME:
                return goal.target_outcome
            if step is ProfileSettingsStep.GOAL_DATE:
                return (
                    goal.event_date.isoformat() if goal.event_date is not None else None
                )
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
        await profiles.update_training_goal_fields(
            user_id=user_id,
            payload={
                "main_goal": main_goal,
                "target_outcome": outcome,
                "event_date": event_date,
                "secondary_priority": secondary,
            },
        )
        state = await repo.save(
            user_id=user_id, step=ProfileSettingsStep.MENU, pending={}
        )
        return ProfileSettingsResult(
            step=state.current_step,
            saved_field="Goal" if changed else None,
        )

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
        # Every goal is chosen from the catalog menu, so goal_template_id is
        # always set; this is the same defensive check used elsewhere for a
        # missing goal, not a live "classify this goal" path.
        if goal is None or goal.goal_template_id is None:
            raise OnboardingApplicationError("stale_action")
        review = await CapabilityAssessmentService().review(
            catalog=TrainingCatalogRepository(session),
            athlete_capabilities=AthleteCapabilityRepository(session),
            athlete_id=user_id,
            goal_template_id=goal.goal_template_id,
            supporting_goal_template_id=goal.supporting_goal_template_id,
        )
        if review is None:
            state = await repo.save(
                user_id=user_id, step=ProfileSettingsStep.MENU, pending={}
            )
            return ProfileSettingsResult(
                step=state.current_step, saved_field=saved_field or "Equipment & access"
            )
        selected = [str(item.id) for item in review.options if item.selected]
        selection_pending: dict[str, object] = {
            "selected": selected,
        }
        state = await repo.save(
            user_id=user_id,
            step=ProfileSettingsStep.EQUIPMENT,
            pending=selection_pending,
        )
        return ProfileSettingsResult(
            step=state.current_step,
            pending=cast(dict[str, JsonValue], selection_pending),
            saved_field=saved_field,
            capability_review=review,
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
        review = await self._capability_review(session=session, user_id=user_id)
        if review is None:
            raise OnboardingApplicationError("stale_action")
        ids = {str(item.id) for item in review.options}
        raw_selected = pending.get("selected", [])
        if not isinstance(raw_selected, list):
            raise OnboardingApplicationError("stale_action")
        selected = {value for value in raw_selected if isinstance(value, str)}
        selected.intersection_update(ids)
        if choice == "done":
            goal = await ProfileRepository(session).get_training_goal(user_id=user_id)
            if goal is None:
                raise OnboardingApplicationError("stale_action")
            assessment = await CapabilityAssessmentService().save_and_assess(
                catalog=TrainingCatalogRepository(session),
                athlete_capabilities=AthleteCapabilityRepository(session),
                athlete_id=user_id,
                goal_template_id=goal.goal_template_id,
                supporting_goal_template_id=goal.supporting_goal_template_id,
                review=review,
                selected_ids={uuid.UUID(value) for value in selected},
            )
            saved = await repo.save(
                user_id=user_id, step=ProfileSettingsStep.MENU, pending={}
            )
            return ProfileSettingsResult(
                step=saved.current_step,
                saved_field="Equipment & access",
                execution_assessment=assessment,
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
            capability_review=self._with_selection(review, selected),
        )

    async def handle_text(
        self,
        identity: TelegramIdentity,
        text: str,
    ) -> OnboardingServiceResult:
        """Route text to deterministic profile validation.

        The goal step is menu-driven only; free text sent while on it falls
        through to the final `invalid_action` below.
        """

        async with self._session_factory() as session:
            user = await self._require_user(session, identity)
            onboarding = await OnboardingRepository(session).require_for_user(
                user_id=user.id
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
            return await self._resume_capability_review(
                identity=identity,
                user_id=user.id,
            )
        if onboarding.status is OnboardingStatus.COMPLETED:
            # Completed-profile changes must enter the explicit ps:v1 mini-flow;
            # never infer or persist an update from an unprompted message.
            raise OnboardingApplicationError("profile_settings_required")
        raise OnboardingApplicationError("invalid_action")

    async def _resume_capability_review(
        self,
        *,
        identity: TelegramIdentity,
        user_id: uuid.UUID,
    ) -> OnboardingServiceResult:
        """Resume a capability review interrupted before it was shown."""

        async with self._session_factory.begin() as session:
            user, onboarding = await self._locked_state(session, identity)
            self._require_active(onboarding)
            if (
                user.id != user_id
                or onboarding.current_step
                is not OnboardingStep.EQUIPMENT_RECOMMENDATION
            ):
                raise OnboardingApplicationError("stale_action")
            goal = await ProfileRepository(session).get_training_goal(user_id=user.id)
            # Every goal is chosen from the catalog menu, so goal_template_id is
            # always set by the time this step is reached.
            if goal is None or goal.goal_template_id is None:
                raise OnboardingApplicationError("stale_action")
        return await self._prepare_capability_review(user_id=user_id)

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
        refresh_capability_review = False
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
                refresh_capability_review = self._training_goal_changed(
                    goal=existing_goal,
                    payload=repository_goal_payload,
                )
                await profiles.update_training_goal_fields(
                    user_id=user_id,
                    payload=repository_goal_payload,
                )
            if refresh_capability_review:
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
        if refresh_capability_review:
            # Commit the goal change before rebuilding the durable review.
            await self._prepare_capability_review(user_id=user_id)
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
            if key == "target_outcome":
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

        template_fields = {
            "goal_template_id",
            "supporting_goal_template_id",
        }
        return any(
            field in template_fields and getattr(goal, field) != value
            for field, value in payload.items()
        )

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
        """Check the text's length while retaining the athlete's literal input."""

        async with self._session_factory.begin() as session:
            user, onboarding = await self._locked_state(session, identity)
            self._require_active(onboarding)
            if user.id != user_id or onboarding.current_step is not step:
                raise OnboardingApplicationError("stale_action")

        cleaned = text.strip()
        if not (_CONTEXT_TEXT_MIN_LENGTH <= len(cleaned) <= _CONTEXT_TEXT_MAX_LENGTH):
            raise OnboardingApplicationError("invalid_action")

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
                return await self._advance_to_training_history(
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
            return await self._resume_capability_review(
                identity=identity,
                user_id=user_id,
            )
        return saved_result

    async def _prepare_capability_review(
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
            review = await CapabilityAssessmentService().review(
                catalog=TrainingCatalogRepository(session),
                athlete_capabilities=AthleteCapabilityRepository(session),
                athlete_id=user.id,
                goal_template_id=goal.goal_template_id,
                supporting_goal_template_id=goal.supporting_goal_template_id,
            )
            if review is None:
                raise OnboardingApplicationError("goal_classification_required")
            answers[_CAPABILITY_SELECTION_KEY] = [
                str(item.id) for item in review.options if item.selected
            ]
            answers.pop(_CONTEXT_RETRY_ERROR_KEY, None)
            onboarding = await OnboardingRepository(session).save_progress(
                user_id=user.id,
                current_step=OnboardingStep.EQUIPMENT_INTAKE,
                answers=cast(dict[str, object], answers),
            )
            return self._result(user, onboarding, capability_review=review)

    async def _capability_review(
        self, *, session: AsyncSession, user_id: uuid.UUID
    ) -> CapabilityReview | None:
        goal = await ProfileRepository(session).get_training_goal(user_id=user_id)
        if goal is None:
            return None
        return await CapabilityAssessmentService().review(
            catalog=TrainingCatalogRepository(session),
            athlete_capabilities=AthleteCapabilityRepository(session),
            athlete_id=user_id,
            goal_template_id=goal.goal_template_id,
            supporting_goal_template_id=goal.supporting_goal_template_id,
        )

    @staticmethod
    def _with_selection(
        review: CapabilityReview, selected: set[str]
    ) -> CapabilityReview:
        return review.model_copy(
            update={
                "options": tuple(
                    item.model_copy(update={"selected": str(item.id) in selected})
                    for item in review.options
                )
            }
        )

    async def _advance_to_training_history(
        self,
        *,
        session: AsyncSession,
        user: User,
        onboarding: OnboardingSession,
        answers: dict[str, JsonValue],
    ) -> OnboardingServiceResult:
        """Advance to the optional history decision after required context."""

        onboarding = await OnboardingRepository(session).save_progress(
            user_id=user.id,
            current_step=OnboardingStep.TRAINING_HISTORY_IMPORT,
            answers=cast(dict[str, object], answers),
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
                current = await self._capability_review(
                    session=session,
                    user_id=user.id,
                )
                raw_selected = dict(onboarding.answers).get(
                    _CAPABILITY_SELECTION_KEY,
                    [],
                )
                selected = (
                    {value for value in raw_selected if isinstance(value, str)}
                    if isinstance(raw_selected, list)
                    else set()
                )
                if current is not None:
                    review = self._with_selection(current, selected)
            return self._result(user, onboarding, capability_review=review)

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
    def _answers(onboarding: OnboardingSession) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], dict(onboarding.answers))

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
        training_history_skipped: bool = False,
        capability_review: CapabilityReview | None = None,
        execution_assessment: GoalExecutionAssessment | None = None,
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
            elif onboarding.current_step is OnboardingStep.TRAINING_HISTORY_IMPORT:
                kind = "training_history_import"
            else:
                # The only remaining step is GOAL_INTAKE, which has two
                # menu screens (sport, then template) distinguished by
                # whether a sport has been chosen yet; see _GOAL_SPORT_KEY.
                kind = "goal_intake"
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
            training_history_skipped=training_history_skipped,
            capability_review=capability_review,
            execution_assessment=execution_assessment,
        )
