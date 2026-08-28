"""Replace the generic HYROX context with its canonical station graph.

Revision ID: 0026_complete_hyrox_catalog
Revises: 0025_persist_workout_calories
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op
from app.training_catalog_seed import (
    CAPABILITIES,
    EXECUTION_OPTIONS,
    GOAL_CONTEXTS,
    OPTION_CAPABILITIES,
    TRAINING_CONTEXTS,
    catalog_id,
)

revision: str = "0026_complete_hyrox_catalog"
down_revision: str | None = "0025_persist_workout_calories"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_HYROX_STATION_CONTEXT_CODES = {
    "hyrox_ski_erg",
    "hyrox_sled_push_pull",
    "hyrox_burpee_broad_jump",
    "hyrox_row",
    "hyrox_farmer_carry",
    "hyrox_sandbag_lunge",
    "hyrox_wall_balls",
}
_HYROX_CONTEXT_CODES = {"running_road", *_HYROX_STATION_CONTEXT_CODES}
_HYROX_CAPABILITY_CODES = {
    "gym_access",
    "ski_ergometer",
    "sled_push_pull_equipment",
    "burpee_broad_jump_space",
    "rowing_ergometer",
    "farmer_carry_weights",
    "sandbag",
    "wall_ball",
}


def _now() -> datetime:
    return datetime.now(UTC)


def upgrade() -> None:
    bind = op.get_bind()
    now = _now()
    contexts = sa.table(
        "training_contexts",
        sa.column("id", sa.Uuid()),
        sa.column("code", sa.String()),
        sa.column("discipline", sa.String()),
        sa.column("display_name", sa.String()),
        sa.column("description", sa.String()),
        sa.column("source", sa.String()),
        sa.column("status", sa.String()),
        sa.column("definition_version", sa.Integer()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    goals = sa.table(
        "goal_templates",
        sa.column("id", sa.Uuid()),
        sa.column("code", sa.String()),
        sa.column("kind", sa.String()),
        sa.column("display_name", sa.String()),
        sa.column("description", sa.String()),
        sa.column("source", sa.String()),
        sa.column("status", sa.String()),
        sa.column("definition_version", sa.Integer()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    capabilities = sa.table(
        "capabilities",
        sa.column("id", sa.Uuid()),
        sa.column("code", sa.String()),
        sa.column("display_name", sa.String()),
        sa.column("description", sa.String()),
        sa.column("kind", sa.String()),
        sa.column("source", sa.String()),
        sa.column("status", sa.String()),
        sa.column("definition_version", sa.Integer()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    goal_contexts = sa.table(
        "goal_template_contexts",
        sa.column("goal_template_id", sa.Uuid()),
        sa.column("training_context_id", sa.Uuid()),
        sa.column("role", sa.String()),
        sa.column("priority", sa.Integer()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    options = sa.table(
        "context_execution_options",
        sa.column("id", sa.Uuid()),
        sa.column("target_context_id", sa.Uuid()),
        sa.column("execution_context_id", sa.Uuid()),
        sa.column("code", sa.String()),
        sa.column("display_name", sa.String()),
        sa.column("role", sa.String()),
        sa.column("priority", sa.Integer()),
        sa.column("limitations", sa.JSON()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    requirements = sa.table(
        "execution_option_capabilities",
        sa.column("execution_option_id", sa.Uuid()),
        sa.column("capability_id", sa.Uuid()),
        sa.column("importance", sa.String()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )

    hyrox_goal_row = bind.execute(
        sa.select(goals.c.id).where(goals.c.code == "HYROX")
    ).one_or_none()
    hyrox_goal_id = (
        hyrox_goal_row.id if hyrox_goal_row is not None else catalog_id("goal", "HYROX")
    )
    if hyrox_goal_row is None:
        bind.execute(
            goals.insert().values(
                id=hyrox_goal_id,
                code="HYROX",
                kind="PRIMARY",
                display_name="HYROX",
                description=(
                    "HYROX hybrid race combining repeated running with "
                    "functional stations."
                ),
                source="SEEDED",
                status="ACTIVE",
                definition_version=1,
                created_at=now,
                updated_at=now,
            )
        )
    bind.execute(
        goals.update()
        .where(goals.c.id == hyrox_goal_id)
        .values(
            description=(
                "HYROX hybrid race combining repeated running with functional stations."
            ),
            updated_at=now,
        )
    )
    functional_fitness_row = bind.execute(
        sa.select(contexts.c.id).where(contexts.c.code == "functional_fitness")
    ).one_or_none()
    if functional_fitness_row is not None:
        bind.execute(
            goal_contexts.delete().where(
                goal_contexts.c.goal_template_id == hyrox_goal_id,
                goal_contexts.c.training_context_id == functional_fitness_row.id,
            )
        )

    context_rows: dict[str, uuid.UUID] = {
        row.code: row.id
        for row in bind.execute(
            sa.select(contexts.c.code, contexts.c.id).where(
                contexts.c.code.in_(_HYROX_CONTEXT_CODES)
            )
        )
    }
    context_data = {
        code: (discipline, display_name, description)
        for code, discipline, display_name, description in TRAINING_CONTEXTS
        if code in _HYROX_CONTEXT_CODES
    }
    for code, (discipline, display_name, description) in context_data.items():
        context_id = context_rows.get(code, catalog_id("context", code))
        if code not in context_rows:
            bind.execute(
                contexts.insert().values(
                    id=context_id,
                    code=code,
                    discipline=discipline,
                    display_name=display_name,
                    description=description,
                    source="SEEDED",
                    status="ACTIVE",
                    definition_version=1,
                    created_at=now,
                    updated_at=now,
                )
            )
        context_rows[code] = context_id

    capability_rows: dict[str, uuid.UUID] = {
        row.code: row.id
        for row in bind.execute(
            sa.select(capabilities.c.code, capabilities.c.id).where(
                capabilities.c.code.in_(_HYROX_CAPABILITY_CODES)
            )
        )
    }
    capability_data = {
        code: (display_name, kind, description)
        for code, display_name, kind, description in CAPABILITIES
        if code in _HYROX_CAPABILITY_CODES
    }
    for code, (display_name, kind, description) in capability_data.items():
        capability_id = capability_rows.get(code, catalog_id("capability", code))
        if code not in capability_rows:
            bind.execute(
                capabilities.insert().values(
                    id=capability_id,
                    code=code,
                    display_name=display_name,
                    description=description,
                    kind=kind,
                    source="SEEDED",
                    status="ACTIVE",
                    definition_version=1,
                    created_at=now,
                    updated_at=now,
                )
            )
        capability_rows[code] = capability_id

    existing_links = {
        (row.goal_template_id, row.training_context_id)
        for row in bind.execute(
            sa.select(
                goal_contexts.c.goal_template_id,
                goal_contexts.c.training_context_id,
            ).where(goal_contexts.c.goal_template_id == hyrox_goal_id)
        )
    }
    for goal, context, role, priority in GOAL_CONTEXTS:
        if goal != "HYROX" or context not in context_rows:
            continue
        pair = (hyrox_goal_id, context_rows[context])
        if pair in existing_links:
            continue
        bind.execute(
            goal_contexts.insert().values(
                goal_template_id=hyrox_goal_id,
                training_context_id=context_rows[context],
                role=role,
                priority=priority,
                created_at=now,
                updated_at=now,
            )
        )
        existing_links.add(pair)

    known_station_context_ids = tuple(
        context_rows[code]
        for code in _HYROX_STATION_CONTEXT_CODES
        if code in context_rows
    )
    option_rows: dict[tuple[str, str], uuid.UUID] = (
        {
            (row.target_context_id, row.code): row.id
            for row in bind.execute(
                sa.select(
                    options.c.target_context_id,
                    options.c.code,
                    options.c.id,
                ).where(options.c.target_context_id.in_(known_station_context_ids))
            )
        }
        if known_station_context_ids
        else {}
    )
    for (
        target,
        code,
        display_name,
        execution,
        role,
        priority,
        limitations,
    ) in EXECUTION_OPTIONS:
        if (
            target not in _HYROX_STATION_CONTEXT_CODES
            or target not in context_rows
            or execution not in context_rows
        ):
            continue
        key = (context_rows[target], code)
        option_id = option_rows.get(key, catalog_id("option", f"{target}:{code}"))
        if key not in option_rows:
            bind.execute(
                options.insert().values(
                    id=option_id,
                    target_context_id=context_rows[target],
                    execution_context_id=context_rows[execution],
                    code=code,
                    display_name=display_name,
                    role=role,
                    priority=priority,
                    limitations=list(limitations),
                    created_at=now,
                    updated_at=now,
                )
            )
        option_rows[key] = option_id

    known_option_ids = tuple(option_rows.values())
    existing_requirements = (
        {
            (row.execution_option_id, row.capability_id)
            for row in bind.execute(
                sa.select(
                    requirements.c.execution_option_id,
                    requirements.c.capability_id,
                ).where(requirements.c.execution_option_id.in_(known_option_ids))
            )
        }
        if known_option_ids
        else set()
    )
    for target, option, capability, importance in OPTION_CAPABILITIES:
        if (
            target not in _HYROX_STATION_CONTEXT_CODES
            or target not in context_rows
            or (context_rows[target], option) not in option_rows
            or capability not in capability_rows
        ):
            continue
        option_id = option_rows[(context_rows[target], option)]
        pair = (option_id, capability_rows[capability])
        if pair in existing_requirements:
            continue
        bind.execute(
            requirements.insert().values(
                execution_option_id=option_id,
                capability_id=capability_rows[capability],
                importance=importance,
                created_at=now,
                updated_at=now,
            )
        )
        existing_requirements.add(pair)


def downgrade() -> None:
    raise RuntimeError(
        "0026 changes the canonical HYROX definition; restore the pre-upgrade "
        "catalog from a backup instead of reconstructing it automatically"
    )
