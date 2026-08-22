"""Generate and persist one evidence-gated plan for the following week."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.base import utc_now
from app.db.models import AthleteBaselineAssessment, WeeklyTrainingPlan
from app.domain.enums import (
    Discipline,
    GoalContextRole,
    GoalTemplateKind,
    LLMUsageStatus,
    OnboardingStep,
)
from app.integrations.llm.models import StructuredOnboardingModel
from app.observability.protocol import ProviderMode
from app.repositories.athlete_capabilities import AthleteCapabilityRepository
from app.repositories.fitness import FitnessRepository
from app.repositories.llm_usage import LLMUsageRepository
from app.repositories.profiles import ProfileRepository
from app.repositories.training_catalog import TrainingCatalogRepository
from app.repositories.users import UserRepository
from app.repositories.weekly_plans import WeeklyTrainingPlanRepository
from app.schemas.common import TelegramIdentity
from app.schemas.weekly_plans import PlanReadiness, WeeklyPlan
from app.services.fitness.calculator import (
    CALCULATION_VERSION,
    calculate_baseline_window,
)
from app.services.fitness.service import (
    BaselineAssessmentService,
    _fitness_evidence_for_workout,
)
from app.services.weekly_planning.evidence import (
    build_evidence_snapshot,
    build_plan_readiness,
    evidence_input_digest,
)
from app.workflows.prompts.weekly_planning import (
    WEEKLY_PLANNER_PROMPT_VERSION,
    build_weekly_planner_messages,
)

_PLANNER_FEATURE = "WEEKLY_PLAN"


@dataclass(frozen=True, slots=True)
class WeeklyPlanningResult:
    """Safe service outcome consumed by the deterministic Telegram layer."""

    kind: Literal["created", "existing", "insufficient", "unavailable"]
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
            plan = WeeklyPlan.model_validate(response.output)
        except Exception:  # Provider adapters may surface vendor-specific errors.
            await self._record_usage(
                athlete_id=prepared.athlete_id,
                status=LLMUsageStatus.PROVIDER_ERROR,
                prompt_tokens=None,
                completion_tokens=None,
            )
            return WeeklyPlanningResult(kind="unavailable")

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

            week_start = next_week_start(now, user.timezone)
            plans = WeeklyTrainingPlanRepository(session)
            existing = await plans.get_for_week(
                athlete_id=user.id,
                week_start=week_start,
            )
            if existing is not None:
                return WeeklyPlanningResult(
                    kind="existing", plan=_plan_schema(existing)
                )

            profiles = ProfileRepository(session)
            await profiles.lock_owner(user_id=user.id)
            goal = await profiles.get_training_goal(user_id=user.id)
            target_contexts = await _primary_target_contexts(
                catalog=TrainingCatalogRepository(session),
                goal_template_id=(goal.goal_template_id if goal is not None else None),
            )
            disciplines = tuple(
                sorted(
                    {item.discipline for item in target_contexts},
                    key=lambda value: value.value,
                )
            )
            if not disciplines:
                return WeeklyPlanningResult(kind="unavailable")

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
            )
            if not readiness.ready:
                return WeeklyPlanningResult(kind="insufficient", readiness=readiness)

            await BaselineAssessmentService(
                settings=self._settings
            ).create_missing_baselines_for_disciplines_in_session(
                session,
                athlete_id=user.id,
                disciplines=disciplines,
                calculated_at=now,
                owner_locked=True,
            )
            repository = FitnessRepository(session)
            baseline_rows: list[AthleteBaselineAssessment] = []
            for discipline in disciplines:
                baseline = await repository.baseline_for_discipline(
                    athlete_id=user.id,
                    discipline=discipline,
                )
                if baseline is not None:
                    baseline_rows.append(baseline)
            baselines = tuple(baseline_rows)
            profile = await profiles.get_athlete_profile_context(user_id=user.id)
            capabilities = await AthleteCapabilityRepository(session).available(
                athlete_id=user.id
            )
            evidence_snapshot = build_evidence_snapshot(
                readiness=readiness,
                calculations=calculations,
                baselines=baselines,
            )
            prompt_context = {
                "week_start": week_start.isoformat(),
                "goal": {
                    "main_goal": goal.main_goal if goal is not None else None,
                    "target_outcome": goal.target_outcome if goal is not None else None,
                    "event_date": (
                        goal.event_date.isoformat()
                        if goal is not None and goal.event_date is not None
                        else None
                    ),
                    "secondary_priority": (
                        goal.secondary_priority if goal is not None else None
                    ),
                    "target_contexts": [
                        {
                            "code": item.code,
                            "display_name": item.display_name,
                            "discipline": item.discipline.value,
                        }
                        for item in target_contexts
                    ],
                },
                # Raw profile text is intentionally prompt-only. It is not put in
                # evidence_snapshot, input_digest, LLMUsage, or log messages.
                "availability": profile.availability_text if profile else None,
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
                "baselines": evidence_snapshot["baselines"],
            }
            return _PlanningInput(
                athlete_id=user.id,
                week_start=week_start,
                readiness=readiness,
                prompt_context=prompt_context,
                evidence_snapshot=evidence_snapshot,
                input_digest=evidence_input_digest(evidence_snapshot),
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
                stored = await plans.create(
                    athlete_id=prepared.athlete_id,
                    week_start=prepared.week_start,
                    plan_jsonb=plan.model_dump(mode="json"),
                    evidence_snapshot_jsonb=prepared.evidence_snapshot,
                    input_digest=prepared.input_digest,
                    prompt_version=WEEKLY_PLANNER_PROMPT_VERSION,
                    calculation_version=CALCULATION_VERSION,
                    planner_model=self._model.model_name,
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
                return WeeklyPlanningResult(kind="created", plan=_plan_schema(stored))
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


async def _primary_target_contexts(
    *,
    catalog: TrainingCatalogRepository,
    goal_template_id: uuid.UUID | None,
) -> tuple[_TargetContext, ...]:
    if goal_template_id is None:
        return ()
    goal = await catalog.active_goal_by_id(goal_template_id=goal_template_id)
    if goal is None or goal.kind is not GoalTemplateKind.PRIMARY:
        return ()
    rows = await catalog.contexts_for_goals(goal_template_ids=(goal.id,))
    return tuple(
        _TargetContext(
            code=context.code,
            display_name=context.display_name,
            discipline=context.discipline,
        )
        for relation, context in rows
        if relation.role is GoalContextRole.TARGET
    )


def _plan_schema(plan: WeeklyTrainingPlan) -> WeeklyPlan:
    return WeeklyPlan.model_validate(plan.plan_jsonb)


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
