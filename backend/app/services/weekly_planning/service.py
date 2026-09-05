"""Generate and persist one evidence-gated plan for the following week."""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from langchain_core.messages import BaseMessage, HumanMessage
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.base import utc_now
from app.db.models import WeeklyTrainingPlan
from app.domain.enums import (
    Discipline,
    GoalContextRole,
    GoalTemplateKind,
    LLMUsageStatus,
    OnboardingStep,
    UserStatus,
)
from app.integrations.llm.models import (
    StructuredModelResponse,
    StructuredOnboardingModel,
)
from app.observability.callbacks import build_langchain_run_config
from app.observability.noop import NoOpAIWorkflowObserver
from app.observability.protocol import (
    AIWorkflowObserver,
    AIWorkflowRunError,
    AIWorkflowRunMetadata,
    AIWorkflowRunResult,
    ProviderMode,
)
from app.repositories.athlete_baselines import AthleteBaselineRepository
from app.repositories.athlete_capabilities import AthleteCapabilityRepository
from app.repositories.fitness import FitnessRepository
from app.repositories.llm_usage import LLMUsageRepository
from app.repositories.profiles import ProfileRepository
from app.repositories.training_catalog import TrainingCatalogRepository
from app.repositories.users import UserRepository
from app.repositories.weekly_plan_outcomes import WeeklyPlanOutcomeRepository
from app.repositories.weekly_plans import WeeklyTrainingPlanRepository
from app.schemas.availability import ConfirmedWeeklyAvailability
from app.schemas.baseline import AthleteBaselineData, TrainingPreferences
from app.schemas.common import TelegramIdentity
from app.schemas.weekly_plans import (
    FirstWeekPlan,
    FirstWeekPlanPrescription,
    PlanReadiness,
    WeeklyPlan,
    WeeklyPlanPrescription,
)
from app.services.fitness.calculator import (
    CALCULATION_VERSION,
    calculate_baseline_window,
)
from app.services.fitness.service import _fitness_evidence_for_workout
from app.services.weekly_planning.comparison import WeekComparison, compare_week
from app.services.weekly_planning.evidence import (
    build_evidence_snapshot,
    build_plan_readiness,
)
from app.services.weekly_planning.scheduler import schedule_prescription
from app.services.weekly_planning.tiers import (
    BaselineTier,
    resolve_first_week_tiers,
)
from app.services.weekly_planning.validation import (
    ValidationOutcome,
    build_fallback_week,
    make_first_week_plan,
    repair_plan,
    validate_first_week_plan,
    validate_plan,
)
from app.services.weekly_planning.zones import (
    ResolvedIntensityZones,
    resolve_first_week_zones,
)
from app.workflows.prompts.weekly_planning import (
    FIRST_WEEK_PLANNER_PROMPT_VERSION,
    WEEKLY_PLANNER_PROMPT_VERSION,
    build_first_week_planner_messages,
    build_weekly_planner_messages,
    render_availability_constraints,
)

_PLANNER_FEATURE = "WEEKLY_PLAN"

logger = logging.getLogger(__name__)

_FIRST_WEEK_REPAIR_ATTEMPTS = 2


@dataclass(frozen=True, slots=True)
class WeeklyPlanningResult:
    """Safe service outcome consumed by the deterministic Telegram layer."""

    kind: Literal[
        "created",
        "existing",
        "insufficient",
        "unavailable",
        "baseline_required",
        "timezone_required",
    ]
    plan: WeeklyPlan | FirstWeekPlan | None = None
    readiness: PlanReadiness | None = None
    generation_source: Literal["model", "model_repaired", "fallback"] | None = None


@dataclass(frozen=True, slots=True)
class _PlanningInput:
    athlete_id: uuid.UUID
    week_start: date
    readiness: PlanReadiness
    baseline: AthleteBaselineData | None
    availability: ConfirmedWeeklyAvailability | None
    preferences: TrainingPreferences | None
    target_disciplines: tuple[Discipline, ...]
    prompt_context: dict[str, object]
    evidence_snapshot: dict[str, object]
    input_digest: str
    zones: dict[Discipline, ResolvedIntensityZones]
    tiers: dict[Discipline, BaselineTier]


@dataclass(frozen=True, slots=True)
class _TargetContext:
    code: str
    display_name: str
    discipline: Discipline
    role: GoalContextRole


def _confirmed_availability(profile: object) -> dict[str, object] | None:
    raw = getattr(profile, "weekly_availability_jsonb", None)
    if not isinstance(raw, dict):
        return None
    try:
        return ConfirmedWeeklyAvailability.model_validate(raw).model_dump(mode="json")
    except ValidationError:
        return None


def _confirmed_availability_model(
    profile: object,
) -> ConfirmedWeeklyAvailability | None:
    raw = getattr(profile, "weekly_availability_jsonb", None)
    if not isinstance(raw, dict):
        return None
    try:
        return ConfirmedWeeklyAvailability.model_validate(raw)
    except ValidationError:
        return None


def _planner_baseline_payload(
    baseline: AthleteBaselineData | None,
) -> dict[str, object] | None:
    """Serialize the complete structured baseline for the first-week prompt."""

    if baseline is None:
        return None
    return baseline.model_dump(mode="json", exclude_none=True)


def _ongoing_goal_context(
    *,
    goal: object | None,
    target_contexts: tuple[_TargetContext, ...],
    baseline: AthleteBaselineData | None,
) -> dict[str, object]:
    """Return goal-directed data only for the ongoing planner."""

    event_date = getattr(goal, "event_date", None)
    return {
        "main_goal": getattr(goal, "main_goal", None),
        "event_date": (
            event_date.isoformat() if isinstance(event_date, date) else None
        ),
        "secondary_priority": getattr(goal, "secondary_priority", None),
        "goal_metadata": getattr(goal, "goal_metadata_jsonb", None),
        "triathlon_context": (
            baseline.triathlon.model_dump(mode="json")
            if baseline is not None and baseline.triathlon is not None
            else None
        ),
        "performance_targets": (
            {
                "distance_km": getattr(goal, "target_distance_km", None),
                "elevation_m": getattr(goal, "target_elevation_m", None),
                "running_pace_seconds_per_km": getattr(
                    goal, "target_pace_seconds_per_km", None
                ),
                "swim_pace_seconds_per_100m": getattr(
                    goal, "target_swim_pace_seconds_per_100m", None
                ),
                "average_speed_kph": getattr(goal, "target_average_speed_kph", None),
                "finish_time_seconds": getattr(
                    goal, "target_finish_time_seconds", None
                ),
            }
            if goal is not None
            else None
        ),
        "target_contexts": [
            {
                "code": item.code,
                "display_name": item.display_name,
                "discipline": item.discipline.value,
                "role": item.role.value,
            }
            for item in target_contexts
        ],
    }


def _baseline_target_minutes(
    baseline: AthleteBaselineData | None, discipline: Discipline
) -> int | None:
    if baseline is None:
        return None
    record = {
        Discipline.RUNNING: baseline.running,
        Discipline.CYCLING: baseline.cycling,
        Discipline.SWIMMING: baseline.swimming,
    }.get(discipline)
    value = getattr(record, "typical_weekly_duration_minutes", None)
    return value if isinstance(value, int) else None


def _is_untrained_discipline(
    baseline: AthleteBaselineData | None,
    readiness: PlanReadiness,
    discipline: Discipline,
) -> bool:
    if discipline is Discipline.STRENGTH or baseline is None:
        return False
    record = {
        Discipline.RUNNING: baseline.running,
        Discipline.CYCLING: baseline.cycling,
        Discipline.SWIMMING: baseline.swimming,
    }.get(discipline)
    evidence_count = next(
        (
            row.session_count
            for row in readiness.disciplines
            if row.discipline is discipline
        ),
        0,
    )
    return (
        record is not None
        and getattr(record, "typical_weekly_sessions", None) == 0
        and getattr(record, "typical_weekly_duration_minutes", None) == 0
        and evidence_count == 0
    )


def _validation_snapshot(
    kind: str,
    outcome: ValidationOutcome,
    *,
    generation_source: Literal["model", "model_repaired", "fallback"] | None = None,
    fallback_reason: str | None = None,
    fallback_errors: tuple[dict[str, str], ...] = (),
) -> dict[str, object]:
    snapshot: dict[str, object] = {
        "kind": kind,
        "violations": [
            {
                "code": violation.code,
                "discipline": (
                    violation.discipline.value
                    if violation.discipline is not None
                    else None
                ),
                "day": violation.day.isoformat() if violation.day is not None else None,
            }
            for violation in outcome.violations
        ],
    }
    if generation_source is not None:
        snapshot["generation_source"] = generation_source
    if fallback_reason is not None:
        snapshot["fallback_reason"] = fallback_reason
        snapshot["fallback_errors"] = list(fallback_errors)
    return snapshot


def _stored_generation_source(
    validation: object,
) -> Literal["model", "model_repaired", "fallback"] | None:
    if not isinstance(validation, dict):
        return None
    source = validation.get("generation_source")
    return source if source in {"model", "model_repaired", "fallback"} else None


def _first_week_validation_errors(
    outcome: ValidationOutcome,
) -> tuple[dict[str, str], ...]:
    return tuple(
        {
            "code": violation.code,
            "detail": violation.detail,
        }
        for violation in outcome.violations
    )


def _first_week_schema_errors(
    error: ValidationError,
) -> tuple[dict[str, str], ...]:
    return tuple(
        {
            "code": "SCHEMA_VALIDATION_FAILED",
            "detail": f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}",
        }
        for item in error.errors(include_url=False)
    )


def _planning_input_digest(
    *,
    prompt_context: dict[str, object],
) -> str:
    """Digest the complete prompt context without retaining raw health text."""

    payload = dict(prompt_context)
    health_limitations = payload.pop("health_limitations", None)
    payload["health_limitations_digest"] = hashlib.sha256(
        str(health_limitations or "").encode()
    ).hexdigest()
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _plan_fits_availability(plan: WeeklyPlan, availability: dict[str, object]) -> bool:
    raw_days = availability.get("days")
    if not isinstance(raw_days, dict):
        return False
    for plan_day in plan.days:
        details = raw_days.get(plan_day.date.strftime("%A").casefold())
        if not isinstance(details, dict):
            return False
        allowed = details.get("disciplines")
        windows = details.get("time_windows")
        if not isinstance(allowed, list) or not isinstance(windows, list):
            return False
        limit = sum(
            int(window.get("duration_minutes", 0))
            for window in windows
            if isinstance(window, dict)
        )
        if plan_day.sessions and (not details.get("available") or limit <= 0):
            return False
        if any(
            session.discipline.value.casefold() not in allowed
            or (session.targets.duration_minutes or 0) > limit
            for session in plan_day.sessions
        ):
            return False
    return True


class WeeklyPlanningService:
    """Shared planning harness; use a named subclass for a concrete planner mode."""

    planner_mode: Literal["FIRST_WEEK", "ONGOING"] = "ONGOING"

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        model: StructuredOnboardingModel,
        observer: AIWorkflowObserver | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._model = model
        self._observer = observer or NoOpAIWorkflowObserver()

    async def has_plan_for_next_week(self, identity: TelegramIdentity) -> bool:
        """Return only whether the current athlete already has next week's plan."""

        async with self._session_factory() as session:
            user = await UserRepository(session).get_by_telegram_id(
                identity.telegram_user_id
            )
            if user is None:
                return False
            week_start = next_week_start(utc_now(), user.timezone)
            return (
                await WeeklyTrainingPlanRepository(session).get_for_week(
                    athlete_id=user.id,
                    week_start=week_start,
                )
                is not None
            )

    async def view_next_week(self, identity: TelegramIdentity) -> WeeklyPlanningResult:
        """Read the persisted plan only; this path never contacts a model."""

        async with self._session_factory() as session:
            user = await UserRepository(session).get_by_telegram_id(
                identity.telegram_user_id
            )
            if user is None:
                return WeeklyPlanningResult(kind="unavailable")
            plan = await WeeklyTrainingPlanRepository(session).get_for_week(
                athlete_id=user.id,
                week_start=next_week_start(utc_now(), user.timezone),
            )
            return (
                WeeklyPlanningResult(
                    kind="existing",
                    plan=_plan_schema(plan),
                    generation_source=_stored_generation_source(plan.validation_jsonb),
                )
                if plan is not None
                else WeeklyPlanningResult(kind="unavailable")
            )

    async def delete_next_week(self, identity: TelegramIdentity) -> bool:
        """Discard the current next-week plan so the athlete can generate another."""

        async with self._session_factory() as session, session.begin():
            user = await UserRepository(session).get_by_telegram_id(
                identity.telegram_user_id
            )
            if user is None:
                return False
            discarded = await WeeklyTrainingPlanRepository(session).supersede_current(
                athlete_id=user.id,
                week_start=next_week_start(utc_now(), user.timezone),
            )
            return discarded is not None

    async def compare_finished_week(
        self, identity: TelegramIdentity
    ) -> WeekComparison | None:
        """Persist and return the deterministic comparison for the week just ended."""

        async with self._session_factory() as session:
            user = await UserRepository(session).get_by_telegram_id(
                identity.telegram_user_id
            )
            if user is None:
                return None
            local_today = (
                _as_utc(utc_now()).astimezone(_zone_or_utc(user.timezone)).date()
            )
            current_week_start = date.fromordinal(
                local_today.toordinal() - local_today.weekday()
            )
            week_start = date.fromordinal(current_week_start.toordinal() - 7)
            stored_plan = await WeeklyTrainingPlanRepository(session).get_for_week(
                athlete_id=user.id, week_start=week_start
            )
            if stored_plan is None:
                return None
            plan = _plan_schema(stored_plan)
            if isinstance(plan, FirstWeekPlan):
                # First-week menus are athlete-placed; comparison begins once actual
                # session placement/logging is linked in the evaluator.
                return None
            disciplines = tuple(
                sorted(
                    {
                        session.discipline
                        for day in plan.days
                        for session in day.sessions
                    },
                    key=lambda discipline: discipline.value,
                )
            )
            workouts = await FitnessRepository(session).workouts_for_window(
                athlete_id=user.id,
                disciplines=disciplines,
                started_at=datetime.combine(week_start, time.min, tzinfo=UTC),
                ended_at=(
                    datetime.combine(
                        date.fromordinal(week_start.toordinal() + 7),
                        time.min,
                        tzinfo=UTC,
                    )
                    - timedelta(microseconds=1)
                ),
            )
            comparison = compare_week(
                plan_id=stored_plan.id,
                plan=plan,
                workouts=tuple(
                    _fitness_evidence_for_workout(workout) for workout in workouts
                ),
            )
        async with self._session_factory.begin() as session:
            await WeeklyPlanOutcomeRepository(session).upsert(
                athlete_id=user.id,
                plan_id=stored_plan.id,
                week_start=week_start,
                comparison_jsonb=comparison.model_dump(mode="json"),
            )
        return comparison

    async def generate_next_week(
        self, identity: TelegramIdentity
    ) -> WeeklyPlanningResult:
        """Create a first plan only after deterministic evidence preflight."""

        prepared = await self._prepare(identity)
        if isinstance(prepared, WeeklyPlanningResult):
            return prepared

        started_at = datetime.now(UTC)
        metadata = AIWorkflowRunMetadata(
            workflow_name="weekly_training_plan",
            run_id=uuid.uuid4(),
            # Shared structured-model boundary requires an onboarding step.
            onboarding_step=OnboardingStep.TRAINING_HISTORY_IMPORT,
            provider_mode=self._model.provider_mode,
            model_name=self._model.model_name,
            started_at=started_at,
        )
        await self._observer.on_run_started(metadata)

        try:
            response = await self._model.ainvoke_structured(
                # Existing provider protocol is shared with onboarding. Feature
                # metadata below distinguishes this non-onboarding call safely.
                step=OnboardingStep.TRAINING_HISTORY_IMPORT,
                schema=(
                    FirstWeekPlanPrescription
                    if self.planner_mode == "FIRST_WEEK"
                    else WeeklyPlanPrescription
                ),
                messages=self._build_planner_messages(prepared.prompt_context),
                config=build_langchain_run_config(metadata),
            )
        except Exception as exc:  # Provider adapters surface vendor-specific errors.
            logger.error(
                "weekly_plan_provider_error athlete_id=%s error_type=%s",
                str(prepared.athlete_id),
                type(exc).__name__,
            )
            if self.planner_mode == "FIRST_WEEK":
                fallback_plan = _build_first_week_fallback(prepared)
                outcome = validate_first_week_plan(
                    fallback_plan,
                    readiness=prepared.readiness,
                    baseline=prepared.baseline,
                    availability=prepared.availability,
                    preferences=prepared.preferences,
                    zones=prepared.zones,
                    tiers=prepared.tiers,
                )
                result = await self._persist_generated(
                    prepared=prepared,
                    plan=fallback_plan,
                    validation=_validation_snapshot(
                        "fallback",
                        outcome,
                        generation_source="fallback",
                        fallback_reason="provider_error",
                        fallback_errors=(
                            {
                                "code": "PROVIDER_ERROR",
                                "detail": type(exc).__name__,
                            },
                        ),
                    ),
                    prompt_tokens=None,
                    completion_tokens=None,
                )
                await self._observe_failure(metadata, "provider_or_schema_fallback")
                return result
            await self._record_usage(
                athlete_id=prepared.athlete_id,
                status=LLMUsageStatus.PROVIDER_ERROR,
                prompt_tokens=None,
                completion_tokens=None,
            )
            await self._observe_failure(metadata, "provider_error")
            return WeeklyPlanningResult(kind="unavailable")

        if self.planner_mode == "FIRST_WEEK":
            return await self._finalize_first_week(
                prepared=prepared,
                response=response,
                metadata=metadata,
            )

        try:
            prescription = WeeklyPlanPrescription.model_validate(response.output)
        except ValidationError as error:
            # The reply arrived and was unusable. This is a prompt or schema
            # defect, not an outage, and must not be reported as one.
            logger.error(
                "weekly_plan_response_invalid athlete_id=%s error_count=%s"
                " malformed=%s",
                str(prepared.athlete_id),
                len(error.errors()),
                response.malformed,
            )
            await self._record_usage(
                athlete_id=prepared.athlete_id,
                status=LLMUsageStatus.PROVIDER_ERROR,
                prompt_tokens=response.prompt_tokens,
                completion_tokens=response.completion_tokens,
            )
            await self._observe_failure(
                metadata,
                "structured_output_malformed"
                if response.malformed
                else "schema_validation_failed",
            )
            return WeeklyPlanningResult(kind="unavailable")

        plan = schedule_prescription(prescription, prepared.availability)
        if plan is None:
            plan = build_fallback_week(
                prepared.week_start,
                baseline=prepared.baseline,
                availability=prepared.availability,
                preferences=prepared.preferences,
                disciplines=prepared.target_disciplines,
            )
            validation_kind = "fallback"
        else:
            validation_kind = "clean"
        outcome = validate_plan(
            plan,
            readiness=prepared.readiness,
            baseline=prepared.baseline,
            availability=prepared.availability,
            preferences=prepared.preferences,
        )
        ignorable = {"MONOTONY", "SESSION_COUNT_UNDERSHOOT"}
        if any(item.code not in ignorable for item in outcome.violations):
            plan = build_fallback_week(
                prepared.week_start,
                baseline=prepared.baseline,
                availability=prepared.availability,
                preferences=prepared.preferences,
                disciplines=prepared.target_disciplines,
            )
            outcome = validate_plan(
                plan,
                readiness=prepared.readiness,
                baseline=prepared.baseline,
                availability=prepared.availability,
                preferences=prepared.preferences,
            )
            validation_kind = "fallback"

        result = await self._persist_generated(
            prepared=prepared,
            plan=plan,
            validation=_validation_snapshot(validation_kind, outcome),
            prompt_tokens=response.prompt_tokens,
            completion_tokens=response.completion_tokens,
        )
        await self._observe_completed(metadata, response, "confirmation_required")
        return result

    async def _finalize_first_week(
        self,
        *,
        prepared: _PlanningInput,
        response: StructuredModelResponse,
        metadata: AIWorkflowRunMetadata,
    ) -> WeeklyPlanningResult:
        """Validate an unscheduled probe menu, with bounded LLM repair then fallback."""

        plan: FirstWeekPlan | None = None
        outcome: ValidationOutcome | None = None
        current_response = response
        source: Literal["model", "model_repaired", "fallback"] = "model"
        fallback_reason: str | None = None
        fallback_errors: tuple[dict[str, str], ...] = ()
        for attempt in range(_FIRST_WEEK_REPAIR_ATTEMPTS + 1):
            try:
                prescription = FirstWeekPlanPrescription.model_validate(
                    current_response.output
                )
                plan = make_first_week_plan(prescription)
                outcome = validate_first_week_plan(
                    plan,
                    readiness=prepared.readiness,
                    baseline=prepared.baseline,
                    availability=prepared.availability,
                    preferences=prepared.preferences,
                    zones=prepared.zones,
                    tiers=prepared.tiers,
                )
                fallback_errors = _first_week_validation_errors(outcome)
            except ValidationError as error:
                plan = None
                outcome = None
                fallback_reason = "schema_validation_failed"
                fallback_errors = _first_week_schema_errors(error)

            if plan is not None and outcome is not None and outcome.ok:
                source = "model" if attempt == 0 else "model_repaired"
                break

            if plan is not None and outcome is not None:
                repaired = repair_plan(
                    plan,
                    outcome.violations,
                    baseline=prepared.baseline,
                    availability=prepared.availability,
                )
                assert isinstance(repaired, FirstWeekPlan)
                repaired_outcome = validate_first_week_plan(
                    repaired,
                    readiness=prepared.readiness,
                    baseline=prepared.baseline,
                    availability=prepared.availability,
                    preferences=prepared.preferences,
                    zones=prepared.zones,
                    tiers=prepared.tiers,
                )
                if repaired_outcome.ok:
                    plan, outcome, source = repaired, repaired_outcome, "model_repaired"
                    break
                fallback_errors = _first_week_validation_errors(repaired_outcome)
                fallback_reason = "validation_errors"

            if attempt == _FIRST_WEEK_REPAIR_ATTEMPTS:
                plan = _build_first_week_fallback(prepared)
                outcome = validate_first_week_plan(
                    plan,
                    readiness=prepared.readiness,
                    baseline=prepared.baseline,
                    availability=prepared.availability,
                    preferences=prepared.preferences,
                    zones=prepared.zones,
                    tiers=prepared.tiers,
                )
                source = "fallback"
                fallback_reason = (
                    "repair_exhausted"
                    if fallback_reason != "schema_validation_failed"
                    else fallback_reason
                )
                logger.warning(
                    "first_week_plan_fallback athlete_id=%s reason=%s errors=%s",
                    str(prepared.athlete_id),
                    fallback_reason,
                    fallback_errors,
                )
                break

            errors = list(fallback_errors) or [
                {"code": "SCHEMA_VALIDATION_FAILED", "detail": "invalid menu schema"}
            ]
            repair_message = HumanMessage(
                content=json.dumps(
                    {
                        "repair_request": (
                            "Return a corrected first-week menu only. "
                            "Fix only these validation errors."
                        ),
                        "previous_plan": plan.model_dump(mode="json") if plan else None,
                        "validation_errors": errors,
                    },
                    separators=(",", ":"),
                )
            )
            try:
                current_response = await self._model.ainvoke_structured(
                    step=OnboardingStep.TRAINING_HISTORY_IMPORT,
                    schema=FirstWeekPlanPrescription,
                    messages=[
                        *self._build_planner_messages(prepared.prompt_context),
                        repair_message,
                    ],
                    config=build_langchain_run_config(metadata),
                )
            except Exception as error:
                plan = _build_first_week_fallback(prepared)
                outcome = validate_first_week_plan(
                    plan,
                    readiness=prepared.readiness,
                    baseline=prepared.baseline,
                    availability=prepared.availability,
                    preferences=prepared.preferences,
                    zones=prepared.zones,
                    tiers=prepared.tiers,
                )
                source = "fallback"
                fallback_reason = "repair_provider_error"
                fallback_errors = (
                    {"code": "PROVIDER_ERROR", "detail": type(error).__name__},
                )
                logger.warning(
                    "first_week_plan_fallback athlete_id=%s reason=%s errors=%s",
                    str(prepared.athlete_id),
                    fallback_reason,
                    fallback_errors,
                )
                break

        assert plan is not None and outcome is not None
        result = await self._persist_generated(
            prepared=prepared,
            plan=plan,
            validation=_validation_snapshot(
                "clean" if source == "model" else source,
                outcome,
                generation_source=source,
                fallback_reason=fallback_reason if source == "fallback" else None,
                fallback_errors=fallback_errors if source == "fallback" else (),
            ),
            prompt_tokens=current_response.prompt_tokens,
            completion_tokens=current_response.completion_tokens,
        )
        await self._observe_completed(
            metadata, current_response, "confirmation_required"
        )
        return result

    def _build_planner_messages(self, context: dict[str, object]) -> list[BaseMessage]:
        return (
            build_first_week_planner_messages(context)
            if self.planner_mode == "FIRST_WEEK"
            else build_weekly_planner_messages(context)
        )

    @property
    def _prompt_version(self) -> int:
        return (
            FIRST_WEEK_PLANNER_PROMPT_VERSION
            if self.planner_mode == "FIRST_WEEK"
            else WEEKLY_PLANNER_PROMPT_VERSION
        )

    async def _observe_completed(
        self,
        metadata: AIWorkflowRunMetadata,
        response: object,
        outcome: Literal["confirmation_required", "fallback_required"],
    ) -> None:
        completed_at = datetime.now(UTC)
        await self._observer.on_run_completed(
            AIWorkflowRunResult(
                metadata=metadata,
                outcome=outcome,
                completed_at=completed_at,
                latency_ms=int(
                    (completed_at - metadata.started_at).total_seconds() * 1000
                ),
                prompt_tokens=getattr(response, "prompt_tokens", None),
                completion_tokens=getattr(response, "completion_tokens", None),
            )
        )

    async def _observe_failure(
        self, metadata: AIWorkflowRunMetadata, error_code: str
    ) -> None:
        failed_at = datetime.now(UTC)
        await self._observer.on_run_failed(
            AIWorkflowRunError(
                metadata=metadata,
                failed_at=failed_at,
                latency_ms=int(
                    (failed_at - metadata.started_at).total_seconds() * 1000
                ),
                error_code=error_code,
            )
        )

    async def _prepare(
        self, identity: TelegramIdentity
    ) -> _PlanningInput | WeeklyPlanningResult:
        now = _as_utc(utc_now())
        async with self._session_factory() as session, session.begin():
            user = await UserRepository(session).get_by_telegram_id(
                identity.telegram_user_id
            )
            if user is None:
                return WeeklyPlanningResult(kind="unavailable")
            if not user.timezone:
                return WeeklyPlanningResult(kind="timezone_required")

            week_start = next_week_start(now, user.timezone)
            profiles = ProfileRepository(session)
            await profiles.lock_owner(user_id=user.id)
            goal = await profiles.get_training_goal(user_id=user.id)
            target_contexts = await _planned_contexts(
                catalog=TrainingCatalogRepository(session),
                goal_template_id=(goal.goal_template_id if goal is not None else None),
                supporting_goal_template_id=(
                    goal.supporting_goal_template_id if goal is not None else None
                ),
            )
            disciplines = tuple(
                sorted(
                    {item.discipline for item in target_contexts},
                    key=lambda value: value.value,
                )
            )
            if not disciplines:
                return WeeklyPlanningResult(kind="unavailable")

            self_reported_baseline: AthleteBaselineData | None = None
            saved_baseline = await AthleteBaselineRepository(session).get(
                athlete_id=user.id
            )
            if (
                saved_baseline is not None
                and saved_baseline.goal_signature == _goal_signature(goal)
            ):
                try:
                    self_reported_baseline = AthleteBaselineData.model_validate(
                        saved_baseline.baseline_jsonb
                    )
                except ValidationError:
                    logger.warning(
                        "weekly_plan_self_reported_baseline_invalid athlete_id=%s",
                        str(user.id),
                    )
            if (
                self_reported_baseline is None
                and user.status is UserStatus.ONBOARDING_IN_PROGRESS
            ):
                return WeeklyPlanningResult(kind="baseline_required")
            self_reported_disciplines = _self_reported_disciplines(
                baseline=self_reported_baseline,
                planned=disciplines,
            )

            window_started_at = now - timedelta(days=self._settings.planner_window_days)
            workouts = await FitnessRepository(session).workouts_for_window(
                athlete_id=user.id,
                disciplines=disciplines,
                started_at=window_started_at,
                ended_at=now,
            )
            evidence = tuple(
                _fitness_evidence_for_workout(workout) for workout in workouts
            )
            calculations = {
                discipline: calculate_baseline_window(
                    discipline=discipline,
                    workouts=evidence,
                    window_started_at=window_started_at,
                    window_ended_at=now,
                    calculated_at=now,
                )
                for discipline in disciplines
            }
            readiness = build_plan_readiness(
                week_start=week_start,
                window_started_at=window_started_at,
                window_ended_at=now,
                calculations=calculations,
                self_reported_disciplines=self_reported_disciplines,
            )
            if not readiness.ready:
                return WeeklyPlanningResult(kind="insufficient", readiness=readiness)

            profile = await profiles.get_athlete_profile_context(user_id=user.id)
            availability = _confirmed_availability_model(profile)
            capabilities = await AthleteCapabilityRepository(session).available(
                athlete_id=user.id
            )
            evidence_snapshot = build_evidence_snapshot(
                readiness=readiness,
                calculations=calculations,
                self_reported_baseline=self_reported_baseline,
            )
            preferences = (
                self_reported_baseline.preferences
                if self_reported_baseline is not None
                else None
            )
            zones = resolve_first_week_zones(
                baseline=self_reported_baseline,
                calculations=calculations,
                disciplines=disciplines,
            )
            tiers = resolve_first_week_tiers(
                baseline=self_reported_baseline,
                readiness=readiness,
                disciplines=disciplines,
            )
            prompt_context = {
                "planner_mode": self.planner_mode,
                "week_start": week_start.isoformat(),
                "athlete_profile": {
                    "birth_year": profile.birth_year if profile is not None else None,
                    "sex_category": (
                        profile.gender.value
                        if profile is not None and profile.gender is not None
                        else None
                    ),
                    "weight_kg": profile.weight_kg if profile is not None else None,
                    "height_cm": profile.height_cm if profile is not None else None,
                    "timezone": user.timezone,
                },
                "planned_disciplines": [
                    item.discipline.value for item in target_contexts
                ],
                "confirmed_availability": (
                    availability.model_dump(mode="json")
                    if availability is not None
                    else None
                ),
                "availability_constraints": render_availability_constraints(
                    availability, week_start
                ),
                "health_limitations": (
                    profile.health_limitations_text if profile else None
                ),
                "equipment_and_access": [
                    {
                        "code": capability.code,
                        "display_name": capability.display_name,
                        "kind": capability.kind.value,
                    }
                    for capability in capabilities
                ],
                "recent_evidence": evidence_snapshot["recent_evidence"],
                "self_reported_baseline": _planner_baseline_payload(
                    self_reported_baseline
                ),
                "evidence_state": {
                    row.discipline.value: row.state.value
                    for row in readiness.disciplines
                },
                "preferences": {
                    "coaching_style": (
                        preferences.coaching_style.value
                        if preferences is not None
                        else "NORMAL"
                    ),
                    "desired_weekly_sessions": (
                        {
                            discipline.value: count
                            for discipline, count in (
                                preferences.desired_weekly_sessions.items()
                            )
                        }
                        if preferences is not None
                        else None
                    ),
                    "desired_sessions_fit_availability": (
                        preferences.fits_availability
                        if preferences is not None
                        else None
                    ),
                },
                "per_discipline_target_minutes": {
                    discipline.value: _baseline_target_minutes(
                        self_reported_baseline, discipline
                    )
                    for discipline in disciplines
                },
                "untrained_disciplines": [
                    discipline.value
                    for discipline in disciplines
                    if _is_untrained_discipline(
                        self_reported_baseline, readiness, discipline
                    )
                ],
            }
            if self.planner_mode == "ONGOING":
                prompt_context["goal"] = _ongoing_goal_context(
                    goal=goal,
                    target_contexts=target_contexts,
                    baseline=self_reported_baseline,
                )
            else:
                prompt_context["first_week_baseline_tiers"] = {
                    discipline.value: tier for discipline, tier in tiers.items()
                }
                prompt_context["resolved_intensity_zones"] = {
                    discipline.value: zone.model_dump(mode="json")
                    for discipline, zone in zones.items()
                }
            input_digest = _planning_input_digest(prompt_context=prompt_context)
            existing = await WeeklyTrainingPlanRepository(session).get_for_week(
                athlete_id=user.id, week_start=week_start
            )
            if existing is not None and existing.input_digest == input_digest:
                return WeeklyPlanningResult(
                    kind="existing", plan=_plan_schema(existing)
                )
            return _PlanningInput(
                athlete_id=user.id,
                week_start=week_start,
                readiness=readiness,
                baseline=self_reported_baseline,
                availability=availability,
                preferences=(
                    self_reported_baseline.preferences
                    if self_reported_baseline is not None
                    else None
                ),
                target_disciplines=disciplines,
                prompt_context=prompt_context,
                evidence_snapshot=evidence_snapshot,
                input_digest=input_digest,
                zones=zones,
                tiers=tiers,
            )

    async def _persist_generated(
        self,
        *,
        prepared: _PlanningInput,
        plan: WeeklyPlan | FirstWeekPlan,
        validation: dict[str, object] | None,
        prompt_tokens: int | None,
        completion_tokens: int | None,
    ) -> WeeklyPlanningResult:
        if plan.week_start != prepared.week_start:
            await self._record_usage(
                athlete_id=prepared.athlete_id,
                status=LLMUsageStatus.PROVIDER_ERROR,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )
            return WeeklyPlanningResult(kind="unavailable")
        try:
            async with self._session_factory() as session, session.begin():
                await ProfileRepository(session).lock_owner(user_id=prepared.athlete_id)
                plans = WeeklyTrainingPlanRepository(session)
                existing = await plans.get_for_week(
                    athlete_id=prepared.athlete_id,
                    week_start=prepared.week_start,
                )
                previous_revision = await plans.latest_revision(
                    athlete_id=prepared.athlete_id,
                    week_start=prepared.week_start,
                )
                if existing is not None:
                    if existing.input_digest == prepared.input_digest:
                        await _record_usage_in_session(
                            session=session,
                            athlete_id=prepared.athlete_id,
                            model=self._model.model_name,
                            provider_mode=self._model.provider_mode,
                            status=LLMUsageStatus.SUCCEEDED,
                            prompt_tokens=prompt_tokens,
                            completion_tokens=completion_tokens,
                        )
                        return WeeklyPlanningResult(
                            kind="existing",
                            plan=_plan_schema(existing),
                            generation_source=_stored_generation_source(
                                existing.validation_jsonb
                            ),
                        )
                    await plans.supersede_current(
                        athlete_id=prepared.athlete_id,
                        week_start=prepared.week_start,
                    )
                stored = await plans.create(
                    athlete_id=prepared.athlete_id,
                    week_start=prepared.week_start,
                    plan_jsonb=plan.model_dump(mode="json"),
                    plan_schema_version=4 if isinstance(plan, FirstWeekPlan) else 3,
                    validation_jsonb=validation,
                    evidence_snapshot_jsonb=prepared.evidence_snapshot,
                    input_digest=prepared.input_digest,
                    prompt_version=self._prompt_version,
                    calculation_version=CALCULATION_VERSION,
                    planner_model=self._model.model_name,
                    revision=previous_revision + 1,
                )
                await _record_usage_in_session(
                    session=session,
                    athlete_id=prepared.athlete_id,
                    model=self._model.model_name,
                    provider_mode=self._model.provider_mode,
                    status=LLMUsageStatus.SUCCEEDED,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                )
                return WeeklyPlanningResult(
                    kind="created",
                    plan=_plan_schema(stored),
                    readiness=prepared.readiness,
                    generation_source=_stored_generation_source(validation),
                )
        except IntegrityError:
            # The unique constraint is the final authority for double clicks or
            # separate bot workers. The loser simply reads the winner's plan.
            async with self._session_factory() as session:
                existing = await WeeklyTrainingPlanRepository(session).get_for_week(
                    athlete_id=prepared.athlete_id,
                    week_start=prepared.week_start,
                )
                if existing is not None:
                    return WeeklyPlanningResult(
                        kind="existing",
                        plan=_plan_schema(existing),
                        generation_source=_stored_generation_source(
                            existing.validation_jsonb
                        ),
                    )
            return WeeklyPlanningResult(kind="unavailable")

    async def _record_usage(
        self,
        *,
        athlete_id: uuid.UUID,
        status: LLMUsageStatus,
        prompt_tokens: int | None,
        completion_tokens: int | None,
    ) -> None:
        async with self._session_factory() as session, session.begin():
            await _record_usage_in_session(
                session=session,
                athlete_id=athlete_id,
                model=self._model.model_name,
                provider_mode=self._model.provider_mode,
                status=status,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )


async def _record_usage_in_session(
    *,
    session: AsyncSession,
    athlete_id: uuid.UUID,
    model: str,
    provider_mode: ProviderMode,
    status: LLMUsageStatus,
    prompt_tokens: int | None,
    completion_tokens: int | None,
) -> None:
    await LLMUsageRepository(session).record(
        user_id=athlete_id,
        onboarding_step=OnboardingStep.TRAINING_HISTORY_IMPORT,
        feature=_PLANNER_FEATURE,
        provider_mode=provider_mode,
        model=model,
        status=status,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )


async def _planned_contexts(
    *,
    catalog: TrainingCatalogRepository,
    goal_template_id: uuid.UUID | None,
    supporting_goal_template_id: uuid.UUID | None,
) -> tuple[_TargetContext, ...]:
    """Disciplines the coach plans: the primary target plus any support.

    A supporting goal previously reached the baseline service but never the
    planner, so an athlete could hold strength maintenance, accumulate fitness
    numbers for it, and never receive a strength session.
    """

    expected_role_by_goal_id: dict[uuid.UUID, GoalContextRole] = {}
    if goal_template_id is not None:
        primary = await catalog.active_goal_by_id(goal_template_id=goal_template_id)
        if primary is not None and primary.kind is GoalTemplateKind.PRIMARY:
            expected_role_by_goal_id[primary.id] = GoalContextRole.TARGET
    if supporting_goal_template_id is not None:
        supporting = await catalog.active_goal_by_id(
            goal_template_id=supporting_goal_template_id
        )
        if supporting is not None and supporting.kind is GoalTemplateKind.SUPPORTING:
            expected_role_by_goal_id[supporting.id] = GoalContextRole.SUPPORTING
    if not expected_role_by_goal_id:
        return ()

    rows = await catalog.contexts_for_goals(
        goal_template_ids=expected_role_by_goal_id.keys()
    )
    return tuple(
        _TargetContext(
            code=context.code,
            display_name=context.display_name,
            discipline=context.discipline,
            role=relation.role,
        )
        for relation, context in rows
        if expected_role_by_goal_id.get(relation.goal_template_id) is relation.role
    )


def _build_first_week_fallback(prepared: _PlanningInput) -> FirstWeekPlan:
    """Return a baseline-scaled, explicitly degraded menu when provider output fails."""

    availability_names = {
        Discipline.RUNNING: "running",
        Discipline.CYCLING: "cycling",
        Discipline.SWIMMING: "swimming",
        Discipline.STRENGTH: "strength_training",
    }
    sessions: list[dict[str, object]] = []
    for discipline in prepared.target_disciplines:
        baseline = {
            Discipline.RUNNING: prepared.baseline.running
            if prepared.baseline
            else None,
            Discipline.CYCLING: prepared.baseline.cycling
            if prepared.baseline
            else None,
            Discipline.SWIMMING: prepared.baseline.swimming
            if prepared.baseline
            else None,
        }.get(discipline)
        stated_sessions = getattr(baseline, "typical_weekly_sessions", 0)
        zero_baseline = (
            discipline
            in {
                Discipline.RUNNING,
                Discipline.CYCLING,
                Discipline.SWIMMING,
            }
            and stated_sessions == 0
            and getattr(baseline, "typical_weekly_duration_minutes", 0) == 0
        )
        if zero_baseline:
            continue
        requested = (
            prepared.preferences.desired_weekly_sessions.get(discipline)
            if prepared.preferences is not None
            else None
        )
        desired_count = requested if requested is not None and requested > 0 else None
        if discipline is Discipline.STRENGTH:
            count = desired_count if desired_count is not None else 1
        else:
            count = min(
                desired_count if desired_count is not None else max(1, stated_sessions),
                max(1, stated_sessions),
            )
        allowed_name = availability_names.get(discipline)
        max_window = (
            max(
                (
                    window.duration_minutes
                    for details in prepared.availability.days.values()
                    if details.available and allowed_name in details.disciplines
                    for window in details.time_windows
                ),
                default=0,
            )
            if prepared.availability is not None
            else 60
        )
        if max_window < 5:
            continue
        total_minutes = getattr(baseline, "typical_weekly_duration_minutes", 0)
        menu_minutes = min(total_minutes or 30, max_window * count)
        tier = prepared.tiers.get(discipline, "UNPREPARED")
        for index in range(count):
            remaining_count = count - index
            duration = min(
                max_window,
                max(5, menu_minutes // remaining_count),
            )
            menu_minutes -= duration
            intensity, purpose, objective, execution = _fallback_session_shape(
                discipline=discipline,
                index=index,
                tier=tier,
                zone=prepared.zones.get(discipline),
            )
            rpe_range = intensity["rpe_range"]
            assert isinstance(rpe_range, list) and isinstance(rpe_range[1], int)
            targets: dict[str, int] = {"duration_minutes": duration}
            if discipline is not Discipline.STRENGTH:
                targets["rpe"] = rpe_range[1]
            sessions.append(
                {
                    "discipline": discipline,
                    "purpose": purpose,
                    "intensity": intensity,
                    "objective": objective,
                    "targets": targets,
                    "execution": execution,
                }
            )
    if not sessions:
        discipline = prepared.target_disciplines[0]
        fallback_targets: dict[str, int] = {"duration_minutes": 30}
        if discipline is not Discipline.STRENGTH:
            fallback_targets["rpe"] = 3
        sessions.append(
            {
                "discipline": discipline,
                "purpose": "Build gentle familiarity with the discipline.",
                "intensity": {
                    "metric": "RPE",
                    "target_range": [2, 3],
                    "rpe_range": [2, 3],
                    "guidance": "Easy, conversational effort guided by feel.",
                },
                "objective": "Practice comfortably and record how it felt.",
                "targets": fallback_targets,
                "execution": (
                    "Keep this introductory session easy and stop if symptoms worsen."
                ),
            }
        )
    return make_first_week_plan(
        FirstWeekPlanPrescription.model_validate(
            {"week_start": prepared.week_start, "sessions": sessions}
        )
    )


def _fallback_session_shape(
    *,
    discipline: Discipline,
    index: int,
    tier: BaselineTier,
    zone: ResolvedIntensityZones | None,
) -> tuple[dict[str, object], str, str, str]:
    """Create distinct, conservative roles without fabricating a threshold."""

    if (
        zone is not None
        and zone.mode == "NUMERIC"
        and tier in {"DEVELOPING", "TRAINED", "WELL_TRAINED"}
        and index == 1
        and zone.moderate is not None
    ):
        return (
            {
                "metric": zone.metric,
                "target_range": list(zone.moderate),
                "rpe_range": [5, 6],
                "guidance": (
                    f"Controlled tempo inside the resolved {zone.metric} range; "
                    "finish with reserve."
                ),
            },
            "Characterize controlled tempo.",
            "Record how a sustained, controlled tempo feels today.",
            (
                "Warm up easily, hold the controlled tempo with relaxed form, "
                "then cool down."
            ),
        )
    if zone is not None and zone.mode == "NUMERIC" and zone.easy is not None:
        purpose, objective, execution = _easy_fallback_role(index)
        return (
            {
                "metric": zone.metric,
                "target_range": list(zone.easy),
                "rpe_range": [2, 4],
                "guidance": f"Stay in the resolved easy {zone.metric} range.",
            },
            purpose,
            objective,
            execution,
        )
    purpose, objective, execution = _easy_fallback_role(index)
    return (
        {
            "metric": "RPE",
            "target_range": [2, 3],
            "rpe_range": [2, 3],
            "guidance": "Easy, conversational effort; use feel rather than a test.",
        },
        purpose,
        objective,
        execution,
    )


def _easy_fallback_role(index: int) -> tuple[str, str, str]:
    roles = (
        (
            "Establish aerobic baseline.",
            "Complete relaxed aerobic work and record how it feels.",
            "Keep breathing comfortable and finish with plenty in reserve.",
        ),
        (
            "Practice relaxed movement economy.",
            "Notice cadence, form, and breathing at an easy effort.",
            "Stay conversational and use smooth, repeatable movement.",
        ),
        (
            "Build low-stress consistency.",
            "Finish an easy session feeling able to do more.",
            "Keep the effort relaxed and stop if symptoms or pain worsen.",
        ),
    )
    purpose, objective, execution = roles[index % len(roles)]
    if index < len(roles):
        return purpose, objective, execution
    return (
        f"{purpose} Session variation {index + 1}.",
        f"{objective} This is variation {index + 1}.",
        execution,
    )


def _plan_schema(plan: WeeklyTrainingPlan) -> WeeklyPlan | FirstWeekPlan:
    if plan.plan_schema_version >= 4 and "plan_kind" in plan.plan_jsonb:
        return FirstWeekPlan.model_validate(plan.plan_jsonb)
    if plan.plan_schema_version < 3:
        return WeeklyPlan.model_validate(_upgrade_v1_payload(plan.plan_jsonb))
    return WeeklyPlan.model_validate(plan.plan_jsonb)


def _upgrade_v1_payload(payload: dict[str, object]) -> dict[str, object]:
    """Adapt legacy persisted plans without making the current schema permissive."""

    upgraded = cast(dict[str, object], json.loads(json.dumps(payload)))
    raw_days = upgraded.get("days")
    if not isinstance(raw_days, list):
        return upgraded
    for day in raw_days:
        if not isinstance(day, dict):
            continue
        sessions = day.get("sessions")
        if not isinstance(sessions, list):
            continue
        for session in sessions:
            if not isinstance(session, dict):
                continue
            duration = session.pop("duration_minutes", None)
            structure = session.pop("structure", None)
            legacy_intensity = session.get("intensity")
            if not isinstance(session.get("targets"), dict):
                session["targets"] = {"duration_minutes": duration}
            if not isinstance(session.get("execution"), str):
                session["execution"] = structure or "Follow the prescribed effort."
            session["purpose"] = session.get(
                "objective", "Build consistent, manageable training."
            )
            session["intensity"] = _legacy_intensity_target(legacy_intensity)
    return upgraded


def _legacy_intensity_target(value: object) -> dict[str, object]:
    rpe_range = {
        "EASY": [2, 3],
        "MODERATE": [4, 6],
        "HARD": [7, 9],
    }.get(value if isinstance(value, str) else "", [2, 3])
    return {
        "metric": "RPE",
        "target_range": rpe_range,
        "rpe_range": rpe_range,
        "guidance": "Legacy intensity converted to an RPE range.",
    }


def _goal_signature(goal: object | None) -> str:
    if goal is None:
        return ""
    return "|".join(
        str(value)
        for value in (
            getattr(goal, "goal_template_id", None),
            getattr(goal, "supporting_goal_template_id", None),
        )
        if value is not None
    )


def _self_reported_disciplines(
    *,
    baseline: AthleteBaselineData | None,
    planned: tuple[Discipline, ...],
) -> frozenset[Discipline]:
    if baseline is None:
        return frozenset()
    available = {
        Discipline.RUNNING: baseline.running,
        Discipline.CYCLING: baseline.cycling,
        Discipline.SWIMMING: baseline.swimming,
    }
    return frozenset(
        discipline for discipline in planned if available.get(discipline) is not None
    )


def next_week_start(now: datetime, timezone: str | None) -> date:
    """Return the Monday strictly after the athlete's local date."""

    instant = _as_utc(now)
    try:
        local = instant.astimezone(ZoneInfo(timezone)) if timezone else instant
    except ZoneInfoNotFoundError:
        local = instant
    today = local.date()
    return today + timedelta(days=7 - today.weekday())


def _zone_or_utc(timezone: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(timezone) if timezone else ZoneInfo("UTC")
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class FirstWeekPlanner(WeeklyPlanningService):
    """Probe-first planner that deliberately omits event-target machinery."""

    planner_mode: Literal["FIRST_WEEK"] = "FIRST_WEEK"


class OngoingWeeklyPlanner(WeeklyPlanningService):
    """Goal-directed planner for weeks after the initial probe week."""

    planner_mode: Literal["ONGOING"] = "ONGOING"
