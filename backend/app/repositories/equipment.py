"""Ownership-scoped equipment catalog and athlete-access persistence."""

from __future__ import annotations

import uuid
from collections.abc import Collection

from sqlalchemy import case, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AthleteEquipment, EquipmentCatalog, User
from app.domain.enums import Discipline
from app.repositories.errors import OwnedRecordNotFoundError


class EquipmentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def catalog_for_disciplines(
        self, *, disciplines: Collection[Discipline]
    ) -> tuple[EquipmentCatalog, ...]:
        if not disciplines:
            return ()
        rows = await self._session.scalars(
            select(EquipmentCatalog)
            .where(EquipmentCatalog.discipline.in_(tuple(disciplines)))
            .order_by(
                EquipmentCatalog.discipline,
                case(
                    (EquipmentCatalog.importance == "essential", 0),
                    (EquipmentCatalog.importance == "recommended", 1),
                    else_=2,
                ),
                EquipmentCatalog.display_name,
            )
        )
        return tuple(rows)

    async def selected_catalog(
        self, *, athlete_id: uuid.UUID
    ) -> tuple[EquipmentCatalog, ...]:
        await self._require_athlete(athlete_id)
        rows = await self._session.scalars(
            select(EquipmentCatalog)
            .join(
                AthleteEquipment,
                AthleteEquipment.equipment_id == EquipmentCatalog.id,
            )
            .where(AthleteEquipment.athlete_id == athlete_id)
            .order_by(EquipmentCatalog.discipline, EquipmentCatalog.display_name)
        )
        return tuple(rows)

    async def replace_for_disciplines(
        self,
        *,
        athlete_id: uuid.UUID,
        disciplines: Collection[Discipline],
        equipment_ids: Collection[uuid.UUID],
    ) -> tuple[EquipmentCatalog, ...]:
        """Replace only the reviewed disciplines, preserving unrelated access."""

        await self._require_athlete(athlete_id)
        catalog = await self.catalog_for_disciplines(disciplines=disciplines)
        allowed = {item.id: item for item in catalog}
        selected = set(equipment_ids)
        if not selected.issubset(allowed):
            raise ValueError("equipment selection is outside the reviewed catalog")

        scoped_ids = tuple(allowed)
        if scoped_ids:
            await self._session.execute(
                delete(AthleteEquipment).where(
                    AthleteEquipment.athlete_id == athlete_id,
                    AthleteEquipment.equipment_id.in_(scoped_ids),
                )
            )
        for equipment_id in sorted(selected, key=str):
            self._session.add(
                AthleteEquipment(
                    athlete_id=athlete_id,
                    equipment_id=equipment_id,
                )
            )
        await self._session.flush()
        return tuple(allowed[item] for item in sorted(selected, key=str))

    async def _require_athlete(self, athlete_id: uuid.UUID) -> None:
        if (
            await self._session.scalar(select(User.id).where(User.id == athlete_id))
            is None
        ):
            raise OwnedRecordNotFoundError("athlete not found")
