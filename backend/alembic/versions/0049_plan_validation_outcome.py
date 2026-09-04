"""Persist weekly-plan validation observations and repair outcomes.

Revision ID: 0049_plan_validation_outcome
Revises: 0048_plan_session_targets
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0049_plan_validation_outcome"
down_revision: str | None = "0048_plan_session_targets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("weekly_training_plans") as batch:
        batch.add_column(sa.Column("validation_jsonb", sa.JSON(), nullable=True))


def downgrade() -> None:
    raise NotImplementedError(
        "Validation history is operational evidence and is retained."
    )
