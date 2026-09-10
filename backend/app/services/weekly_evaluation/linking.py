"""Athlete-explicit workout-to-planned-session linking.

docs/briefs/backlog/first-week-evaluator.md, "Durable links": "Every write
must prove: the plan belongs to the athlete; the session reference resolves
inside that exact plan revision; the workout belongs to the same athlete;
the workout satisfies the approved eligibility rules; and the operation
respects link uniqueness/relinking rules."
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PlannedSessionLink
from app.repositories.activities import TrainingActivityRepository
from app.repositories.errors import OwnedRecordNotFoundError
from app.repositories.planned_session_links import PlannedSessionLinkRepository
from app.repositories.users import UserRepository
from app.repositories.weekly_plans import WeeklyTrainingPlanRepository
from app.schemas.weekly_plans import FirstWeekPlan
from app.services.weekly_evaluation.eligibility import is_within_plan_week
from app.services.weekly_evaluation.errors import (
    PlannedSessionNotFoundError,
    PlanNotLinkableError,
    WorkoutNotEligibleError,
)
from app.services.weekly_planning.constants import FIRST_WEEK_PLAN_SCHEMA_VERSION


class LinkingService:
    """Owner-scoped, eligibility-checked athlete confirmation of one link."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._plans = WeeklyTrainingPlanRepository(session)
        self._workouts = TrainingActivityRepository(session)
        self._users = UserRepository(session)
        self._links = PlannedSessionLinkRepository(session)

    async def link_workout_to_session(
        self,
        *,
        athlete_id: uuid.UUID,
        plan_id: uuid.UUID,
        plan_session_id: uuid.UUID,
        workout_id: uuid.UUID,
    ) -> PlannedSessionLink:
        plan = await self._plans.get_by_id(plan_id=plan_id)
        if plan is None or plan.athlete_id != athlete_id:
            raise OwnedRecordNotFoundError("plan not found")
        if plan.plan_schema_version < FIRST_WEEK_PLAN_SCHEMA_VERSION:
            raise PlanNotLinkableError("plan revision predates stable session identity")
        if plan.superseded_at is not None:
            raise PlanNotLinkableError("plan revision has been superseded")

        first_week_plan = FirstWeekPlan.model_validate(plan.plan_jsonb)
        if not any(
            session.id == plan_session_id for session in first_week_plan.sessions
        ):
            raise PlannedSessionNotFoundError(
                "session reference does not resolve inside this plan revision"
            )

        workout = await self._workouts.get_owned(
            user_id=athlete_id, workout_id=workout_id
        )
        if workout is None:
            raise OwnedRecordNotFoundError("workout not found")

        athlete = await self._users.require_by_id(athlete_id)
        if not is_within_plan_week(
            started_at=workout.started_at,
            timezone=athlete.timezone,
            week_start=plan.week_start,
        ):
            raise WorkoutNotEligibleError(
                "workout falls outside the plan's athlete-local week"
            )

        return await self._links.upsert_link(
            athlete_id=athlete_id,
            plan_id=plan_id,
            plan_session_id=plan_session_id,
            workout_id=workout_id,
        )
