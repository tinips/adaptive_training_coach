"""Represent explicitly confirmed conversational training goals.

Revision ID: 0007_conversational_goal
Revises: 0006_exact_workout_identity
Create Date: 2026-08-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_conversational_goal"
down_revision: str | None = "0006_exact_workout_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add only the canonical fields missing from the existing goal table."""

    with op.batch_alter_table("training_goals") as batch_op:
        batch_op.add_column(sa.Column("main_goal", sa.String(500), nullable=True))
        batch_op.add_column(
            sa.Column("target_outcome", sa.String(500), nullable=True)
        )
        batch_op.add_column(
            sa.Column("secondary_priority", sa.String(500), nullable=True)
        )
        batch_op.add_column(
            sa.Column("original_description", sa.Text(), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "status",
                sa.String(16),
                server_default="CONFIRMED",
                nullable=False,
            )
        )
        batch_op.create_check_constraint(
            "ck_training_goals_training_goal_status",
            "status IN ('CONFIRMED')",
        )


def downgrade() -> None:
    """Remove the conversational representation and retain legacy goal fields."""

    with op.batch_alter_table("training_goals") as batch_op:
        batch_op.drop_constraint(
            "ck_training_goals_training_goal_status",
            type_="check",
        )
        batch_op.drop_column("status")
        batch_op.drop_column("original_description")
        batch_op.drop_column("secondary_priority")
        batch_op.drop_column("target_outcome")
        batch_op.drop_column("main_goal")
