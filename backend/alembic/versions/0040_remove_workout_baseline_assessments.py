"""Remove retired workout-derived baseline assessments."""

from __future__ import annotations

from alembic import op

revision = "0041_remove_baseline"
down_revision = "0040_structured_goals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("athlete_baseline_assessments")


def downgrade() -> None:
    raise NotImplementedError("Workout-derived baseline assessments were retired")
