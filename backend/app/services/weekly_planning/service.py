"""Generate and persist one evidence-gated plan for the following week."""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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
)
from app.integrations.llm.models import StructuredOnboardingModel
from app.observability.protocol import ProviderMode
from app.repositories.athlete_baselines import AthleteBaselineRepository
from app.repositories.athlete_capabilities import AthleteCapabilityRepository
from app.repositories.fitness import FitnessRepository
from app.repositories.llm_usage import LLMUsageRepository
from app.repositories.profiles import ProfileRepository
from app.repositories.training_catalog import TrainingCatalogRepository
from app.repositories.users import UserRepository
from app.repositories.weekly_plans import WeeklyTrainingPlanRepository
from app.schemas.availability import ConfirmedWeeklyAvailability
from app.schemas.baseline import AthleteBaselineData
from app.schemas.common import TelegramIdentity
from app.schemas.weekly_plans import PlanReadiness, WeeklyPlan
from app.services.fitness.calculator import (
    CALCULATION_VERSION,
    calculate_baseline_window,
)
from app.services.fitness.service import _fitness_evidence_for_workout
from app.services.weekly_planning.evidence import (
    build_evidence_snapshot,
    build_plan_readiness,
)
from app.workflows.prompts.weekly_planning import (
    WEEKLY_PLANNER_PROMPT_VERSION,
    build_weekly_planner_messages,
)

_PLANNER_FEATURE = "WEEKLY_PLAN"

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class WeeklyPlanningResult:
    """Safe service outcome consumed by the deterministic Telegram layer."""

    kind: Literal[
        "created",
        "existing",
        "insufficient",
        "unavailable",
        "availability_conflict",
        "timezone_required",
    ]
    plan: WeeklyPlan | None = None
    readiness: PlanReadiness | None = None


@dataclass(frozen=True, slots=True)
class _PlanningInput:
    athlete_id: uuid.UUID
    week_start: date
    readiness: PlanReadiness
    prompt_context: dict[str, object]
    evidence_snapshot: dict[str, object]
    input_digest: str


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


def _planning_input_digest(
    *,
    evidence_snapshot: dict[str, object],
    goal: object | None,
    confirmed_availability: dict[str, object] | None,
) -> str:
    """Digest all structured planner inputs, never raw profile text."""

    raw_event_date = getattr(goal, "event_date", None)
    payload = {
        "evidence": evidence_snapshot,
        "goal": {
            "template_id": str(getattr(goal, "goal_template_id", None)),
            "supporting_template_id": str(
                getattr(goal, "supporting_goal_template_id", None)
            ),
            "event_date": (
                raw_event_date.isoformat() if isinstance(raw_event_date, date) else None
            ),
            "targets": {
                name: getattr(goal, name, None)
                for name in (
                    "target_distance_km",
                    "target_elevation_m",
                    "target_pace_seconds_per_km",
                    "target_swim_pace_seconds_per_100m",
                    "target_average_speed_kph",
                    "target_finish_time_seconds",
                )
            },
        },
        "confirmed_availability": confirmed_availability,
    }
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
            or session.duration_minutes > limit
            for session in plan_day.sessions
        ):
            return False
    return True


class WeeklyPlanningService:
    """Preflight first, invoke the provider without a DB transaction, then save."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        model: StructuredOnboardingModel,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._model = model

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
                WeeklyPlanningResult(kind="existing", plan=_plan_schema(plan))
                if plan is not None
                else WeeklyPlanningResult(kind="unavailable")
            )

    async def generate_next_week(
        self, identity: TelegramIdentity
    ) -> WeeklyPlanningResult:
        """Create a first plan only after deterministic evidence preflight."""

        prepared = await self._prepare(identity)
        if isinstance(prepared, WeeklyPlanningResult):
            return prepared

        try:
            response = await self._model.ainvoke_structured(
                # Existing provider protocol is shared with onboarding. Feature
                # metadata below distinguishes this non-onboarding call safely.
                step=OnboardingStep.TRAINING_HISTORY_IMPORT,
                schema=WeeklyPlan,
                messages=build_weekly_planner_messages(prepared.prompt_context),
                config={"run_name": "weekly_training_plan"},
            )
        except Exception as exc:  # Provider adapters surface vendor-specific errors.
            logger.error(
                "weekly_plan_provider_error athlete_id=%s error_type=%s",
                str(prepared.athlete_id),
                type(exc).__name__,
            )
            await self._record_usage(
                athlete_id=prepared.athlete_id,
                status=LLMUsageStatus.PROVIDER_ERROR,
                prompt_tokens=None,
                completion_tokens=None,
            )
            return WeeklyPlanningResult(kind="unavailable")

        try:
            plan = WeeklyPlan.model_validate(response.output)
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
            return WeeklyPlanningResult(kind="unavailable")

        availability = prepared.prompt_context.get("confirmed_availability")
        if isinstance(availability, dict) and not _plan_fits_availability(
            plan, availability
        ):
            return WeeklyPlanningResult(kind="availability_conflict")

        return await self._persist_generated(
            prepared=prepared,
            plan=plan,
            prompt_tokens=response.prompt_tokens,
            completion_tokens=response.completion_tokens,
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
            capabilities = await AthleteCapabilityRepository(session).available(
                athlete_id=user.id
            )
            evidence_snapshot = build_evidence_snapshot(
                readiness=readiness,
                calculations=calculations,
                self_reported_baseline=self_reported_baseline,
            )
            prompt_context = {
                "week_start": week_start.isoformat(),
                "goal": {
                    "main_goal": goal.main_goal if goal is not None else None,
                    "event_date": (
                        goal.event_date.isoformat()
                        if goal is not None and goal.event_date is not None
                        else None
                    ),
                    "secondary_priority": (
                        goal.secondary_priority if goal is not None else None
                    ),
                    "performance_targets": (
                        {
                            "distance_km": goal.target_distance_km,
                            "elevation_m": goal.target_elevation_m,
                            "running_pace_seconds_per_km": (
                                goal.target_pace_seconds_per_km
                            ),
                            "swim_pace_seconds_per_100m": (
                                goal.target_swim_pace_seconds_per_100m
                            ),
                            "average_speed_kph": goal.target_average_speed_kph,
                            "finish_time_seconds": goal.target_finish_time_seconds,
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
                },
                "confirmed_availability": _confirmed_availability(profile),
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
                "self_reported_baseline": (
                    self_reported_baseline.model_dump(mode="json", exclude_none=True)
                    if self_reported_baseline is not None
                    else None
                ),
                "evidence_state": {
                    row.discipline.value: row.state.value
                    for row in readiness.disciplines
                },
            }
            input_digest = _planning_input_digest(
                evidence_snapshot=evidence_snapshot,
                goal=goal,
                confirmed_availability=_confirmed_availability(profile),
            )
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
                prompt_context=prompt_context,
                evidence_snapshot=evidence_snapshot,
                input_digest=input_digest,
            )

    async def _persist_generated(
        self,
        *,
        prepared: _PlanningInput,
        plan: WeeklyPlan,
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
                            kind="existing", plan=_plan_schema(existing)
                        )
                    previous_revision = existing.revision
                    await plans.supersede_current(
                        athlete_id=prepared.athlete_id,
                        week_start=prepared.week_start,
                    )
                else:
                    previous_revision = 0
                stored = await plans.create(
                    athlete_id=prepared.athlete_id,
                    week_start=prepared.week_start,
                    plan_jsonb=plan.model_dump(mode="json"),
                    evidence_snapshot_jsonb=prepared.evidence_snapshot,
                    input_digest=prepared.input_digest,
                    prompt_version=WEEKLY_PLANNER_PROMPT_VERSION,
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
                        kind="existing", plan=_plan_schema(existing)
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


def _plan_schema(plan: WeeklyTrainingPlan) -> WeeklyPlan:
    return WeeklyPlan.model_validate(plan.plan_jsonb)


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


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
