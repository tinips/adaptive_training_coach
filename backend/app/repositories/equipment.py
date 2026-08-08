"""Ownership-scoped equipment knowledge and athlete-status persistence."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AthleteGoalEquipmentInterpretation,
    AthleteGoalEquipmentStatus,
    EquipmentGoalType,
    EquipmentResource,
    EquipmentResourceRequirement,
    EquipmentResourceSubstitution,
    EquipmentStageWindow,
)
from app.domain.enums import AthleteEquipmentStatus


class EquipmentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def goal_types(self) -> tuple[EquipmentGoalType, ...]:
        rows = await self._session.scalars(
            select(EquipmentGoalType).order_by(EquipmentGoalType.match_priority)
        )
        return tuple(rows)

    async def requirements(
        self, *, goal_type_id: uuid.UUID
    ) -> tuple[tuple[EquipmentResourceRequirement, EquipmentResource], ...]:
        rows = await self._session.execute(
            select(EquipmentResourceRequirement, EquipmentResource)
            .join(
                EquipmentResource,
                EquipmentResource.id == EquipmentResourceRequirement.resource_id,
            )
            .where(EquipmentResourceRequirement.goal_type_id == goal_type_id)
            .order_by(EquipmentResourceRequirement.display_order)
        )
        return tuple(
            cast(tuple[EquipmentResourceRequirement, EquipmentResource], row)
            for row in rows.all()
        )

    async def stage_windows(
        self, *, goal_type_id: uuid.UUID
    ) -> tuple[EquipmentStageWindow, ...]:
        rows = await self._session.scalars(
            select(EquipmentStageWindow).where(
                EquipmentStageWindow.goal_type_id == goal_type_id
            )
        )
        return tuple(rows)

    async def substitutions(
        self, *, required_resource_ids: tuple[uuid.UUID, ...]
    ) -> tuple[tuple[EquipmentResourceSubstitution, EquipmentResource], ...]:
        if not required_resource_ids:
            return ()
        rows = await self._session.execute(
            select(EquipmentResourceSubstitution, EquipmentResource)
            .join(
                EquipmentResource,
                EquipmentResource.id
                == EquipmentResourceSubstitution.substitute_resource_id,
            )
            .where(
                EquipmentResourceSubstitution.required_resource_id.in_(
                    required_resource_ids
                )
            )
        )
        return tuple(
            cast(tuple[EquipmentResourceSubstitution, EquipmentResource], row)
            for row in rows.all()
        )

    async def replace_statuses(
        self,
        *,
        user_id: uuid.UUID,
        training_goal_id: uuid.UUID,
        goal_revision: int,
        statuses: Mapping[uuid.UUID, AthleteEquipmentStatus],
    ) -> None:
        existing = await self._session.scalars(
            select(AthleteGoalEquipmentStatus).where(
                AthleteGoalEquipmentStatus.user_id == user_id,
                AthleteGoalEquipmentStatus.training_goal_id == training_goal_id,
                AthleteGoalEquipmentStatus.goal_revision == goal_revision,
            )
        )
        by_resource = {row.resource_id: row for row in existing}
        for resource_id, status in statuses.items():
            row = by_resource.get(resource_id)
            if row is None:
                self._session.add(
                    AthleteGoalEquipmentStatus(
                        user_id=user_id,
                        training_goal_id=training_goal_id,
                        goal_revision=goal_revision,
                        resource_id=resource_id,
                        status=status,
                    )
                )
            else:
                row.status = status
        await self._session.flush()

    async def statuses(
        self, *, user_id: uuid.UUID, training_goal_id: uuid.UUID, goal_revision: int
    ) -> tuple[AthleteGoalEquipmentStatus, ...]:
        rows = await self._session.scalars(
            select(AthleteGoalEquipmentStatus).where(
                AthleteGoalEquipmentStatus.user_id == user_id,
                AthleteGoalEquipmentStatus.training_goal_id == training_goal_id,
                AthleteGoalEquipmentStatus.goal_revision == goal_revision,
            )
        )
        return tuple(rows)

    async def save_interpretation(
        self,
        *,
        user_id: uuid.UUID,
        training_goal_id: uuid.UUID,
        goal_revision: int,
        interpretation: dict[str, object],
    ) -> None:
        row = await self._session.scalar(
            select(AthleteGoalEquipmentInterpretation).where(
                AthleteGoalEquipmentInterpretation.user_id == user_id,
                AthleteGoalEquipmentInterpretation.training_goal_id == training_goal_id,
                AthleteGoalEquipmentInterpretation.goal_revision == goal_revision,
            )
        )
        if row is None:
            self._session.add(
                AthleteGoalEquipmentInterpretation(
                    user_id=user_id,
                    training_goal_id=training_goal_id,
                    goal_revision=goal_revision,
                    interpretation=interpretation,
                )
            )
        else:
            row.interpretation = interpretation
        await self._session.flush()
