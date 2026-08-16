"""Persist imported workout calories in canonical detail rows.

Revision ID: 0025_persist_workout_calories
Revises: 0024_training_history_import
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0025_persist_workout_calories"
down_revision: str | None = "0024_training_history_import"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DETAIL_TABLES = (
    "running_workout_details",
    "cycling_workout_details",
    "hiking_workout_details",
    "swimming_workout_details",
    "strength_workout_details",
    "other_workout_details",
)


def upgrade() -> None:
    for table in _DETAIL_TABLES:
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("calories_kcal", sa.Float(), nullable=True))
            batch.create_check_constraint(
                op.f(f"ck_{table}_calories_nonnegative"),
                "calories_kcal IS NULL OR calories_kcal >= 0",
            )


def downgrade() -> None:
    for table in reversed(_DETAIL_TABLES):
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(
                op.f(f"ck_{table}_calories_nonnegative"),
                type_="check",
            )
            batch.drop_column("calories_kcal")
