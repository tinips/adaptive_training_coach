"""Add immutable persisted weekly training plans and planner usage metadata.

Revision ID: 0029_weekly_training_plans
Revises: 0028_athlete_fitness_projections
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0029_weekly_training_plans"
down_revision: str | None = "0028_athlete_fitness_projections"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    # These two tables only existed in a local, pre-simplification prototype.
    # Fresh databases never create them, while upgrading an existing developer
    # database must remove them so its physical schema matches this milestone.
    bind = op.get_bind()
    for stale_table in (
        "athlete_fitness_snapshots",
        "athlete_current_fitness",
    ):
        if bind.dialect.has_table(bind, stale_table):
            op.drop_table(stale_table)
    op.create_table(
        "weekly_training_plans",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("athlete_id", sa.Uuid(), nullable=False),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("plan_jsonb", _JSON, nullable=False),
        sa.Column("evidence_snapshot_jsonb", _JSON, nullable=False),
        sa.Column("input_digest", sa.CHAR(length=64), nullable=False),
        sa.Column("prompt_version", sa.Integer(), nullable=False),
        sa.Column("calculation_version", sa.Integer(), nullable=False),
        sa.Column("planner_model", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "calculation_version > 0",
            name=op.f("ck_weekly_training_plans_calculation_version_positive"),
        ),
        sa.CheckConstraint(
            "prompt_version > 0",
            name=op.f("ck_weekly_training_plans_prompt_version_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["athlete_id"],
            ["users.id"],
            name=op.f("fk_weekly_training_plans_athlete_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_weekly_training_plans")),
        sa.UniqueConstraint(
            "athlete_id",
            "week_start",
            name="uq_weekly_training_plans_athlete_week_start",
        ),
    )
    with op.batch_alter_table("llm_usage") as batch:
        batch.add_column(sa.Column("feature", sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("llm_usage") as batch:
        batch.drop_column("feature")
    op.drop_table("weekly_training_plans")
