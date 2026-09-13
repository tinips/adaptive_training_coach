"""Retire swimming goggles from the selectable capability catalog.

Revision ID: 0054_retire_swimming_goggles
Revises: 0053_evaluation_outcomes
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0054_retire_swimming_goggles"
down_revision: str | None = "0053_evaluation_outcomes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Remove the retired seed capability and its dependent selections."""

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
        sa.column("capability_id", sa.Uuid()),
    )
    athlete_capabilities = sa.table(
        "athlete_capabilities",
        sa.column("capability_id", sa.Uuid()),
    )
    row = bind.execute(
        sa.select(
            capabilities.c.id,
            capabilities.c.source,
            capabilities.c.definition_version,
        ).where(capabilities.c.code == "goggles")
    ).one_or_none()
    if row is None:
        return
    if row.source != "SEEDED" or row.definition_version != 1:
        raise RuntimeError("refusing to delete non-seed capability: goggles")

    bind.execute(requirements.delete().where(requirements.c.capability_id == row.id))
    bind.execute(
        athlete_capabilities.delete().where(
            athlete_capabilities.c.capability_id == row.id
        )
    )
    bind.execute(capabilities.delete().where(capabilities.c.id == row.id))


def downgrade() -> None:
    raise NotImplementedError(
        "0054 deleted athlete goggles selections that cannot be reconstructed"
    )
