"""Reads of reusable global training-goal and capability knowledge."""

from __future__ import annotations

import uuid
from collections.abc import Collection
from dataclasses import dataclass
from typing import cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.db.models import (
    Capability,
    ContextExecutionOption,
    ExecutionOptionCapability,
    GoalTemplate,
    GoalTemplateContext,
    TrainingContext,
)
from app.domain.enums import (
    CatalogItemStatus,
    Discipline,
    GoalContextRole,
    GoalTemplateKind,
)


@dataclass(frozen=True, slots=True)
class ExecutionOptionCatalogEntry:
    option: ContextExecutionOption
    target_context: TrainingContext
    execution_context: TrainingContext
    requirements: tuple[tuple[ExecutionOptionCapability, Capability], ...]


class TrainingCatalogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def active_goal_templates(self) -> tuple[GoalTemplate, ...]:
        rows = await self._session.scalars(
            select(GoalTemplate)
            .where(GoalTemplate.status == CatalogItemStatus.ACTIVE)
            .order_by(GoalTemplate.kind, GoalTemplate.code)
        )
        return tuple(rows)

    async def active_goal_by_code(
        self, *, code: str, kind: GoalTemplateKind | None = None
    ) -> GoalTemplate | None:
        statement = select(GoalTemplate).where(
            GoalTemplate.code == code,
            GoalTemplate.status == CatalogItemStatus.ACTIVE,
        )
        if kind is not None:
            statement = statement.where(GoalTemplate.kind == kind)
        return cast(GoalTemplate | None, await self._session.scalar(statement))

    async def active_goal_by_id(
        self, *, goal_template_id: uuid.UUID
    ) -> GoalTemplate | None:
        return cast(
            GoalTemplate | None,
            await self._session.scalar(
                select(GoalTemplate).where(
                    GoalTemplate.id == goal_template_id,
                    GoalTemplate.status == CatalogItemStatus.ACTIVE,
                )
            ),
        )

    async def active_primary_goal_target_disciplines(
        self,
    ) -> tuple[tuple[str, str, frozenset[Discipline]], ...]:
        """Active primary goals as (code, display_name, target disciplines).

        Used to build the deterministic sport/goal selection menu; the caller
        turns these plain rows into `GoalOption` before grouping them, since
        repositories in this codebase do not import from `app.services`.
        """

        rows = await self._session.execute(
            select(
                GoalTemplate.code,
                GoalTemplate.display_name,
                TrainingContext.discipline,
            )
            .join(
                GoalTemplateContext,
                GoalTemplateContext.goal_template_id == GoalTemplate.id,
            )
            .join(
                TrainingContext,
                TrainingContext.id == GoalTemplateContext.training_context_id,
            )
            .where(
                GoalTemplate.kind == GoalTemplateKind.PRIMARY,
                GoalTemplate.status == CatalogItemStatus.ACTIVE,
                GoalTemplateContext.role == GoalContextRole.TARGET,
            )
        )
        by_code: dict[str, tuple[str, set[Discipline]]] = {}
        for code, display_name, discipline in rows:
            entry = by_code.setdefault(code, (display_name, set()))
            entry[1].add(discipline)
        return tuple(
            (code, display_name, frozenset(disciplines))
            for code, (display_name, disciplines) in sorted(by_code.items())
        )

    async def active_contexts(self) -> tuple[TrainingContext, ...]:
        rows = await self._session.scalars(
            select(TrainingContext)
            .where(TrainingContext.status == CatalogItemStatus.ACTIVE)
            .order_by(TrainingContext.code)
        )
        return tuple(rows)

    async def active_capabilities(self) -> tuple[Capability, ...]:
        rows = await self._session.scalars(
            select(Capability)
            .where(Capability.status == CatalogItemStatus.ACTIVE)
            .order_by(Capability.code)
        )
        return tuple(rows)

    async def contexts_for_goals(
        self, *, goal_template_ids: Collection[uuid.UUID]
    ) -> tuple[tuple[GoalTemplateContext, TrainingContext], ...]:
        if not goal_template_ids:
            return ()
        rows = await self._session.execute(
            select(GoalTemplateContext, TrainingContext)
            .join(
                TrainingContext,
                TrainingContext.id == GoalTemplateContext.training_context_id,
            )
            .where(
                GoalTemplateContext.goal_template_id.in_(tuple(goal_template_ids)),
                TrainingContext.status == CatalogItemStatus.ACTIVE,
            )
            .order_by(GoalTemplateContext.priority, TrainingContext.code)
        )
        return tuple(rows.tuples())

    async def execution_options(
        self, *, context_ids: Collection[uuid.UUID]
    ) -> tuple[tuple[ContextExecutionOption, TrainingContext, TrainingContext], ...]:
        if not context_ids:
            return ()
        target = aliased(TrainingContext)
        execution = aliased(TrainingContext)
        rows = await self._session.execute(
            select(ContextExecutionOption, target, execution)
            .join(target, target.id == ContextExecutionOption.target_context_id)
            .join(
                execution, execution.id == ContextExecutionOption.execution_context_id
            )
            .where(
                ContextExecutionOption.target_context_id.in_(tuple(context_ids)),
                target.status == CatalogItemStatus.ACTIVE,
                execution.status == CatalogItemStatus.ACTIVE,
            )
            .order_by(
                target.code,
                ContextExecutionOption.role,
                ContextExecutionOption.priority,
                ContextExecutionOption.code,
            )
        )
        return tuple(rows.tuples())

    async def option_requirements(
        self, *, option_ids: Collection[uuid.UUID]
    ) -> tuple[tuple[ExecutionOptionCapability, Capability], ...]:
        if not option_ids:
            return ()
        rows = await self._session.execute(
            select(ExecutionOptionCapability, Capability)
            .join(Capability, Capability.id == ExecutionOptionCapability.capability_id)
            .where(
                ExecutionOptionCapability.execution_option_id.in_(tuple(option_ids)),
                Capability.status == CatalogItemStatus.ACTIVE,
            )
            .order_by(Capability.display_name)
        )
        return tuple(rows.tuples())

    async def execution_option_catalog(
        self, *, context_ids: Collection[uuid.UUID]
    ) -> tuple[ExecutionOptionCatalogEntry, ...]:
        """Load active execution options together with their requirements."""

        options = await self.execution_options(context_ids=context_ids)
        if not options:
            return ()
        requirements = await self.option_requirements(
            option_ids={option.id for option, _, _ in options}
        )
        requirements_by_option: dict[
            uuid.UUID, list[tuple[ExecutionOptionCapability, Capability]]
        ] = {}
        for requirement, capability in requirements:
            requirements_by_option.setdefault(
                requirement.execution_option_id, []
            ).append((requirement, capability))
        return tuple(
            ExecutionOptionCatalogEntry(
                option=option,
                target_context=target,
                execution_context=execution,
                requirements=tuple(requirements_by_option.get(option.id, ())),
            )
            for option, target, execution in options
        )
