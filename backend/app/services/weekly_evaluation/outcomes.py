"""Persist a WeeklyEvaluation as an immutable, versioned outcome revision.

docs/decisions/locked.md, "First-week evaluator": "evaluation is a manual,
idempotent action after link review. Later corrections create superseding
immutable evaluation revisions."
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import FirstWeekEvaluationOutcome
from app.repositories.first_week_evaluation_outcomes import (
    FirstWeekEvaluationOutcomeRepository,
)
from app.schemas.weekly_evaluation import WeeklyEvaluation


class OutcomeService:
    def __init__(self, session: AsyncSession) -> None:
        self._outcomes = FirstWeekEvaluationOutcomeRepository(session)

    async def persist_evaluation(
        self, *, athlete_id: uuid.UUID, evaluation: WeeklyEvaluation
    ) -> FirstWeekEvaluationOutcome:
        """Idempotent replay: an unchanged re-evaluation writes no new row.

        A changed result (a correction, a relink, a later re-run against
        updated evidence) always appends a new immutable revision; no row is
        ever mutated or replaced.
        """

        payload = evaluation.model_dump(mode="json")
        latest = await self._outcomes.get_latest(plan_id=evaluation.plan_id)
        if latest is not None and latest.payload_jsonb == payload:
            return latest

        return await self._outcomes.create_next_revision(
            athlete_id=athlete_id,
            plan_id=evaluation.plan_id,
            week_start=evaluation.week_start,
            evaluator_version=evaluation.evaluator_version,
            payload_jsonb=payload,
        )
