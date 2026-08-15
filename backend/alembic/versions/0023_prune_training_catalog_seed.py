"""Remove retired version-one catalog seed records.

Revision ID: 0023_prune_training_catalog_seed
Revises: 0022_dynamic_training_catalog
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0023_prune_training_catalog_seed"
down_revision: str | None = "0022_dynamic_training_catalog"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CAPABILITY_CODES = (
    "safe_running_route",
    "trail_access",
    "backpack",
    "trekking_poles",
    "bodyweight_space",
    "training_space_unspecified",
)
_BODYWEIGHT_CONTEXT_CODE = "strength_bodyweight"


def upgrade() -> None:
    bind = op.get_bind()
    capabilities = sa.table(
        "capabilities",
        sa.column("id", sa.Uuid()),
        sa.column("code"),
        sa.column("source"),
        sa.column("definition_version"),
    )
    contexts = sa.table(
        "training_contexts",
        sa.column("id", sa.Uuid()),
        sa.column("code"),
        sa.column("source"),
        sa.column("definition_version"),
    )
    options = sa.table(
        "context_execution_options",
        sa.column("id", sa.Uuid()),
        sa.column("target_context_id", sa.Uuid()),
        sa.column("execution_context_id", sa.Uuid()),
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

    context_row = bind.execute(
        sa.select(
            contexts.c.id,
            contexts.c.source,
            contexts.c.definition_version,
        ).where(contexts.c.code == _BODYWEIGHT_CONTEXT_CODE)
    ).one_or_none()
    if context_row is not None and (
        context_row.source != "SEEDED" or context_row.definition_version != 1
    ):
        raise RuntimeError("refusing to delete non-seed strength_bodyweight context")

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

    if context_row is not None:
        option_ids = tuple(
            bind.execute(
                sa.select(options.c.id).where(
                    sa.or_(
                        options.c.target_context_id == context_row.id,
                        options.c.execution_context_id == context_row.id,
                    )
                )
            ).scalars()
        )
        if option_ids:
            bind.execute(
                requirements.delete().where(
                    requirements.c.execution_option_id.in_(option_ids)
                )
            )
            bind.execute(options.delete().where(options.c.id.in_(option_ids)))
        bind.execute(contexts.delete().where(contexts.c.id == context_row.id))

    if capability_ids:
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
    if bind.execute(
        sa.select(contexts.c.id).where(contexts.c.code == _BODYWEIGHT_CONTEXT_CODE)
    ).first():
        raise RuntimeError("retired context cleanup verification failed")


def downgrade() -> None:
    raise RuntimeError(
        "0023 deleted athlete capability selections that cannot be reconstructed; "
        "restore the pre-upgrade database backup instead"
    )
