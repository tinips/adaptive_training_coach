"""Add cycling power fields for smart-trainer/static-bike actuals.

Revision ID: 0051_cycling_power
Revises: 0050_weekly_plan_outcomes
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0051_cycling_power"
down_revision: str | None = "0050_weekly_plan_outcomes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("cycling_workout_details") as batch:
        batch.add_column(sa.Column("average_power_watts", sa.Float(), nullable=True))
        batch.add_column(sa.Column("max_power_watts", sa.Float(), nullable=True))
        batch.create_check_constraint(
            "average_power_nonnegative",
            "average_power_watts IS NULL OR average_power_watts >= 0",
        )
        batch.create_check_constraint(
            "max_power_nonnegative",
            "max_power_watts IS NULL OR max_power_watts >= 0",
        )


def downgrade() -> None:
    with op.batch_alter_table("cycling_workout_details") as batch:
        batch.drop_constraint("max_power_nonnegative", type_="check")
        batch.drop_constraint("average_power_nonnegative", type_="check")
        batch.drop_column("max_power_watts")
        batch.drop_column("average_power_watts")
