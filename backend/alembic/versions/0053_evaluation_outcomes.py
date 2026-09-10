"""Add first_week_evaluation_outcomes: immutable, versioned evaluations.

Revision ID: 0053_first_week_evaluation_outcomes
Revises: 0052_planned_session_links
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0053_evaluation_outcomes"
down_revision: str | None = "0052_planned_session_links"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "first_week_evaluation_outcomes",
        sa.Column("athlete_id", sa.Uuid(), nullable=False),
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("evaluation_revision", sa.Integer(), nullable=False),
        sa.Column("evaluator_version", sa.Integer(), nullable=False),
        sa.Column("payload_jsonb", sa.JSON(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["athlete_id"],
            ["users.id"],
            name=op.f("fk_first_week_evaluation_outcomes_athlete_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["plan_id"],
            ["weekly_training_plans.id"],
            name=op.f(
                "fk_first_week_evaluation_outcomes_plan_id_weekly_training_plans"
            ),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_first_week_evaluation_outcomes")),
        sa.UniqueConstraint(
            "plan_id",
            "evaluation_revision",
            name="uq_first_week_evaluation_outcomes_plan_revision",
        ),
    )


def downgrade() -> None:
    raise NotImplementedError(
        "Persisted evaluation history is not destructively downgraded."
    )
