"""Remove the redundant training-goal outcome text.

Revision ID: 0044_remove_target_outcome
Revises: 0043_cleanup_dead_storage
"""

from __future__ import annotations

from alembic import op

revision = "0044_remove_target_outcome"
down_revision = "0043_cleanup_dead_storage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("training_goals", "target_outcome")


def downgrade() -> None:
    raise NotImplementedError("The redundant target outcome text was retired")
