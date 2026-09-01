"""Remove the retired iPhone companion credential storage.

Revision ID: 0045_remove_mobile_sync
Revises: 0044_remove_target_outcome
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0045_remove_mobile_sync"
down_revision: str | None = "0044_remove_target_outcome"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_table("mobile_sync_credentials")


def downgrade() -> None:
    raise NotImplementedError("The retired iPhone credential store is not restored.")
