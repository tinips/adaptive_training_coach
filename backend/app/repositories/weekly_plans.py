"""Ownership-scoped persistence for immutable weekly training plans."""

from __future__ import annotations

import uuid
from datetime import date
from typing import cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import WeeklyTrainingPlan


class WeeklyTrainingPlanRepository:
    """Read and create at most one published plan per athlete/week."""

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
                )
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
    ) -> WeeklyTrainingPlan:
        plan = WeeklyTrainingPlan(
            athlete_id=athlete_id,
            week_start=week_start,
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
