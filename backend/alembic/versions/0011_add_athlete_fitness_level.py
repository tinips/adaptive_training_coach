"""Add the optional athlete fitness level used by generic modifications.

Revision ID: 0011_add_athlete_fitness_level
Revises: 0010_remove_legacy_goal_fields
Create Date: 2026-08-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_add_athlete_fitness_level"
down_revision: str | None = "0010_remove_legacy_goal_fields"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add a nullable, bounded description without changing existing rows."""

    op.add_column(
        "athlete_profiles",
        sa.Column("fitness_level", sa.String(length=50), nullable=True),
    )


def downgrade() -> None:
    """Remove the optional fitness-level field."""

    op.drop_column("athlete_profiles", "fitness_level")
