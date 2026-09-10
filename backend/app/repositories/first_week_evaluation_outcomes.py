"""Append-only persistence for immutable, versioned first-week evaluations."""

from __future__ import annotations

import uuid
from datetime import date
from typing import cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import FirstWeekEvaluationOutcome


class FirstWeekEvaluationOutcomeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_latest(
        self, *, plan_id: uuid.UUID
    ) -> FirstWeekEvaluationOutcome | None:
        return cast(
            "FirstWeekEvaluationOutcome | None",
            await self._session.scalar(
                select(FirstWeekEvaluationOutcome)
                .where(FirstWeekEvaluationOutcome.plan_id == plan_id)
                .order_by(FirstWeekEvaluationOutcome.evaluation_revision.desc())
            ),
        )

    async def get_all(self, *, plan_id: uuid.UUID) -> list[FirstWeekEvaluationOutcome]:
        result = await self._session.scalars(
            select(FirstWeekEvaluationOutcome)
            .where(FirstWeekEvaluationOutcome.plan_id == plan_id)
            .order_by(FirstWeekEvaluationOutcome.evaluation_revision.asc())
        )
        return list(result)

    async def create_next_revision(
        self,
        *,
        athlete_id: uuid.UUID,
        plan_id: uuid.UUID,
        week_start: date,
        evaluator_version: int,
        payload_jsonb: dict[str, object],
    ) -> FirstWeekEvaluationOutcome:
        latest = await self.get_latest(plan_id=plan_id)
        next_revision = (latest.evaluation_revision + 1) if latest else 1
        outcome = FirstWeekEvaluationOutcome(
            athlete_id=athlete_id,
            plan_id=plan_id,
            week_start=week_start,
            evaluation_revision=next_revision,
            evaluator_version=evaluator_version,
            payload_jsonb=payload_jsonb,
        )
        self._session.add(outcome)
        await self._session.flush()
        return outcome
