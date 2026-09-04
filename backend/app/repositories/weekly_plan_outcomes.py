"""Owner-scoped storage for persisted weekly plan outcomes."""

from __future__ import annotations

import uuid
from datetime import date
from typing import cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utc_now
from app.db.models import WeeklyPlanOutcome


class WeeklyPlanOutcomeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(
        self, *, athlete_id: uuid.UUID, week_start: date
    ) -> WeeklyPlanOutcome | None:
        return cast(
            WeeklyPlanOutcome | None,
            await self._session.scalar(
                select(WeeklyPlanOutcome).where(
                    WeeklyPlanOutcome.athlete_id == athlete_id,
                    WeeklyPlanOutcome.week_start == week_start,
                )
            ),
        )

    async def upsert(
        self,
        *,
        athlete_id: uuid.UUID,
        plan_id: uuid.UUID,
        week_start: date,
        comparison_jsonb: dict[str, object],
    ) -> WeeklyPlanOutcome:
        outcome = await self.get(athlete_id=athlete_id, week_start=week_start)
        if outcome is None:
            outcome = WeeklyPlanOutcome(
                athlete_id=athlete_id,
                plan_id=plan_id,
                week_start=week_start,
                comparison_jsonb=comparison_jsonb,
            )
            self._session.add(outcome)
        else:
            outcome.plan_id = plan_id
            outcome.comparison_jsonb = comparison_jsonb
            outcome.computed_at = utc_now()
        await self._session.flush()
        return outcome
