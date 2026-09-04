"""Persist one aggregated outcome per completed plan week.

Revision ID: 0050_weekly_plan_outcomes
Revises: 0049_plan_validation_outcome
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0050_weekly_plan_outcomes"
down_revision: str | None = "0049_plan_validation_outcome"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "weekly_plan_outcomes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("athlete_id", sa.Uuid(), nullable=False),
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("comparison_jsonb", sa.JSON(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["athlete_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["plan_id"], ["weekly_training_plans.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "athlete_id", "week_start", name="uq_weekly_plan_outcomes_athlete_week"
        ),
    )


def downgrade() -> None:
    raise NotImplementedError(
        "Persisted outcome history is not destructively downgraded."
    )
