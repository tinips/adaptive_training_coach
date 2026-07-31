"""Remove persisted heart-rate observations.

Revision ID: 0005_remove_hr_observations
Revises: 0004_discipline_workout_models
Create Date: 2026-07-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_remove_hr_observations"
down_revision: str | Sequence[str] | None = "0004_discipline_workout_models"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

HEART_RATE_QUALITIES = (
    "EXACT_SAMPLE",
    "SHORT_INTERVAL",
    "COARSE_INTERVAL",
    "MANUAL",
    "UNKNOWN",
)


def _heart_rate_quality_enum() -> sa.Enum:
    return sa.Enum(
        *HEART_RATE_QUALITIES,
        name="heart_rate_observation_quality",
        native_enum=False,
        create_constraint=True,
        length=24,
    )


def upgrade() -> None:
    """Drop per-sample persistence; canonical HR remains in workout details."""

    op.drop_table("heart_rate_observations")


def downgrade() -> None:
    """Restore the former empty table shape without reconstructing samples."""

    op.create_table(
        "heart_rate_observations",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("workout_id", sa.Uuid(), nullable=False),
        sa.Column("source_record_key", sa.String(length=64), nullable=False),
        sa.Column("source_name", sa.String(length=255), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("beats_per_minute", sa.Float(), nullable=False),
        sa.Column(
            "temporal_quality",
            _heart_rate_quality_enum(),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_heart_rate_observations_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workout_id"],
            ["workouts.id"],
            name=op.f("fk_heart_rate_observations_workout_id_workouts"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name=op.f("pk_heart_rate_observations"),
        ),
        sa.UniqueConstraint(
            "user_id",
            "source_record_key",
            name="uq_heart_rate_observations_user_source_key",
        ),
    )
    op.create_index(
        "ix_heart_rate_observations_workout_started",
        "heart_rate_observations",
        ["workout_id", "started_at"],
        unique=False,
    )
