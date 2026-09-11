"""Transactional delivery orchestration for the first-week evaluator."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.repositories.activities import TrainingActivityRepository
from app.repositories.errors import OwnedRecordNotFoundError
from app.repositories.planned_session_links import PlannedSessionLinkRepository
from app.repositories.profiles import ProfileRepository
from app.repositories.users import UserRepository
from app.repositories.weekly_plans import WeeklyTrainingPlanRepository
from app.schemas.weekly_evaluation import WeeklyEvaluation
from app.schemas.weekly_plans import FirstWeekPlan
from app.services.athlete_zones import resolve_reference_hr_zones
from app.services.weekly_evaluation.aggregation import aggregate_week
from app.services.weekly_evaluation.comparison import compare_session
from app.services.weekly_evaluation.errors import PlanNotLinkableError
from app.services.weekly_evaluation.evidence import build_evaluator_workout_evidence
from app.services.weekly_evaluation.outcomes import OutcomeService
from app.services.weekly_planning.constants import FIRST_WEEK_PLAN_SCHEMA_VERSION


class FirstWeekEvaluationUnavailableError(RuntimeError):
    """Raised when a requested plan cannot be evaluated by this v1 flow."""


@dataclass(frozen=True, slots=True)
class FirstWeekEvaluationResult:
    """A persisted deterministic result suitable for Telegram rendering."""

    outcome_id: uuid.UUID
    evaluation: WeeklyEvaluation


class FirstWeekEvaluationService:
    """Load explicit links, evaluate them deterministically, and persist once."""

    def __init__(self, *, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def evaluate_plan(
        self, *, telegram_user_id: int, plan_id: uuid.UUID
    ) -> FirstWeekEvaluationResult:
        """Evaluate one owned current first-week plan, without any inference."""

        async with self._session_factory() as session, session.begin():
            user = await UserRepository(session).get_by_telegram_id(telegram_user_id)
            if user is None:
                raise FirstWeekEvaluationUnavailableError("athlete not recognized")

            plan = await WeeklyTrainingPlanRepository(session).get_by_id(
                plan_id=plan_id
            )
            if plan is None or plan.athlete_id != user.id:
                raise OwnedRecordNotFoundError("plan not found")
            if (
                plan.superseded_at is not None
                or plan.plan_schema_version < FIRST_WEEK_PLAN_SCHEMA_VERSION
            ):
                raise PlanNotLinkableError("plan is not an active first-week plan")
            try:
                first_week_plan = FirstWeekPlan.model_validate(plan.plan_jsonb)
            except ValueError as error:
                raise FirstWeekEvaluationUnavailableError(
                    "plan is not a valid first-week menu"
                ) from error

            profile = await ProfileRepository(session).get_athlete_profile(
                user_id=user.id
            )
            if profile is None or profile.birth_year is None:
                raise FirstWeekEvaluationUnavailableError(
                    "birth year is required to evaluate effort"
                )
            reference_hr_zones = resolve_reference_hr_zones(
                birth_year=profile.birth_year,
                current_year=datetime.now(UTC).year,
            )

            links = await PlannedSessionLinkRepository(session).get_for_plan(
                plan_id=plan.id
            )
            linked_workout_by_session = {
                link.plan_session_id: link.workout_id for link in links
            }
            workouts = TrainingActivityRepository(session)
            insights = []
            for planned_session in first_week_plan.sessions:
                workout_id = linked_workout_by_session.get(planned_session.id)
                actual = None
                if workout_id is not None:
                    workout = await workouts.get_owned(
                        user_id=user.id, workout_id=workout_id
                    )
                    if workout is not None:
                        actual = await build_evaluator_workout_evidence(
                            session, workout
                        )
                insights.append(
                    compare_session(
                        planned=planned_session,
                        actual=actual,
                        reference_hr_zones=reference_hr_zones,
                    )
                )

            evaluation = aggregate_week(
                plan_id=plan.id,
                plan_revision=plan.revision,
                week_start=plan.week_start,
                timezone=user.timezone,
                insights=tuple(insights),
            )
            outcome = await OutcomeService(session).persist_evaluation(
                athlete_id=user.id,
                evaluation=evaluation,
            )
        return FirstWeekEvaluationResult(
            outcome_id=outcome.id,
            evaluation=evaluation,
        )


__all__ = [
    "FirstWeekEvaluationResult",
    "FirstWeekEvaluationService",
    "FirstWeekEvaluationUnavailableError",
]
