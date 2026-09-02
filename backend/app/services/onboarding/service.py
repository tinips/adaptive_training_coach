"""Focused, durable onboarding through deterministic, menu-driven steps."""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import JsonValue
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.base import utc_now
from app.db.models import (
    GoalTemplate,
    OnboardingSession,
    ProfileSettingsSession,
    TrainingGoal,
    User,
)
from app.domain.enums import (
    AthleteGender,
    Discipline,
    GoalContextRole,
    GoalTemplateKind,
    OnboardingStatus,
    OnboardingStep,
    ProfileSettingsStep,
    UserStatus,
)
from app.integrations.llm.factory import create_goal_extraction_model
from app.integrations.llm.models import StructuredOnboardingModel
from app.repositories.apple_health import AppleHealthRepository
from app.repositories.athlete_baselines import AthleteBaselineRepository
from app.repositories.athlete_capabilities import AthleteCapabilityRepository
from app.repositories.onboarding import OnboardingRepository
from app.repositories.profile_settings import ProfileSettingsRepository
from app.repositories.profiles import ProfileRepository
from app.repositories.training_catalog import TrainingCatalogRepository
from app.repositories.users import UserRepository
from app.schemas.availability import ConfirmedWeeklyAvailability
from app.schemas.capabilities import CapabilityReview, GoalExecutionAssessment
from app.schemas.common import TelegramIdentity
from app.schemas.onboarding_service import (
    OnboardingResultKind,
    OnboardingServiceResult,
    UpdatedOnboardingData,
)
from app.schemas.profile_settings import ProfileSettingsResult
from app.services.capabilities import CapabilityAssessmentService
from app.services.onboarding.availability import (
    AvailabilityExtractionError,
    AvailabilityExtractionService,
)
from app.services.onboarding.baseline_form import (
    build_baseline,
    fields_for_disciplines,
    is_optional_field,
    parse_answer,
)
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
_BASELINE_FIELDS_KEY = "baseline_fields"
_BASELINE_INDEX_KEY = "baseline_index"
_BASELINE_VALUES_KEY = "baseline_values"
_BASELINE_GOAL_SIGNATURE_KEY = "baseline_goal_signature"
_AVAILABILITY_DRAFT_KEY = "availability_draft"
_AVAILABILITY_SOURCE_KEY = "availability_source_text"
_GOAL_METRIC_FIELDS_KEY = "goal_metric_fields"
_GOAL_METRIC_INDEX_KEY = "goal_metric_index"
_GOAL_METRIC_VALUES_KEY = "goal_metric_values"
_FIXED_RUNNING_DISTANCES = {
    "RUNNING_5K": 5.0,
    "RUNNING_10K": 10.0,
    "HALF_MARATHON": 21.1,
    "MARATHON": 42.2,
}
_TRIATHLON_CODES = frozenset(
    {
        "TRIATHLON_SPRINT",
        "TRIATHLON_OLYMPIC",
        "TRIATHLON_HALF_DISTANCE",
        "TRIATHLON_FULL_DISTANCE",
    }
)
_CONTEXT_RETRY_ERROR_KEY = "_context_retry_error"
_INTEGER_PATTERN = re.compile(r"[0-9]+")
_WEIGHT_PATTERN = re.compile(r"[0-9]+(?:\.[0-9]+)?")
_ATHLETE_PROFILE_UPDATE_FIELDS = frozenset(
    {"birth_year", "gender", "weight_kg", "height_cm"}
)
_TRAINING_GOAL_UPDATE_FIELDS = frozenset({"event_date"})
_ATHLETE_PROFILE_CONTEXT_UPDATE_FIELDS = frozenset(
    {"health_limitations_text"}
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
# Availability is parsed into a confirmed structured schedule. Health text remains
# literal context, so both inputs use the same bounded text intake guard.
_CONTEXT_TEXT_MIN_LENGTH = 3
_CONTEXT_TEXT_MAX_LENGTH = 2000
_EVENT_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y")


def _parse_event_date(text: str) -> date | None:
    """Parse a race date deterministically. No model, no fuzzy matching."""

    cleaned = text.strip()
    for pattern in _EVENT_DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, pattern).date()
        except ValueError:
            continue
    return None


def _parse_goal_metric(field: str, text: str) -> JsonValue:
    """Validate the one structured metric currently requested."""

    cleaned = text.strip()
    if field in {
        "running_distance",
        "cycling_distance",
        "cycling_average_speed",
        "elevation",
    }:
        try:
            decimal_value = Decimal(cleaned)
        except InvalidOperation as exc:
            raise ValueError from exc
        if decimal_value <= 0:
            raise ValueError
        return float(decimal_value)
    if field == "swimming_distance":
        try:
            distance_m = int(cleaned)
        except ValueError as exc:
            raise ValueError from exc
        if distance_m <= 0:
            raise ValueError
        return distance_m
    if field == "triathlon_finish_time":
        parts = cleaned.split(":")
        if len(parts) != 2 or not all(part.isdigit() for part in parts):
            raise ValueError
        hours, minutes = (int(part) for part in parts)
        total = hours * 3600 + minutes * 60
        if minutes >= 60 or total <= 0:
            raise ValueError
        return total
    if field in {"running_pace", "swimming_pace"}:
        parts = cleaned.split(":")
        if len(parts) != 2 or not all(part.isdigit() for part in parts):
            raise ValueError
        minutes, seconds = (int(part) for part in parts)
        total = minutes * 60 + seconds
        if seconds >= 60 or total <= 0 or total >= 3600:
            raise ValueError
        return total
    raise ValueError


def _goal_metric_fields(*, sport: str, code: str) -> tuple[str, ...]:
    """Single metric contract shared by onboarding and profile settings."""

    if sport == GoalSport.CYCLING.value:
        return ("cycling_distance", "elevation", "cycling_average_speed")
    if sport == GoalSport.SWIMMING.value:
        return ("swimming_distance", "swimming_pace")
    if sport == GoalSport.TRIATHLON.value:
        return ("triathlon_finish_time",)
    if code in _FIXED_RUNNING_DISTANCES:
        return ("running_pace",)
    return ("running_distance", "elevation", "running_pace")


def _goal_sport_from_template_code(code: str) -> str | None:
    """Infer a goal sport for legacy rows that predate goal metadata."""

    if code in _TRIATHLON_CODES:
        return GoalSport.TRIATHLON.value
    if code == "ROAD_CYCLING_EVENT":
        return GoalSport.CYCLING.value
    if code in {"POOL_SWIMMING_EVENT", "OPEN_WATER_SWIM"}:
        return GoalSport.SWIMMING.value
    if code in set(_FIXED_RUNNING_DISTANCES) | {
        "GENERAL_RUNNING",
        "TRAIL_RACE",
        "ULTRA_MARATHON",
    }:
        return GoalSport.RUNNING.value
    return None


def _development_weekly_availability() -> dict[str, object]:
    days: dict[str, object] = {
        day: {"available": False, "disciplines": [], "time_windows": []}
        for day in (
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
            "friday",
            "saturday",
            "sunday",
        )
    }
    days["tuesday"] = {
        "available": True,
        "disciplines": ["cycling", "running", "swimming"],
        "time_windows": [{"time_of_day": None, "duration_minutes": 60}],
    }
    return ConfirmedWeeklyAvailability(days=days).model_dump(mode="json")


def _target_payload_from_metric_values(
    *, sport: str, code: str, values: Mapping[str, JsonValue]
) -> dict[str, object]:
    distance_km = values.get("running_distance", values.get("cycling_distance"))
    distance_meters = values.get("swimming_distance")
    elevation = values.get("elevation")
    running_pace = values.get("running_pace")
    swimming_pace = values.get("swimming_pace")
    cycling_speed = values.get("cycling_average_speed")
    finish_time = values.get("triathlon_finish_time")
    return {
        "target_distance_km": (
            float(distance_km) if isinstance(distance_km, (int, float)) else None
        ),
        "target_elevation_m": (
            float(elevation) if isinstance(elevation, (int, float)) else None
        ),
        "target_pace_seconds_per_km": (
            float(running_pace) if isinstance(running_pace, (int, float)) else None
        ),
        "target_swim_pace_seconds_per_100m": (
            float(swimming_pace) if isinstance(swimming_pace, (int, float)) else None
        ),
        "target_average_speed_kph": (
            float(cycling_speed) if isinstance(cycling_speed, (int, float)) else None
        ),
        "target_finish_time_seconds": (
            int(finish_time) if isinstance(finish_time, int) else None
        ),
        "goal_metadata_jsonb": {
            "primary_goal": {
                "discipline": sport,
                "goal_type": code,
                "target_distance": (
                    {"value": distance_meters, "unit": "m", "source": "user"}
                    if distance_meters is not None
                    else (
                        {
                            "value": distance_km,
                            "unit": "km",
                            "source": "predefined"
                            if code in _FIXED_RUNNING_DISTANCES
                            else "user",
                        }
                        if distance_km is not None
                        else None
                    )
                ),
                "target_elevation_m": elevation,
                "target_pace": running_pace,
                "target_average_speed_kph": cycling_speed,
                "target_swim_pace_seconds_per_100m": swimming_pace,
                "target_finish_time_seconds": finish_time,
            }
        },
    }


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
        model: StructuredOnboardingModel | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._availability = AvailabilityExtractionService(
            model or create_goal_extraction_model(settings)
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
            if onboarding.current_step is OnboardingStep.BASELINE_INTAKE:
                onboarding = await self._upgrade_legacy_baseline_fields(
                    session=session,
                    onboarding=onboarding,
                )
            return self._result(
                user,
                onboarding,
                created=created or onboarding_created,
                capability_review=capability_review,
            )

    async def _upgrade_legacy_baseline_fields(
        self,
        *,
        session: AsyncSession,
        onboarding: OnboardingSession,
    ) -> OnboardingSession:
        """Replace obsolete stored baseline fields without resetting onboarding."""

        answers = self._answers(onboarding)
        existing = answers.get(_BASELINE_FIELDS_KEY)
        if not isinstance(existing, list) or not all(
            isinstance(field, str) for field in existing
        ):
            return onboarding
        existing_fields = tuple(field for field in existing if isinstance(field, str))
        disciplines_in_order = (
            Discipline.RUNNING,
            Discipline.CYCLING,
            Discipline.SWIMMING,
        )

        available = {
            field
            for discipline in disciplines_in_order
            for field in fields_for_disciplines((discipline,))
        }
        if set(existing_fields).issubset(available):
            return onboarding

        disciplines = tuple(
            discipline
            for discipline in disciplines_in_order
            if any(
                field.startswith(f"{discipline.value}.") for field in existing_fields
            )
        )
        fields = fields_for_disciplines(disciplines)
        if not fields:
            return onboarding

        answers[_BASELINE_FIELDS_KEY] = list(fields)
        answers[_BASELINE_INDEX_KEY] = 0
        answers[_BASELINE_VALUES_KEY] = {}
        answers.pop("baseline_form_errors", None)
        answers.pop("baseline_form_values", None)
        return await OnboardingRepository(session).save_progress(
            user_id=onboarding.user_id,
            current_step=OnboardingStep.BASELINE_INTAKE,
            answers=cast(dict[str, object], answers),
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
                secondary_priority=None,
                goal_template_id=development_template.id,
            )
            context: dict[str, object] = {
                "weekly_availability_jsonb": None,
                "health_limitations_text": None,
            }
            if step_name in {"equipment", "limitations", "history", "completed"}:
                context["weekly_availability_jsonb"] = (
                    _development_weekly_availability()
                )
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
        """Choose a goal sport, skipping a redundant one-item goal menu."""

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
            if sport is GoalSport.CYCLING:
                template = await TrainingCatalogRepository(session).active_goal_by_code(
                    code="ROAD_CYCLING_EVENT", kind=GoalTemplateKind.PRIMARY
                )
                if template is None:
                    raise OnboardingApplicationError("goal_template_not_found")
                return await self._start_goal_metrics(
                    session=session,
                    user=user,
                    onboarding=onboarding,
                    answers=answers,
                    template=template,
                    fields=_goal_metric_fields(sport=sport.value, code=template.code),
                )
            if sport is GoalSport.SWIMMING:
                onboarding = await OnboardingRepository(session).save_progress(
                    user_id=user.id,
                    current_step=OnboardingStep.GOAL_SWIMMING_TYPE,
                    answers=cast(dict[str, object], answers),
                )
                return self._result(user, onboarding)
            onboarding = await OnboardingRepository(session).save_progress(
                user_id=user.id,
                current_step=OnboardingStep.GOAL_INTAKE,
                answers=cast(dict[str, object], answers),
            )
            return self._result(user, onboarding)

    async def choose_swimming_type(
        self, identity: TelegramIdentity, choice: str
    ) -> OnboardingServiceResult:
        """Choose the catalog-backed pool or open-water goal before metrics."""

        code_by_choice = {
            "pool": "POOL_SWIMMING_EVENT",
            "open_water": "OPEN_WATER_SWIM",
        }
        code = code_by_choice.get(choice)
        if code is None:
            raise OnboardingApplicationError("invalid_action")
        async with self._session_factory.begin() as session:
            user, onboarding = await self._locked_state(session, identity)
            self._require_active(onboarding)
            if onboarding.current_step is not OnboardingStep.GOAL_SWIMMING_TYPE:
                raise OnboardingApplicationError("stale_action")
            template = await TrainingCatalogRepository(session).active_goal_by_code(
                code=code, kind=GoalTemplateKind.PRIMARY
            )
            if template is None:
                raise OnboardingApplicationError("goal_template_not_found")
            answers = self._answers(onboarding)
            answers["swimming_type"] = choice.upper()
            return await self._start_goal_metrics(
                session=session,
                user=user,
                onboarding=onboarding,
                answers=answers,
                template=template,
                fields=_goal_metric_fields(sport=GoalSport.SWIMMING.value, code=code),
            )

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
            sport = self._answers(onboarding).get(_GOAL_SPORT_KEY)
            if not isinstance(sport, str):
                raise OnboardingApplicationError("stale_action")
            allowed_codes = {
                option_code
                for option_code, _ in await self.goal_template_options(sport)
            }
            if code not in allowed_codes:
                raise OnboardingApplicationError("invalid_action")
            # Callback data is not trustworthy. An unvalidated code would write
            # a dangling foreign key into training_goals.
            if template is None or template.kind is not GoalTemplateKind.PRIMARY:
                raise OnboardingApplicationError("invalid_action")
            return await self._start_goal_metrics(
                session=session,
                user=user,
                onboarding=onboarding,
                answers=self._answers(onboarding),
                template=template,
                fields=_goal_metric_fields(sport=sport, code=code),
            )

    async def _start_goal_metrics(
        self,
        *,
        session: AsyncSession,
        user: User,
        onboarding: OnboardingSession,
        answers: dict[str, JsonValue],
        template: GoalTemplate,
        fields: tuple[str, ...],
    ) -> OnboardingServiceResult:
        """Stage the known structured fields for one catalog-backed goal."""

        code = template.code
        template_id = template.id
        display_name = template.display_name
        answers["goal_template_code"] = code
        answers[_GOAL_METRIC_FIELDS_KEY] = list(fields)
        answers[_GOAL_METRIC_INDEX_KEY] = 0
        values: dict[str, JsonValue] = {}
        fixed_distance = _FIXED_RUNNING_DISTANCES.get(code)
        if fixed_distance is not None:
            values["running_distance"] = fixed_distance
        answers[_GOAL_METRIC_VALUES_KEY] = values
        await ProfileRepository(session).upsert_training_goal(
            user_id=user.id,
            main_goal=display_name,
            event_date=None,
            secondary_priority=None,
            goal_template_id=template_id,
            supporting_goal_template_id=None,
            target_distance_km=fixed_distance,
        )
        onboarding = await OnboardingRepository(session).save_progress(
            user_id=user.id,
            current_step=OnboardingStep.GOAL_METRIC_INTAKE,
            answers=cast(dict[str, object], answers),
        )
        return self._result(user, onboarding)

    async def skip_goal_metric(
        self, identity: TelegramIdentity
    ) -> OnboardingServiceResult:
        return await self._save_goal_metric(identity, value=None)

    async def submit_goal_metric(
        self, identity: TelegramIdentity, text: str
    ) -> OnboardingServiceResult:
        async with self._session_factory() as session:
            user, onboarding = await self._locked_state(session, identity)
            if onboarding.current_step is not OnboardingStep.GOAL_METRIC_INTAKE:
                raise OnboardingApplicationError("stale_action")
            field = self._current_goal_metric_field(onboarding)
        try:
            value = _parse_goal_metric(field, text)
        except ValueError:
            async with self._session_factory.begin() as session:
                user, onboarding = await self._locked_state(session, identity)
                return self._result(
                    user,
                    onboarding,
                    kind="profile_validation_error",
                    error_code=f"invalid_{field}",
                )
        return await self._save_goal_metric(identity, value=value)

    async def _save_goal_metric(
        self, identity: TelegramIdentity, *, value: JsonValue | None
    ) -> OnboardingServiceResult:
        async with self._session_factory.begin() as session:
            user, onboarding = await self._locked_state(session, identity)
            self._require_active(onboarding)
            if onboarding.current_step is not OnboardingStep.GOAL_METRIC_INTAKE:
                raise OnboardingApplicationError("stale_action")
            field = self._current_goal_metric_field(onboarding)
            answers = self._answers(onboarding)
            values = dict(cast(dict[str, JsonValue], answers[_GOAL_METRIC_VALUES_KEY]))
            values[field] = value
            fields = cast(list[str], answers[_GOAL_METRIC_FIELDS_KEY])
            index = cast(int, answers[_GOAL_METRIC_INDEX_KEY]) + 1
            if index < len(fields):
                answers[_GOAL_METRIC_VALUES_KEY] = values
                answers[_GOAL_METRIC_INDEX_KEY] = index
                onboarding = await OnboardingRepository(session).save_progress(
                    user_id=user.id,
                    current_step=OnboardingStep.GOAL_METRIC_INTAKE,
                    answers=cast(dict[str, object], answers),
                )
                return self._result(user, onboarding)
            await self._persist_structured_goal(
                session=session, user=user, answers=answers, values=values
            )
            onboarding = await OnboardingRepository(session).save_progress(
                user_id=user.id,
                current_step=OnboardingStep.GOAL_EVENT_DATE,
                answers=cast(dict[str, object], answers),
            )
            return self._result(user, onboarding)

    @staticmethod
    def _current_goal_metric_field(onboarding: OnboardingSession) -> str:
        answers = onboarding.answers
        fields = answers.get(_GOAL_METRIC_FIELDS_KEY)
        index = answers.get(_GOAL_METRIC_INDEX_KEY)
        if (
            not isinstance(fields, list)
            or not all(isinstance(item, str) for item in fields)
            or not isinstance(index, int)
            or isinstance(index, bool)
            or not 0 <= index < len(fields)
        ):
            raise OnboardingApplicationError("stale_action")
        return cast(str, fields[index])

    async def _persist_structured_goal(
        self,
        *,
        session: AsyncSession,
        user: User,
        answers: dict[str, JsonValue],
        values: dict[str, JsonValue],
    ) -> None:
        code = answers.get("goal_template_code")
        sport = answers.get(_GOAL_SPORT_KEY)
        if not isinstance(code, str) or not isinstance(sport, str):
            raise OnboardingApplicationError("stale_action")
        template = await TrainingCatalogRepository(session).active_goal_by_code(
            code=code, kind=GoalTemplateKind.PRIMARY
        )
        if template is None:
            raise OnboardingApplicationError("stale_action")
        target_payload = _target_payload_from_metric_values(
            sport=sport, code=code, values=values
        )
        metadata = cast(dict[str, object], target_payload["goal_metadata_jsonb"])
        metadata["primary_goal"] = {
            **cast(dict[str, object], metadata["primary_goal"]),
            "swimming_type": answers.get("swimming_type"),
        }
        await ProfileRepository(session).upsert_training_goal(
                user_id=user.id,
                main_goal=template.display_name,
                event_date=None,
                secondary_priority=None,
            goal_template_id=template.id,
            supporting_goal_template_id=None,
            target_distance_km=cast(float | None, target_payload["target_distance_km"]),
            target_elevation_m=cast(float | None, target_payload["target_elevation_m"]),
            target_pace_seconds_per_km=cast(
                float | None, target_payload["target_pace_seconds_per_km"]
            ),
            target_swim_pace_seconds_per_100m=cast(
                float | None,
                target_payload["target_swim_pace_seconds_per_100m"],
            ),
            target_average_speed_kph=cast(
                float | None, target_payload["target_average_speed_kph"]
            ),
            target_finish_time_seconds=cast(
                int | None, target_payload["target_finish_time_seconds"]
            ),
            goal_metadata_jsonb=cast(
                dict[str, object] | None, target_payload["goal_metadata_jsonb"]
            ),
        )

    async def submit_event_date(
        self,
        identity: TelegramIdentity,
        text: str,
    ) -> OnboardingServiceResult:
        """Store a typed race date, or reject it without advancing.

        A race date that has already happened is rejected the same way as
        text that cannot be parsed at all: neither is a usable goal date.
        Both cases return a validation-error result rather than raising,
        matching every other text-intake step (birth year, weight, height):
        `CoachBotApplicationService.handle_text` has no exception handler
        for `OnboardingApplicationError`, so raising here would propagate
        as an unhandled error instead of re-prompting the athlete.
        """

        parsed = _parse_event_date(text)
        if parsed is not None and parsed < utc_now().date():
            parsed = None
        if parsed is None:
            async with self._session_factory.begin() as session:
                user, onboarding = await self._locked_state(session, identity)
                self._require_active(onboarding)
                if onboarding.current_step is not OnboardingStep.GOAL_EVENT_DATE:
                    raise OnboardingApplicationError("stale_action")
                return self._result(
                    user,
                    onboarding,
                    kind="profile_validation_error",
                    error_code="invalid_event_date",
                )
        return await self._store_event_date(identity, parsed)

    async def skip_event_date(
        self, identity: TelegramIdentity
    ) -> OnboardingServiceResult:
        """An athlete with no event still gets a plan, in the GENERAL phase."""

        return await self._store_event_date(identity, None)

    async def _store_event_date(
        self,
        identity: TelegramIdentity,
        event_date: date | None,
    ) -> OnboardingServiceResult:
        async with self._session_factory.begin() as session:
            user, onboarding = await self._locked_state(session, identity)
            self._require_active(onboarding)
            if onboarding.current_step is not OnboardingStep.GOAL_EVENT_DATE:
                raise OnboardingApplicationError("stale_action")
            profiles = ProfileRepository(session)
            goal = await profiles.get_training_goal(user_id=user.id)
            if goal is None:
                raise OnboardingApplicationError("stale_action")
            await profiles.upsert_training_goal(
                user_id=user.id,
                main_goal=goal.main_goal,
                event_date=event_date,
                secondary_priority=goal.secondary_priority,
                goal_template_id=goal.goal_template_id,
                supporting_goal_template_id=goal.supporting_goal_template_id,
                target_distance_km=goal.target_distance_km,
                target_elevation_m=goal.target_elevation_m,
                target_pace_seconds_per_km=goal.target_pace_seconds_per_km,
                target_swim_pace_seconds_per_100m=goal.target_swim_pace_seconds_per_100m,
                target_average_speed_kph=goal.target_average_speed_kph,
                target_finish_time_seconds=goal.target_finish_time_seconds,
                goal_metadata_jsonb=goal.goal_metadata_jsonb,
            )
            onboarding = await OnboardingRepository(session).save_progress(
                user_id=user.id,
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
                secondary_priority=secondary_priority,
                goal_template_id=goal.goal_template_id,
                supporting_goal_template_id=supporting_id,
                target_distance_km=goal.target_distance_km,
                target_elevation_m=goal.target_elevation_m,
                target_pace_seconds_per_km=goal.target_pace_seconds_per_km,
                target_swim_pace_seconds_per_100m=goal.target_swim_pace_seconds_per_100m,
                target_average_speed_kph=goal.target_average_speed_kph,
                target_finish_time_seconds=goal.target_finish_time_seconds,
                goal_metadata_jsonb=goal.goal_metadata_jsonb,
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
        options = grouped.get(goal_sport, ())
        return tuple((option.code, option.display_name) for option in options)

    async def supporting_goal_options(
        self, identity: TelegramIdentity | None = None
    ) -> tuple[tuple[str, str], ...]:
        """Return supporting goals except the athlete's current main goal."""

        async with self._session_factory() as session:
            goal = None
            if identity is not None:
                profiles = ProfileRepository(session)
                goal = await profiles.get_training_goal(
                    user_id=(await self._require_user(session, identity)).id
                )
            templates = await TrainingCatalogRepository(session).active_goal_templates()
        main_goal = goal.main_goal.casefold().strip() if goal is not None else None
        excluded_codes: set[str] = set()
        if goal is not None and goal.goal_template_id is not None:
            primary = next(
                (item for item in templates if item.id == goal.goal_template_id), None
            )
            primary_code = primary.code if primary is not None else ""
            if primary_code in _TRIATHLON_CODES:
                excluded_codes = {
                    "IMPROVE_RUNNING",
                    "IMPROVE_CYCLING",
                    "IMPROVE_SWIMMING",
                }
            elif primary_code in set(_FIXED_RUNNING_DISTANCES) | {"GENERAL_RUNNING"}:
                excluded_codes = {"IMPROVE_RUNNING"}
            elif primary_code == "ROAD_CYCLING_EVENT":
                excluded_codes = {"IMPROVE_CYCLING"}
            elif primary_code in {"POOL_SWIMMING_EVENT", "OPEN_WATER_SWIM"}:
                excluded_codes = {"IMPROVE_SWIMMING"}
        return tuple(
            (item.code, item.display_name)
            for item in templates
            if item.kind is GoalTemplateKind.SUPPORTING
            and item.code not in excluded_codes
            and item.id != (goal.goal_template_id if goal is not None else None)
            and item.display_name.casefold().strip() != main_goal
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
            del answers
            user_id = user.id
        return await self._start_baseline(identity=identity, user_id=user_id)

    async def _start_baseline(
        self,
        *,
        identity: TelegramIdentity,
        user_id: uuid.UUID,
    ) -> OnboardingServiceResult:
        """Create the goal-adaptive baseline checkpoint after health intake."""

        async with self._session_factory.begin() as session:
            user, onboarding = await self._locked_state(session, identity)
            self._require_active(onboarding)
            if (
                user.id != user_id
                or onboarding.current_step
                is not OnboardingStep.HEALTH_LIMITATIONS_INTAKE
            ):
                raise OnboardingApplicationError("stale_action")
            goal = await ProfileRepository(session).get_training_goal(user_id=user.id)
            if goal is None or goal.goal_template_id is None:
                raise OnboardingApplicationError("stale_action")
            expected_roles = {goal.goal_template_id: GoalContextRole.TARGET}
            if goal.supporting_goal_template_id is not None:
                expected_roles[goal.supporting_goal_template_id] = (
                    GoalContextRole.SUPPORTING
                )
            catalog = TrainingCatalogRepository(session)
            primary_goal = await catalog.active_goal_by_id(
                goal_template_id=goal.goal_template_id
            )
            if primary_goal is None:
                raise OnboardingApplicationError("stale_action")
            rows = await catalog.contexts_for_goals(
                goal_template_ids=expected_roles.keys()
            )
            disciplines = tuple(
                sorted(
                    {
                        context.discipline
                        for relation, context in rows
                        if expected_roles.get(relation.goal_template_id)
                        is relation.role
                        and context.discipline
                        in {Discipline.RUNNING, Discipline.CYCLING, Discipline.SWIMMING}
                    },
                    key=lambda item: item.value,
                )
            )
            fields = fields_for_disciplines(
                disciplines,
                include_triathlon=primary_goal.code in _TRIATHLON_CODES,
            )
            if not fields:
                raise OnboardingApplicationError("baseline_not_supported")
            answers = self._answers(onboarding)
            answers[_BASELINE_FIELDS_KEY] = list(fields)
            answers[_BASELINE_INDEX_KEY] = 0
            answers[_BASELINE_VALUES_KEY] = {}
            answers[_BASELINE_GOAL_SIGNATURE_KEY] = "|".join(
                str(item)
                for item in (goal.goal_template_id, goal.supporting_goal_template_id)
                if item is not None
            )
            onboarding = await OnboardingRepository(session).save_progress(
                user_id=user.id,
                current_step=OnboardingStep.BASELINE_INTAKE,
                answers=cast(dict[str, object], answers),
            )
            return self._result(user, onboarding)

    async def submit_baseline_form(
        self, identity: TelegramIdentity, values: Mapping[str, object]
    ) -> OnboardingServiceResult:
        """Validate the complete Web App payload and save it atomically."""

        async with self._session_factory.begin() as session:
            user, onboarding = await self._locked_state(session, identity)
            self._require_active(onboarding)
            if onboarding.current_step is not OnboardingStep.BASELINE_INTAKE:
                raise OnboardingApplicationError("stale_action")
            answers = self._answers(onboarding)
            fields = answers.get(_BASELINE_FIELDS_KEY)
            signature = answers.get(_BASELINE_GOAL_SIGNATURE_KEY)
            if not isinstance(fields, list) or not isinstance(signature, str):
                raise OnboardingApplicationError("stale_action")
            parsed: dict[str, object] = {}
            invalid: list[str] = []
            for field in fields:
                if not isinstance(field, str):
                    raise OnboardingApplicationError("stale_action")
                raw = values.get(field)
                text = raw if isinstance(raw, str) else ""
                if not text and is_optional_field(field):
                    text = "skip"
                try:
                    parsed[field] = parse_answer(key=field, text=text)
                except ValueError:
                    invalid.append(field)
            if invalid:
                answers["baseline_form_errors"] = cast(JsonValue, invalid)
                answers["baseline_form_values"] = {
                    key: value
                    for key, value in values.items()
                    if isinstance(value, str)
                }
                return self._result(
                    user,
                    onboarding,
                    kind="baseline_validation_error",
                    error_code=invalid[0],
                )
            await AthleteBaselineRepository(session).upsert(
                athlete_id=user.id,
                goal_signature=signature,
                baseline=build_baseline(parsed),
            )
            onboarding.status = OnboardingStatus.COMPLETED
            user = await UserRepository(session).update_status(
                user_id=user.id, status=UserStatus.ONBOARDING_COMPLETED
            )
            await session.flush()
            return self._result(user, onboarding)

    async def skip_training_history(
        self,
        identity: TelegramIdentity,
    ) -> OnboardingServiceResult:
        """Complete onboarding after an explicit, deterministic history skip."""

        return await self._complete_training_history(
            identity, training_history_skipped=True
        )

    async def complete_training_history(
        self,
        identity: TelegramIdentity,
    ) -> OnboardingServiceResult:
        """Complete onboarding after choosing phone sync for workout history."""

        return await self._complete_training_history(
            identity, training_history_skipped=False
        )

    async def _complete_training_history(
        self,
        identity: TelegramIdentity,
        *,
        training_history_skipped: bool,
    ) -> OnboardingServiceResult:
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
            return self._result(
                user,
                onboarding,
                training_history_skipped=training_history_skipped,
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
            user = await self._profile_settings_user(session, identity)
            state = await ProfileSettingsRepository(session).save(
                user_id=user.id, step=ProfileSettingsStep.MENU, pending={}
            )
            return ProfileSettingsResult(step=state.current_step)

    async def choose_profile_settings(
        self, identity: TelegramIdentity, action: str
    ) -> ProfileSettingsResult:
        """Apply a stable ps:v1 callback. It never calls a model."""

        async with self._session_factory.begin() as session:
            user = await self._profile_settings_user(session, identity)
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
                "personal:timezone": ProfileSettingsStep.PERSONAL_TIMEZONE,
            }.get(action)
            if action in {"done", "back"} and (
                step is ProfileSettingsStep.AVAILABILITY_REVIEW
                and "availability_draft" in pending
            ):
                return ProfileSettingsResult(
                    step=step,
                    pending=cast(dict[str, JsonValue], pending),
                    confirm_discard=True,
                )
            if action == "discard":
                state = await settings_repo.save(
                    user_id=user.id, step=ProfileSettingsStep.MENU, pending={}
                )
                return ProfileSettingsResult(
                    step=state.current_step, saved_field="__closed__"
                )
            if action == "keep_editing":
                return ProfileSettingsResult(
                    step=step,
                    pending=cast(dict[str, JsonValue], pending),
                    current_value=await self._profile_setting_current_value(
                        session=session, user_id=user.id, step=step
                    ),
                )
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
                if target is ProfileSettingsStep.GOAL_MENU:
                    goal = await ProfileRepository(session).get_training_goal(
                        user_id=user.id
                    )
                    if goal is None:
                        raise OnboardingApplicationError("stale_action")
                    pending = self._profile_goal_pending(goal)
                elif target is ProfileSettingsStep.PERSONAL_MENU:
                    pending = await self._profile_personal_pending(session, user)
                elif target is ProfileSettingsStep.AVAILABILITY:
                    profile = await ProfileRepository(session).get_athlete_profile(
                        user_id=user.id
                    )
                    confirmed = self._confirmed_profile_availability(profile)
                    pending = (
                        {"current_availability": confirmed.model_dump(mode="json")}
                        if confirmed is not None
                        else {}
                    )
                state = await settings_repo.save(
                    user_id=user.id,
                    step=target,
                    pending=pending,
                )
                return ProfileSettingsResult(
                    step=state.current_step,
                    pending=cast(dict[str, JsonValue], state.pending_answers),
                    current_value=await self._profile_setting_current_value(
                        session=session,
                        user_id=user.id,
                        step=state.current_step,
                    ),
                )
            goal_target = {"goal:date": ProfileSettingsStep.GOAL_DATE}.get(action)
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
            if action == "goal:metrics":
                if step is not ProfileSettingsStep.GOAL_MENU:
                    raise OnboardingApplicationError("stale_action")
                goal = await ProfileRepository(session).get_training_goal(
                    user_id=user.id
                )
                if goal is None or goal.goal_template_id is None:
                    raise OnboardingApplicationError("stale_action")
                template = await TrainingCatalogRepository(session).active_goal_by_id(
                    goal_template_id=goal.goal_template_id
                )
                if template is None:
                    raise OnboardingApplicationError("stale_action")
                sport = _goal_sport_from_template_code(
                    template.code
                ) or self._goal_sport_from_metadata(goal)
                if sport is None:
                    raise OnboardingApplicationError("stale_action")
                values = self._metric_values_from_goal(goal, sport=sport)
                fields = _goal_metric_fields(sport=sport, code=template.code)
                state = await settings_repo.save(
                    user_id=user.id,
                    step=ProfileSettingsStep.GOAL_METRICS,
                    pending={
                        _GOAL_SPORT_KEY: sport,
                        "goal_template_code": template.code,
                        _GOAL_METRIC_FIELDS_KEY: list(fields),
                        _GOAL_METRIC_INDEX_KEY: 0,
                        _GOAL_METRIC_VALUES_KEY: values,
                        _MAIN_GOAL_CHANGED_KEY: False,
                    },
                )
                return ProfileSettingsResult(
                    step=state.current_step,
                    pending=cast(dict[str, JsonValue], state.pending_answers),
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
                raw_sport = pending.get(_GOAL_SPORT_KEY)
                if not isinstance(raw_sport, str):
                    raise OnboardingApplicationError("stale_action")
                allowed_codes = {
                    option_code
                    for option_code, _ in await self.goal_template_options(raw_sport)
                }
                if code not in allowed_codes:
                    raise OnboardingApplicationError("invalid_action")
                goal = await ProfileRepository(session).get_training_goal(
                    user_id=user.id
                )
                if goal is None:
                    raise OnboardingApplicationError("stale_action")
                main_changed = template.id != goal.goal_template_id
                await ProfileRepository(session).update_training_goal_fields(
                    user_id=user.id,
                    payload={
                        "main_goal": template.display_name,
                        "goal_template_id": template.id,
                        "target_distance_km": None,
                        "target_elevation_m": None,
                        "target_pace_seconds_per_km": None,
                        "target_swim_pace_seconds_per_100m": None,
                        "target_average_speed_kph": None,
                        "target_finish_time_seconds": None,
                        "goal_metadata_jsonb": {},
                    },
                )
                updated_goal = await ProfileRepository(session).get_training_goal(
                    user_id=user.id
                )
                if updated_goal is None:
                    raise OnboardingApplicationError("stale_action")
                state = await settings_repo.save(
                    user_id=user.id,
                    step=ProfileSettingsStep.GOAL_MENU,
                    pending=self._profile_goal_pending(updated_goal),
                )
                return ProfileSettingsResult(
                    step=state.current_step,
                    pending=cast(dict[str, JsonValue], state.pending_answers),
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
                await ProfileRepository(session).update_training_goal_fields(
                    user_id=user.id,
                    payload={
                        "secondary_priority": secondary_priority,
                        "supporting_goal_template_id": supporting_id,
                    },
                )
                updated_goal = await ProfileRepository(session).get_training_goal(
                    user_id=user.id
                )
                if updated_goal is None:
                    raise OnboardingApplicationError("stale_action")
                state = await settings_repo.save(
                    user_id=user.id,
                    step=ProfileSettingsStep.GOAL_MENU,
                    pending=self._profile_goal_pending(updated_goal),
                )
                return ProfileSettingsResult(
                    step=state.current_step,
                    pending=cast(dict[str, JsonValue], state.pending_answers),
                    saved_field="Secondary priority" if support_changed else None,
                )
            if action == "goal:back":
                if step not in {
                    ProfileSettingsStep.GOAL_MENU,
                    ProfileSettingsStep.GOAL_METRICS,
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
                next_pending: dict[str, object] = {}
                if next_step is ProfileSettingsStep.GOAL_MENU:
                    goal = await ProfileRepository(session).get_training_goal(
                        user_id=user.id
                    )
                    if goal is None:
                        raise OnboardingApplicationError("stale_action")
                    next_pending = self._profile_goal_pending(goal)
                state = await settings_repo.save(
                    user_id=user.id,
                    step=next_step,
                    pending=next_pending,
                )
                return ProfileSettingsResult(
                    step=state.current_step,
                    pending=cast(dict[str, JsonValue], state.pending_answers),
                )
            if action == "personal:back":
                if step is not ProfileSettingsStep.PERSONAL_GENDER:
                    raise OnboardingApplicationError("stale_action")
                state = await settings_repo.save(
                    user_id=user.id,
                    step=ProfileSettingsStep.PERSONAL_MENU,
                    pending=await self._profile_personal_pending(session, user),
                )
                return ProfileSettingsResult(
                    step=state.current_step,
                    pending=cast(dict[str, JsonValue], state.pending_answers),
                )
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
                    user_id=user.id,
                    step=ProfileSettingsStep.PERSONAL_MENU,
                    pending=await self._profile_personal_pending(session, user),
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
            if action == "goal:metric:skip":
                if step is not ProfileSettingsStep.GOAL_METRICS:
                    raise OnboardingApplicationError("stale_action")
                return await self._save_profile_goal_metric(
                    session=session,
                    user_id=user.id,
                    repo=settings_repo,
                    pending=pending,
                    value=None,
                )
            if action == "availability:edit":
                if step is not ProfileSettingsStep.AVAILABILITY_REVIEW:
                    raise OnboardingApplicationError("stale_action")
                profile = await ProfileRepository(session).get_athlete_profile(
                    user_id=user.id
                )
                confirmed = self._confirmed_profile_availability(profile)
                state = await settings_repo.save(
                    user_id=user.id,
                    step=ProfileSettingsStep.AVAILABILITY,
                    pending=(
                        {"current_availability": confirmed.model_dump(mode="json")}
                        if confirmed is not None
                        else {}
                    ),
                )
                return ProfileSettingsResult(step=state.current_step)
            if action == "availability:confirm":
                if step is not ProfileSettingsStep.AVAILABILITY_REVIEW:
                    raise OnboardingApplicationError("stale_action")
                draft = pending.get(_AVAILABILITY_DRAFT_KEY)
                if not isinstance(draft, dict):
                    raise OnboardingApplicationError("stale_action")
                try:
                    confirmed = ConfirmedWeeklyAvailability(days=draft["days"])
                except (KeyError, ValueError):
                    raise OnboardingApplicationError(
                        "invalid_availability_draft"
                    ) from None
                await ProfileRepository(session).update_athlete_profile_context_fields(
                    user_id=user.id,
                    payload={
                        "weekly_availability_jsonb": confirmed.model_dump(mode="json"),
                    },
                )
                state = await settings_repo.save(
                    user_id=user.id, step=ProfileSettingsStep.MENU, pending={}
                )
                return ProfileSettingsResult(
                    step=state.current_step, saved_field="Availability"
                )
            raise OnboardingApplicationError("invalid_action")

    async def submit_profile_settings_text(
        self, identity: TelegramIdentity, text: str
    ) -> ProfileSettingsResult | None:
        """Persist text only when the athlete selected the matching mini-flow."""

        async with self._session_factory.begin() as session:
            try:
                user = await self._profile_settings_user(session, identity)
            except OnboardingApplicationError:
                return None
            repo = ProfileSettingsRepository(session)
            state = await repo.get_or_create(user_id=user.id)
            step, pending = state.current_step, dict(state.pending_answers)
            if step is ProfileSettingsStep.MENU:
                return None
            if step is ProfileSettingsStep.GOAL_METRICS:
                try:
                    value = _parse_goal_metric(
                        self._current_profile_goal_metric_field(pending), text
                    )
                except ValueError:
                    raise OnboardingApplicationError("invalid_goal_metric") from None
                return await self._save_profile_goal_metric(
                    session=session,
                    user_id=user.id,
                    repo=repo,
                    pending=pending,
                    value=value,
                )
            if step is ProfileSettingsStep.GOAL_DATE:
                event_date = _parse_event_date(text)
                if event_date is not None and event_date < utc_now().date():
                    event_date = None
                if event_date is None:
                    raise OnboardingApplicationError("invalid_event_date")
                pending["event_date"] = event_date.isoformat()
                return await self._save_profile_goal(
                    session=session,
                    user_id=user.id,
                    repo=repo,
                    pending=pending,
                )
            if step is ProfileSettingsStep.AVAILABILITY:
                cleaned = text.strip()
                if not (
                    _CONTEXT_TEXT_MIN_LENGTH <= len(cleaned) <= _CONTEXT_TEXT_MAX_LENGTH
                ):
                    raise OnboardingApplicationError("invalid_action")
                goal = await ProfileRepository(session).get_training_goal(
                    user_id=user.id
                )
                sport = (
                    self._goal_sport_from_metadata(goal) if goal is not None else None
                )
                disciplines_by_sport = {
                    GoalSport.RUNNING.value: ("running",),
                    GoalSport.CYCLING.value: ("cycling",),
                    GoalSport.SWIMMING.value: ("swimming",),
                    GoalSport.TRIATHLON.value: ("cycling", "running", "swimming"),
                }
                profile = await ProfileRepository(session).get_athlete_profile(
                    user_id=user.id
                )
                confirmed = self._confirmed_profile_availability(profile)
                try:
                    extraction = (
                        await self._availability.revise(
                            current=confirmed,
                            change_request=cleaned,
                            goal_disciplines=disciplines_by_sport.get(
                                sport or "", ()
                            ),
                        )
                        if confirmed is not None
                        else await self._availability.extract(
                            cleaned,
                            goal_disciplines=disciplines_by_sport.get(
                                sport or "", ()
                            ),
                        )
                    )
                except AvailabilityExtractionError:
                    raise OnboardingApplicationError(
                        "availability_extraction_failed"
                    ) from None
                next_step = (
                    ProfileSettingsStep.AVAILABILITY_REVIEW
                    if extraction.parse_status == "complete"
                    else ProfileSettingsStep.AVAILABILITY
                )
                state = await repo.save(
                    user_id=user.id,
                    step=next_step,
                    pending={
                        **(
                            {
                                "current_availability": confirmed.model_dump(
                                    mode="json"
                                )
                            }
                            if confirmed is not None
                            else {}
                        ),
                        _AVAILABILITY_SOURCE_KEY: cleaned,
                        _AVAILABILITY_DRAFT_KEY: extraction.model_dump(mode="json"),
                    },
                )
                return ProfileSettingsResult(
                    step=state.current_step,
                    pending=cast(dict[str, JsonValue], state.pending_answers),
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
            if step is ProfileSettingsStep.PERSONAL_TIMEZONE:
                timezone = self._parse_timezone(text)
                if timezone is None:
                    raise OnboardingApplicationError("invalid_timezone")
                await UserRepository(session).update_timezone(
                    user_id=user.id, timezone=timezone
                )
                state = await repo.save(
                    user_id=user.id,
                    step=ProfileSettingsStep.PERSONAL_MENU,
                    pending=await self._profile_personal_pending(session, user),
                )
                return ProfileSettingsResult(
                    step=state.current_step, saved_field="Timezone"
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
                await ProfileRepository(session).update_athlete_profile_fields(
                    user_id=user.id, payload={field: value}
                )
                state = await repo.save(
                    user_id=user.id,
                    step=ProfileSettingsStep.PERSONAL_MENU,
                    pending=await self._profile_personal_pending(session, user),
                )
                return ProfileSettingsResult(step=state.current_step, saved_field=label)
            raise OnboardingApplicationError("stale_action")

    @staticmethod
    def _parse_timezone(value: str) -> str | None:
        candidate = value.strip()
        if not candidate or len(candidate) > 64:
            return None
        try:
            return ZoneInfo(candidate).key
        except ZoneInfoNotFoundError:
            return None

    async def _profile_settings_user(
        self, session: AsyncSession, identity: TelegramIdentity
    ) -> User:
        """Authorize profile edits for every completed account lifecycle.

        Some existing accounts were marked ``PROFILE_COMPLETED`` before their
        onboarding checkpoint was normalized. The bot correctly exposes Change
        profile to those athletes, so the settings service must not reject them
        merely because that historical checkpoint remains active or is absent.
        """

        user = await self._require_user(session, identity)
        onboarding = await OnboardingRepository(session).get_for_user(
            user_id=user.id, for_update=True
        )
        if user.status in {
            UserStatus.ONBOARDING_COMPLETED,
            UserStatus.PROFILE_COMPLETED,
        }:
            return user
        if onboarding is not None and onboarding.status is OnboardingStatus.COMPLETED:
            return user
        raise OnboardingApplicationError("stale_action")

    @staticmethod
    def _goal_sport_from_metadata(goal: TrainingGoal) -> str | None:
        metadata = goal.goal_metadata_jsonb
        primary = metadata.get("primary_goal") if isinstance(metadata, dict) else None
        sport = primary.get("discipline") if isinstance(primary, dict) else None
        if isinstance(sport, str) and sport in {item.value for item in GoalSport}:
            return sport
        return None

    @staticmethod
    def _metric_values_from_goal(
        goal: TrainingGoal, *, sport: str
    ) -> dict[str, JsonValue]:
        values: dict[str, JsonValue] = {}
        if goal.target_distance_km is not None:
            distance_key = (
                "cycling_distance"
                if sport == GoalSport.CYCLING.value
                else "running_distance"
            )
            values[distance_key] = goal.target_distance_km
        if goal.target_elevation_m is not None:
            values["elevation"] = goal.target_elevation_m
        if goal.target_pace_seconds_per_km is not None:
            values["running_pace"] = goal.target_pace_seconds_per_km
        if goal.target_swim_pace_seconds_per_100m is not None:
            values["swimming_pace"] = goal.target_swim_pace_seconds_per_100m
        if goal.target_average_speed_kph is not None:
            values["cycling_average_speed"] = goal.target_average_speed_kph
        if goal.target_finish_time_seconds is not None:
            values["triathlon_finish_time"] = goal.target_finish_time_seconds
        primary = (
            goal.goal_metadata_jsonb.get("primary_goal")
            if isinstance(goal.goal_metadata_jsonb, dict)
            else None
        )
        target_distance = (
            primary.get("target_distance") if isinstance(primary, dict) else None
        )
        if isinstance(target_distance, dict) and target_distance.get("unit") == "m":
            value = target_distance.get("value")
            if isinstance(value, int):
                values["swimming_distance"] = value
        return values

    @staticmethod
    def _current_profile_goal_metric_field(pending: Mapping[str, object]) -> str:
        fields = pending.get(_GOAL_METRIC_FIELDS_KEY)
        index = pending.get(_GOAL_METRIC_INDEX_KEY)
        if (
            not isinstance(fields, list)
            or not all(isinstance(item, str) for item in fields)
            or not isinstance(index, int)
            or isinstance(index, bool)
            or not 0 <= index < len(fields)
        ):
            raise OnboardingApplicationError("stale_action")
        return cast(str, fields[index])

    async def _save_profile_goal_metric(
        self,
        *,
        session: AsyncSession,
        user_id: uuid.UUID,
        repo: ProfileSettingsRepository,
        pending: dict[str, object],
        value: JsonValue | None,
    ) -> ProfileSettingsResult:
        field = self._current_profile_goal_metric_field(pending)
        raw_values = pending.get(_GOAL_METRIC_VALUES_KEY, {})
        if not isinstance(raw_values, dict):
            raise OnboardingApplicationError("stale_action")
        values = dict(cast(dict[str, JsonValue], raw_values))
        values[field] = value
        fields = pending.get(_GOAL_METRIC_FIELDS_KEY)
        index = pending.get(_GOAL_METRIC_INDEX_KEY)
        if not isinstance(fields, list) or not isinstance(index, int):
            raise OnboardingApplicationError("stale_action")
        index += 1
        if index < len(fields):
            pending[_GOAL_METRIC_VALUES_KEY] = values
            pending[_GOAL_METRIC_INDEX_KEY] = index
            state = await repo.save(
                user_id=user_id, step=ProfileSettingsStep.GOAL_METRICS, pending=pending
            )
            return ProfileSettingsResult(
                step=state.current_step,
                pending=cast(dict[str, JsonValue], state.pending_answers),
            )
        sport = pending.get(_GOAL_SPORT_KEY)
        code = pending.get("goal_template_code")
        if not isinstance(sport, str) or not isinstance(code, str):
            raise OnboardingApplicationError("stale_action")
        template = await TrainingCatalogRepository(session).active_goal_by_code(
            code=code, kind=GoalTemplateKind.PRIMARY
        )
        if template is None:
            raise OnboardingApplicationError("stale_action")
        payload = _target_payload_from_metric_values(
            sport=sport, code=code, values=values
        )
        await ProfileRepository(session).update_training_goal_fields(
            user_id=user_id, payload=payload
        )
        updated_goal = await ProfileRepository(session).get_training_goal(
            user_id=user_id
        )
        if updated_goal is None:
            raise OnboardingApplicationError("stale_action")
        state = await repo.save(
            user_id=user_id,
            step=ProfileSettingsStep.GOAL_MENU,
            pending=self._profile_goal_pending(updated_goal),
        )
        return ProfileSettingsResult(
            step=state.current_step,
            pending=cast(dict[str, JsonValue], state.pending_answers),
            saved_field="Performance targets",
        )

    @staticmethod
    async def _profile_setting_current_value(
        *,
        session: AsyncSession,
        user_id: uuid.UUID,
        step: ProfileSettingsStep,
    ) -> str | int | float | None:
        profiles = ProfileRepository(session)
        if step is ProfileSettingsStep.PERSONAL_TIMEZONE:
            user = await UserRepository(session).get_by_id(user_id)
            return user.timezone if user is not None else None
        if step is ProfileSettingsStep.GOAL_DATE:
            goal = await profiles.get_training_goal(user_id=user_id)
            if goal is None:
                return None
            return goal.event_date.isoformat() if goal.event_date is not None else None
        profile = await profiles.get_athlete_profile(user_id=user_id)
        if profile is None:
            return None
        values: dict[ProfileSettingsStep, str | int | float | None] = {
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
    def _confirmed_profile_availability(
        profile: object | None,
    ) -> ConfirmedWeeklyAvailability | None:
        raw = getattr(profile, "weekly_availability_jsonb", None)
        if not isinstance(raw, dict):
            return None
        try:
            return ConfirmedWeeklyAvailability.model_validate(raw)
        except ValueError:
            return None

    @staticmethod
    async def _profile_personal_pending(
        session: AsyncSession, user: User
    ) -> dict[str, object]:
        profile = await ProfileRepository(session).get_athlete_profile(user_id=user.id)
        return {
            "birth_year": profile.birth_year if profile else None,
            "gender": (
                profile.gender.value
                if profile is not None and profile.gender is not None
                else None
            ),
            "weight_kg": profile.weight_kg if profile else None,
            "height_cm": profile.height_cm if profile else None,
            "timezone": user.timezone,
        }

    @staticmethod
    def _profile_goal_pending(goal: TrainingGoal) -> dict[str, object]:
        return {
            "main_goal": goal.main_goal,
            "event_date": (
                goal.event_date.isoformat() if goal.event_date is not None else None
            ),
            "secondary_priority": goal.secondary_priority,
            "target_distance_km": goal.target_distance_km,
            "target_elevation_m": goal.target_elevation_m,
            "target_pace_seconds_per_km": goal.target_pace_seconds_per_km,
            "target_swim_pace_seconds_per_100m": (
                goal.target_swim_pace_seconds_per_100m
            ),
            "target_average_speed_kph": goal.target_average_speed_kph,
            "target_finish_time_seconds": goal.target_finish_time_seconds,
        }

    async def _save_profile_goal(
        self,
        *,
        session: AsyncSession,
        user_id: uuid.UUID,
        repo: ProfileSettingsRepository,
        pending: dict[str, object],
    ) -> ProfileSettingsResult:
        main_goal = pending.get("main_goal")
        secondary = pending.get("secondary_priority")
        raw_event_date = pending.get("event_date")
        event_date = (
            date.fromisoformat(raw_event_date)
            if isinstance(raw_event_date, str)
            else None
        )
        if not isinstance(main_goal, str) or (
            secondary is not None and not isinstance(secondary, str)
        ):
            raise OnboardingApplicationError("stale_action")
        profiles = ProfileRepository(session)
        previous = await profiles.get_training_goal(user_id=user_id)
        if previous is None:
            raise OnboardingApplicationError("stale_action")
        changed = (
            previous.main_goal,
            previous.event_date,
            previous.secondary_priority,
        ) != (main_goal, event_date, secondary)
        await profiles.update_training_goal_fields(
            user_id=user_id,
            payload={
                "main_goal": main_goal,
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
            OnboardingStep.PROFILE_TIMEZONE_INTAKE,
        }:
            return await self._handle_profile_text(identity, text)
        if (
            onboarding.status is OnboardingStatus.ACTIVE
            and onboarding.current_step is OnboardingStep.GOAL_EVENT_DATE
        ):
            return await self.submit_event_date(identity, text)
        if (
            onboarding.status is OnboardingStatus.ACTIVE
            and onboarding.current_step is OnboardingStep.GOAL_METRIC_INTAKE
        ):
            return await self.submit_goal_metric(identity, text)
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
            and onboarding.current_step is OnboardingStep.BASELINE_INTAKE
        ):
            return self._result(user, onboarding)
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
            if key == "birth_year":
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
                onboarding = await OnboardingRepository(session).save_progress(
                    user_id=user.id,
                    current_step=OnboardingStep.PROFILE_TIMEZONE_INTAKE,
                    answers=cast(dict[str, object], answers),
                )
                return self._result(user, onboarding)

            if step is OnboardingStep.PROFILE_TIMEZONE_INTAKE:
                timezone = self._parse_timezone(text)
                if timezone is None:
                    return self._result(
                        user,
                        onboarding,
                        kind="profile_validation_error",
                        error_code="invalid_timezone",
                    )
                await UserRepository(session).update_timezone(
                    user_id=user.id, timezone=timezone
                )
                goal = await ProfileRepository(session).get_training_goal(
                    user_id=user.id
                )
                onboarding = await OnboardingRepository(session).save_progress(
                    user_id=user.id,
                    current_step=(
                        OnboardingStep.AVAILABILITY_INTAKE
                        if goal is not None
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

        if step is OnboardingStep.AVAILABILITY_INTAKE:
            async with self._session_factory() as session:
                user, onboarding = await self._locked_state(session, identity)
                goal = await ProfileRepository(session).get_training_goal(
                    user_id=user.id
                )
                if goal is None or goal.goal_template_id is None:
                    raise OnboardingApplicationError("stale_action")
                expected_roles = {goal.goal_template_id: GoalContextRole.TARGET}
                if goal.supporting_goal_template_id is not None:
                    expected_roles[goal.supporting_goal_template_id] = (
                        GoalContextRole.SUPPORTING
                    )
                contexts = await TrainingCatalogRepository(session).contexts_for_goals(
                    goal_template_ids=expected_roles.keys()
                )
                discipline_names = tuple(
                    sorted(
                        {
                            "strength_training"
                            if context.discipline is Discipline.STRENGTH
                            else context.discipline.value.casefold()
                            for relation, context in contexts
                            if expected_roles.get(relation.goal_template_id)
                            is relation.role
                            and context.discipline
                            in {
                                Discipline.RUNNING,
                                Discipline.CYCLING,
                                Discipline.SWIMMING,
                                Discipline.STRENGTH,
                            }
                        }
                    )
                )
            try:
                extraction = await self._availability.extract(
                    cleaned, goal_disciplines=discipline_names
                )
            except AvailabilityExtractionError:
                async with self._session_factory.begin() as session:
                    user, onboarding = await self._locked_state(session, identity)
                    return self._result(
                        user,
                        onboarding,
                        kind="availability_clarification",
                        error_code="availability_extraction_failed",
                    )
            async with self._session_factory.begin() as session:
                user, onboarding = await self._locked_state(session, identity)
                self._require_active(onboarding)
                if onboarding.current_step is not step:
                    raise OnboardingApplicationError("stale_action")
                answers = self._answers(onboarding)
                answers[_AVAILABILITY_SOURCE_KEY] = cleaned
                answers[_AVAILABILITY_DRAFT_KEY] = extraction.model_dump(mode="json")
                if extraction.parse_status == "needs_clarification":
                    return self._result(
                        user,
                        onboarding,
                        kind="availability_clarification",
                        error_code=extraction.clarification_reason,
                    )
                if extraction.parse_status == "needs_details":
                    onboarding = await OnboardingRepository(session).save_progress(
                        user_id=user.id,
                        current_step=OnboardingStep.AVAILABILITY_INTAKE,
                        answers=cast(dict[str, object], answers),
                    )
                    return self._result(user, onboarding, kind="availability_details")
                onboarding = await OnboardingRepository(session).save_progress(
                    user_id=user.id,
                    current_step=OnboardingStep.AVAILABILITY_REVIEW,
                    answers=cast(dict[str, object], answers),
                )
                return self._result(user, onboarding)

        async with self._session_factory.begin() as session:
            user, onboarding = await self._locked_state(session, identity)
            self._require_active(onboarding)
            if onboarding.current_step is not step:
                raise OnboardingApplicationError("stale_action")
            profiles = ProfileRepository(session)
            answers = self._answers(onboarding)
            field_by_step = {
                OnboardingStep.HEALTH_LIMITATIONS_INTAKE: "health_limitations_text",
            }
            next_step_by_step = {
                # Keep the health checkpoint while the next transaction builds
                # the goal-adaptive baseline fields.
                OnboardingStep.HEALTH_LIMITATIONS_INTAKE: (
                    OnboardingStep.HEALTH_LIMITATIONS_INTAKE
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
            onboarding = await OnboardingRepository(session).save_progress(
                user_id=user.id,
                current_step=next_step_by_step[step],
                answers=cast(dict[str, object], answers),
            )
            saved_result = self._result(user, onboarding)

        if step is OnboardingStep.HEALTH_LIMITATIONS_INTAKE:
            return await self._start_baseline(identity=identity, user_id=user_id)
        return saved_result

    async def confirm_availability(
        self, identity: TelegramIdentity
    ) -> OnboardingServiceResult:
        """Persist only the reviewed, complete availability draft."""

        async with self._session_factory.begin() as session:
            user, onboarding = await self._locked_state(session, identity)
            self._require_active(onboarding)
            if onboarding.current_step is not OnboardingStep.AVAILABILITY_REVIEW:
                raise OnboardingApplicationError("stale_action")
            answers = self._answers(onboarding)
            draft = answers.get(_AVAILABILITY_DRAFT_KEY)
            if not isinstance(draft, dict):
                raise OnboardingApplicationError("stale_action")
            try:
                confirmed = ConfirmedWeeklyAvailability(days=draft["days"])
            except (KeyError, ValueError):
                raise OnboardingApplicationError("invalid_availability_draft") from None
            await ProfileRepository(session).update_athlete_profile_context_fields(
                user_id=user.id,
                payload={
                    "weekly_availability_jsonb": confirmed.model_dump(mode="json"),
                },
            )
            answers.pop(_AVAILABILITY_SOURCE_KEY, None)
            answers.pop(_AVAILABILITY_DRAFT_KEY, None)
            onboarding = await OnboardingRepository(session).save_progress(
                user_id=user.id,
                current_step=OnboardingStep.EQUIPMENT_RECOMMENDATION,
                answers=cast(dict[str, object], answers),
            )
        return await self._resume_capability_review(identity=identity, user_id=user.id)

    async def edit_availability(
        self, identity: TelegramIdentity
    ) -> OnboardingServiceResult:
        async with self._session_factory.begin() as session:
            user, onboarding = await self._locked_state(session, identity)
            self._require_active(onboarding)
            if onboarding.current_step is not OnboardingStep.AVAILABILITY_REVIEW:
                raise OnboardingApplicationError("stale_action")
            answers = self._answers(onboarding)
            answers.pop(_AVAILABILITY_SOURCE_KEY, None)
            answers.pop(_AVAILABILITY_DRAFT_KEY, None)
            onboarding = await OnboardingRepository(session).save_progress(
                user_id=user.id,
                current_step=OnboardingStep.AVAILABILITY_INTAKE,
                answers=cast(dict[str, object], answers),
            )
            return self._result(user, onboarding)

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
            elif onboarding.current_step is OnboardingStep.GOAL_EVENT_DATE:
                kind = "goal_event_date"
            elif onboarding.current_step is OnboardingStep.GOAL_SWIMMING_TYPE:
                kind = "goal_swimming_type"
            elif onboarding.current_step is OnboardingStep.GOAL_METRIC_INTAKE:
                kind = "goal_metric_intake"
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
            elif onboarding.current_step is OnboardingStep.PROFILE_TIMEZONE_INTAKE:
                kind = "profile_timezone_intake"
            elif onboarding.current_step is OnboardingStep.AVAILABILITY_INTAKE:
                kind = "availability_intake"
            elif onboarding.current_step is OnboardingStep.AVAILABILITY_REVIEW:
                kind = "availability_review"
            elif onboarding.current_step is OnboardingStep.EQUIPMENT_RECOMMENDATION:
                kind = "equipment_recommendation"
            elif onboarding.current_step is OnboardingStep.EQUIPMENT_INTAKE:
                kind = "equipment_intake"
            elif onboarding.current_step is OnboardingStep.HEALTH_LIMITATIONS_INTAKE:
                kind = "health_limitations_intake"
            elif onboarding.current_step is OnboardingStep.BASELINE_INTAKE:
                kind = "baseline_intake"
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
