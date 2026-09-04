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
                select(WeeklyTrainingPlan)
                .where(
                    WeeklyTrainingPlan.athlete_id == athlete_id,
                    WeeklyTrainingPlan.week_start == week_start,
                    WeeklyTrainingPlan.superseded_at.is_(None),
                )
                .order_by(WeeklyTrainingPlan.revision.desc())
            ),
        )

    async def latest_revision(self, *, athlete_id: uuid.UUID, week_start: date) -> int:
        """Return the latest revision, including plans discarded by the athlete."""

        revision = await self._session.scalar(
            select(WeeklyTrainingPlan.revision)
            .where(
                WeeklyTrainingPlan.athlete_id == athlete_id,
                WeeklyTrainingPlan.week_start == week_start,
            )
            .order_by(WeeklyTrainingPlan.revision.desc())
            .limit(1)
        )
        return int(revision) if revision is not None else 0

    async def create(
        self,
        *,
        athlete_id: uuid.UUID,
        week_start: date,
        plan_jsonb: dict[str, object],
        plan_schema_version: int,
        validation_jsonb: dict[str, object] | None,
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
            plan_schema_version=plan_schema_version,
            validation_jsonb=validation_jsonb,
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

    async def supersede_current_and_next_week(
        self, *, athlete_id: uuid.UUID, current_week_start: date
    ) -> None:
        """Hide plans made for an obsolete goal without deleting revisions."""

        next_week_start = date.fromordinal(current_week_start.toordinal() + 7)
        await self._session.execute(
            update(WeeklyTrainingPlan)
            .where(
                WeeklyTrainingPlan.athlete_id == athlete_id,
                WeeklyTrainingPlan.week_start.in_(
                    (current_week_start, next_week_start)
                ),
                WeeklyTrainingPlan.superseded_at.is_(None),
            )
            .values(superseded_at=utc_now())
        )
        await self._session.flush()
