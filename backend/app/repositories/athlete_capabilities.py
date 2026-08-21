"""Ownership-scoped current athlete capability state."""

from __future__ import annotations

import uuid
from collections.abc import Collection

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AthleteCapability, Capability, User
from app.domain.enums import AthleteCapabilityStatus, CatalogItemStatus
from app.repositories.errors import OwnedRecordNotFoundError


class AthleteCapabilityRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def states(
        self, *, athlete_id: uuid.UUID
    ) -> dict[uuid.UUID, AthleteCapabilityStatus]:
        await self._require_athlete(athlete_id)
        rows = await self._session.execute(
            select(AthleteCapability.capability_id, AthleteCapability.status).where(
                AthleteCapability.athlete_id == athlete_id
            )
        )
        return {capability_id: status for capability_id, status in rows.tuples()}

    async def available(self, *, athlete_id: uuid.UUID) -> tuple[Capability, ...]:
        await self._require_athlete(athlete_id)
        rows = await self._session.scalars(
            select(Capability)
            .join(
                AthleteCapability,
                AthleteCapability.capability_id == Capability.id,
            )
            .where(
                AthleteCapability.athlete_id == athlete_id,
                AthleteCapability.status == AthleteCapabilityStatus.AVAILABLE,
                Capability.status == CatalogItemStatus.ACTIVE,
            )
            .order_by(Capability.kind, Capability.display_name)
        )
        return tuple(rows)

    async def replace_reviewed(
        self,
        *,
        athlete_id: uuid.UUID,
        reviewed_ids: Collection[uuid.UUID],
        available_ids: Collection[uuid.UUID],
    ) -> None:
        await self._require_athlete(athlete_id)
        reviewed = set(reviewed_ids)
        available = set(available_ids)
        if not available.issubset(reviewed):
            raise ValueError("available capabilities are outside the review")
        existing_rows = await self._session.scalars(
            select(AthleteCapability).where(
                AthleteCapability.athlete_id == athlete_id,
                AthleteCapability.capability_id.in_(tuple(reviewed)),
            )
        )
        existing = {row.capability_id: row for row in existing_rows}
        for capability_id in reviewed:
            status = (
                AthleteCapabilityStatus.AVAILABLE
                if capability_id in available
                else AthleteCapabilityStatus.UNAVAILABLE
            )
            row = existing.get(capability_id)
            if row is None:
                self._session.add(
                    AthleteCapability(
                        athlete_id=athlete_id,
                        capability_id=capability_id,
                        status=status,
                    )
                )
            else:
                row.status = status
        await self._session.flush()

    async def clear_for_athlete(self, *, athlete_id: uuid.UUID) -> None:
        """Remove every equipment/access answer for one athlete."""

        await self._require_athlete(athlete_id)
        await self._session.execute(
            delete(AthleteCapability).where(AthleteCapability.athlete_id == athlete_id)
        )
        await self._session.flush()

    async def _require_athlete(self, athlete_id: uuid.UUID) -> None:
        exists = await self._session.scalar(
            select(User.id).where(User.id == athlete_id)
        )
        if exists is None:
            raise OwnedRecordNotFoundError("athlete not found")
