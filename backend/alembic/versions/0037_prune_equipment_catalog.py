"""Reduce the equipment/facility catalog to the core nine capabilities.

Revision ID: 0037_prune_equipment_catalog
Revises: 0036_prune_non_endurance_goals
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0037_prune_equipment_catalog"
down_revision: str | None = "0036_prune_non_endurance_goals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CAPABILITY_CODES = (
    "trail_running_shoes",
    "sports_watch",
    "helmet",
    "repair_kit",
    "wetsuit",
    "hiking_shoes",
    "hiking_footwear_unspecified",
    "ski_ergometer",
    "sled_push_pull_equipment",
    "burpee_broad_jump_space",
    "rowing_ergometer",
    "farmer_carry_weights",
    "sandbag",
    "wall_ball",
    "obstacle_access",
    "resistance_bands",
    "free_weights",
    "pull_up_bar",
    "bike_access_unspecified",
    "swimming_access_unspecified",
    "home_training_space",
)


def upgrade() -> None:
    bind = op.get_bind()
    capabilities = sa.table(
        "capabilities",
        sa.column("id", sa.Uuid()),
        sa.column("code"),
        sa.column("source"),
        sa.column("definition_version"),
    )
    requirements = sa.table(
        "execution_option_capabilities",
        sa.column("execution_option_id", sa.Uuid()),
        sa.column("capability_id", sa.Uuid()),
    )
    athlete_capabilities = sa.table(
        "athlete_capabilities",
        sa.column("capability_id", sa.Uuid()),
    )

    capability_rows = tuple(
        bind.execute(
            sa.select(
                capabilities.c.id,
                capabilities.c.code,
                capabilities.c.source,
                capabilities.c.definition_version,
            ).where(capabilities.c.code.in_(_CAPABILITY_CODES))
        )
    )
    unexpected_capabilities = [
        row.code
        for row in capability_rows
        if row.source != "SEEDED" or row.definition_version != 1
    ]
    if unexpected_capabilities:
        raise RuntimeError(
            "refusing to delete non-seed capabilities: "
            + ", ".join(sorted(unexpected_capabilities))
        )

    capability_ids = tuple(row.id for row in capability_rows)
    if capability_ids:
        bind.execute(
            requirements.delete().where(
                requirements.c.capability_id.in_(capability_ids)
            )
        )
        bind.execute(
            athlete_capabilities.delete().where(
                athlete_capabilities.c.capability_id.in_(capability_ids)
            )
        )
        bind.execute(capabilities.delete().where(capabilities.c.id.in_(capability_ids)))

    remaining = set(
        bind.execute(
            sa.select(capabilities.c.code).where(
                capabilities.c.code.in_(_CAPABILITY_CODES)
            )
        ).scalars()
    )
    if remaining:
        raise RuntimeError("retired capability cleanup verification failed")


def downgrade() -> None:
    raise RuntimeError(
        "0037 deleted athlete capability selections that cannot be reconstructed; "
        "restore the pre-upgrade database backup instead"
    )
