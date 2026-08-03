"""Remove the unused athlete fitness-level field.

Revision ID: 0012_remove_fitness_level
Revises: 0011_add_athlete_fitness_level
Create Date: 2026-08-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_remove_fitness_level"
down_revision: str | None = "0011_add_athlete_fitness_level"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Remove the unsupported athlete profile field."""

    op.drop_column("athlete_profiles", "fitness_level")


def downgrade() -> None:
    """Restore the nullable column for migration reversibility."""

    op.add_column(
        "athlete_profiles",
        sa.Column("fitness_level", sa.String(length=50), nullable=True),
    )
