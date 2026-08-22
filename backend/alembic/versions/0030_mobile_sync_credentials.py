"""Add revocable credentials for the iPhone HealthKit proof of concept.

Revision ID: 0030_mobile_sync_credentials
Revises: 0029_weekly_training_plans
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0030_mobile_sync_credentials"
down_revision: str | None = "0029_weekly_training_plans"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mobile_sync_credentials",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("pairing_code_hash", sa.CHAR(length=64), nullable=True),
        sa.Column("pairing_code_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("device_token_hash", sa.CHAR(length=64), nullable=True),
        sa.Column("installation_id", sa.String(length=36), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "pairing_code_hash IS NULL OR pairing_code_expires_at IS NOT NULL",
            name=op.f("ck_mobile_sync_credentials_pairing_code_requires_expiry"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_mobile_sync_credentials_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mobile_sync_credentials")),
        sa.UniqueConstraint(
            "user_id",
            name="uq_mobile_sync_credentials_user_id",
        ),
        sa.UniqueConstraint(
            "pairing_code_hash",
            name="uq_mobile_sync_credentials_pairing_code_hash",
        ),
    )
    op.create_index(
        "ix_mobile_sync_credentials_device_token_hash",
        "mobile_sync_credentials",
        ["device_token_hash"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_mobile_sync_credentials_device_token_hash",
        table_name="mobile_sync_credentials",
    )
    op.drop_table("mobile_sync_credentials")
