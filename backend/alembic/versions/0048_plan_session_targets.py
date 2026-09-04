"""Version weekly plans with structured session targets.

Revision ID: 0048_plan_session_targets
Revises: 0047_add_training_preferences
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0048_plan_session_targets"
down_revision: str | None = "0047_add_training_preferences"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("weekly_training_plans") as batch:
        batch.add_column(
            sa.Column(
                "plan_schema_version",
                nullable=False,
                server_default="1",
                type_=sa.Integer(),
            )
        )
    with op.batch_alter_table("weekly_training_plans") as batch:
        batch.alter_column("plan_schema_version", server_default=None)


def downgrade() -> None:
    raise NotImplementedError("Historical plan payloads are versioned, not rewritten.")
