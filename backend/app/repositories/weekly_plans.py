"""Ownership-scoped persistence for immutable weekly training plans."""

from __future__ import annotations

import uuid
from datetime import date
from typing import cast

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utc_now
from app.db.models import WeeklyTrainingPlan


class WeeklyTrainingPlanRepository:
    """Read the current immutable plan and retain superseded revisions."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_for_week(
        self, *, athlete_id: uuid.UUID, week_start: date
    ) -> WeeklyTrainingPlan | None:
        return cast(
            WeeklyTrainingPlan | None,
            await self._session.scalar(
                select(WeeklyTrainingPlan).where(
                    WeeklyTrainingPlan.athlete_id == athlete_id,
                    WeeklyTrainingPlan.week_start == week_start,
                    WeeklyTrainingPlan.superseded_at.is_(None),
                )
                .order_by(WeeklyTrainingPlan.revision.desc())
            ),
        )

    async def create(
        self,
        *,
        athlete_id: uuid.UUID,
        week_start: date,
        plan_jsonb: dict[str, object],
        evidence_snapshot_jsonb: dict[str, object],
        input_digest: str,
        prompt_version: int,
        calculation_version: int,
        planner_model: str | None,
        revision: int,
    ) -> WeeklyTrainingPlan:
        plan = WeeklyTrainingPlan(
            athlete_id=athlete_id,
            week_start=week_start,
            revision=revision,
            plan_jsonb=plan_jsonb,
            evidence_snapshot_jsonb=evidence_snapshot_jsonb,
            input_digest=input_digest,
            prompt_version=prompt_version,
            calculation_version=calculation_version,
            planner_model=planner_model,
        )
        self._session.add(plan)
        await self._session.flush()
        return plan

    async def supersede_current(
        self, *, athlete_id: uuid.UUID, week_start: date
    ) -> WeeklyTrainingPlan | None:
        plan = await self.get_for_week(athlete_id=athlete_id, week_start=week_start)
        if plan is None:
            return None
        await self._session.execute(
            update(WeeklyTrainingPlan)
            .where(WeeklyTrainingPlan.id == plan.id)
            .values(superseded_at=utc_now())
        )
        await self._session.flush()
        return plan
