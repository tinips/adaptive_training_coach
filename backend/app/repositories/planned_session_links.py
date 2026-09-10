"""Persistence for the athlete-confirmed workout-to-planned-session link.

docs/decisions/locked.md, "First-week evaluator": a workout links to at most
one planned session, and a planned session has at most one primary workout.
Relinking before evaluation updates the existing row in place.
"""

from __future__ import annotations

import uuid
from typing import cast

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PlannedSessionLink
from app.services.weekly_evaluation.errors import WorkoutAlreadyLinkedError


class PlannedSessionLinkRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_for_plan(self, *, plan_id: uuid.UUID) -> list[PlannedSessionLink]:
        result = await self._session.scalars(
            select(PlannedSessionLink).where(PlannedSessionLink.plan_id == plan_id)
        )
        return list(result)

    async def get_by_plan_session(
        self, *, plan_id: uuid.UUID, plan_session_id: uuid.UUID
    ) -> PlannedSessionLink | None:
        return cast(
            "PlannedSessionLink | None",
            await self._session.scalar(
                select(PlannedSessionLink).where(
                    PlannedSessionLink.plan_id == plan_id,
                    PlannedSessionLink.plan_session_id == plan_session_id,
                )
            ),
        )

    async def get_by_workout(
        self, *, workout_id: uuid.UUID
    ) -> PlannedSessionLink | None:
        return cast(
            "PlannedSessionLink | None",
            await self._session.scalar(
                select(PlannedSessionLink).where(
                    PlannedSessionLink.workout_id == workout_id
                )
            ),
        )

    async def upsert_link(
        self,
        *,
        athlete_id: uuid.UUID,
        plan_id: uuid.UUID,
        plan_session_id: uuid.UUID,
        workout_id: uuid.UUID,
    ) -> PlannedSessionLink:
        """Create the session's link, or relink it to a different workout.

        Raises WorkoutAlreadyLinkedError when `workout_id` is already the
        primary link for a *different* session -- a workout links to at most
        one planned session, so that workout must be unlinked there first.
        The same call repeated with identical arguments is a no-op (already
        linked exactly this way), making double-click retries idempotent.
        """

        existing_for_workout = await self.get_by_workout(workout_id=workout_id)
        if existing_for_workout is not None and (
            existing_for_workout.plan_id != plan_id
            or existing_for_workout.plan_session_id != plan_session_id
        ):
            raise WorkoutAlreadyLinkedError(
                "workout is already linked to a different planned session"
            )

        existing_for_session = await self.get_by_plan_session(
            plan_id=plan_id, plan_session_id=plan_session_id
        )
        if existing_for_session is not None:
            existing_for_session.workout_id = workout_id
            existing_for_session.athlete_id = athlete_id
            await self._session.flush()
            return existing_for_session

        link = PlannedSessionLink(
            athlete_id=athlete_id,
            plan_id=plan_id,
            plan_session_id=plan_session_id,
            workout_id=workout_id,
        )
        self._session.add(link)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            # A concurrent request won the race between our read above and
            # this write (double-click / two bot workers); the unique
            # constraints are the final authority.
            raise WorkoutAlreadyLinkedError(
                "workout or session was linked concurrently"
            ) from exc
        return link
