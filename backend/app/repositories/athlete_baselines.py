"""Owner-scoped storage for the current self-reported training baseline."""

from __future__ import annotations

import uuid
from typing import cast

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AthleteSelfReportedBaseline
from app.schemas.baseline import AthleteBaselineData

CURRENT_BASELINE_FORM_VERSION = 2


class AthleteBaselineRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, *, athlete_id: uuid.UUID) -> AthleteSelfReportedBaseline | None:
        return cast(
            AthleteSelfReportedBaseline | None,
            await self._session.scalar(
                select(AthleteSelfReportedBaseline).where(
                    AthleteSelfReportedBaseline.athlete_id == athlete_id
                )
            ),
        )

    async def upsert(
        self,
        *,
        athlete_id: uuid.UUID,
        goal_signature: str,
        baseline: AthleteBaselineData,
    ) -> AthleteSelfReportedBaseline:
        current = await self.get(athlete_id=athlete_id)
        values = cast(
            dict[str, object], baseline.model_dump(mode="json", exclude_none=True)
        )
        if current is None:
            current = AthleteSelfReportedBaseline(
                athlete_id=athlete_id,
                goal_signature=goal_signature,
                form_version=CURRENT_BASELINE_FORM_VERSION,
                baseline_jsonb=values,
            )
            self._session.add(current)
        else:
            current.goal_signature = goal_signature
            current.form_version = CURRENT_BASELINE_FORM_VERSION
            current.baseline_jsonb = values
        await self._session.flush()
        return current

    async def invalidate_for_goal_change(self, *, athlete_id: uuid.UUID) -> None:
        """Remove the goal-scoped baseline before collecting the new one."""

        await self._session.execute(
            delete(AthleteSelfReportedBaseline).where(
                AthleteSelfReportedBaseline.athlete_id == athlete_id
            )
        )
        await self._session.flush()
