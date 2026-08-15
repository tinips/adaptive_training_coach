"""Insert the immutable dynamic training catalog into focused SQLite tests."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Capability,
    ContextExecutionOption,
    ExecutionOptionCapability,
    GoalTemplate,
    GoalTemplateContext,
    TrainingContext,
)
from app.domain.enums import (
    CapabilityImportance,
    CapabilityKind,
    CatalogItemSource,
    CatalogItemStatus,
    Discipline,
    ExecutionOptionRole,
    GoalContextRole,
    GoalTemplateKind,
)
from app.training_catalog_seed import (
    CAPABILITIES,
    EXECUTION_OPTIONS,
    GOAL_CONTEXTS,
    GOAL_TEMPLATES,
    OPTION_CAPABILITIES,
    TRAINING_CONTEXTS,
    catalog_id,
)


async def seed_training_catalog(session: AsyncSession) -> None:
    session.add_all(
        GoalTemplate(
            id=catalog_id("goal", code),
            code=code,
            kind=GoalTemplateKind(kind),
            display_name=display_name,
            description=description,
            source=CatalogItemSource.SEEDED,
            status=CatalogItemStatus.ACTIVE,
            definition_version=1,
        )
        for code, kind, display_name, description in GOAL_TEMPLATES
    )
    session.add_all(
        TrainingContext(
            id=catalog_id("context", code),
            code=code,
            discipline=Discipline(discipline),
            display_name=display_name,
            description=description,
            source=CatalogItemSource.SEEDED,
            status=CatalogItemStatus.ACTIVE,
            definition_version=1,
        )
        for code, discipline, display_name, description in TRAINING_CONTEXTS
    )
    session.add_all(
        Capability(
            id=catalog_id("capability", code),
            code=code,
            display_name=display_name,
            kind=CapabilityKind(kind),
            description=description,
            source=CatalogItemSource.SEEDED,
            status=CatalogItemStatus.ACTIVE,
            definition_version=1,
        )
        for code, display_name, kind, description in CAPABILITIES
    )
    await session.flush()
    session.add_all(
        GoalTemplateContext(
            goal_template_id=catalog_id("goal", goal),
            training_context_id=catalog_id("context", context),
            role=GoalContextRole(role),
            priority=priority,
        )
        for goal, context, role, priority in GOAL_CONTEXTS
    )
    session.add_all(
        ContextExecutionOption(
            id=catalog_id("option", f"{target}:{code}"),
            target_context_id=catalog_id("context", target),
            execution_context_id=catalog_id("context", execution),
            code=code,
            display_name=display_name,
            role=ExecutionOptionRole(role),
            priority=priority,
            limitations=list(limitations),
        )
        for target, code, display_name, execution, role, priority, limitations in (
            EXECUTION_OPTIONS
        )
    )
    await session.flush()
    session.add_all(
        ExecutionOptionCapability(
            execution_option_id=catalog_id("option", f"{target}:{option}"),
            capability_id=catalog_id("capability", capability),
            importance=CapabilityImportance(importance),
        )
        for target, option, capability, importance in OPTION_CAPABILITIES
    )
    await session.flush()
