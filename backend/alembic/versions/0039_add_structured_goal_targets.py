"""Store athlete-defined event targets as structured goal data."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0039_structured_goal_targets"
down_revision: str | None = "0038_self_reported_baselines"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "training_goals",
        sa.Column("target_distance_km", sa.Float(), nullable=True),
    )
    op.add_column(
        "training_goals",
        sa.Column("target_elevation_m", sa.Float(), nullable=True),
    )
    op.add_column(
        "training_goals",
        sa.Column("target_pace_seconds_per_km", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("training_goals", "target_pace_seconds_per_km")
    op.drop_column("training_goals", "target_elevation_m")
    op.drop_column("training_goals", "target_distance_km")
