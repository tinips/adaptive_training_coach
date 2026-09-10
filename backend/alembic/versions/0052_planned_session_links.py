"""Add planned_session_links: the athlete-confirmed workout-to-session link.

Revision ID: 0052_planned_session_links
Revises: 0051_cycling_power
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0052_planned_session_links"
down_revision: str | None = "0051_cycling_power"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "planned_session_links",
        sa.Column("athlete_id", sa.Uuid(), nullable=False),
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("plan_session_id", sa.Uuid(), nullable=False),
        sa.Column("workout_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["athlete_id"],
            ["users.id"],
            name=op.f("fk_planned_session_links_athlete_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["plan_id"],
            ["weekly_training_plans.id"],
            name=op.f("fk_planned_session_links_plan_id_weekly_training_plans"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workout_id"],
            ["workouts.id"],
            name=op.f("fk_planned_session_links_workout_id_workouts"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_planned_session_links")),
        sa.UniqueConstraint(
            "plan_id",
            "plan_session_id",
            name="uq_planned_session_links_plan_session",
        ),
        sa.UniqueConstraint(
            "workout_id",
            name="uq_planned_session_links_workout",
        ),
    )


def downgrade() -> None:
    raise NotImplementedError(
        "Athlete-confirmed workout links are not destructively downgraded."
    )
