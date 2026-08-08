"""Add persistent deterministic post-onboarding settings state.

Revision ID: 0015_profile_settings_session
Revises: 0014_equipment_knowledge
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "0015_profile_settings_session"
down_revision = "0014_equipment_knowledge"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "profile_settings_sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("current_step", sa.String(32), nullable=False, server_default="MENU"),
        sa.Column("pending_answers", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("current_step IN ('MENU','GOAL_MAIN','GOAL_OUTCOME','GOAL_DATE','AVAILABILITY','EQUIPMENT','HEALTH','PERSONAL_MENU','PERSONAL_BIRTH_YEAR','PERSONAL_GENDER','PERSONAL_WEIGHT','PERSONAL_HEIGHT')", name="profile_settings_step"),
        sa.UniqueConstraint("user_id", name="uq_profile_settings_sessions_user_id"),
    )


def downgrade() -> None:
    op.drop_table("profile_settings_sessions")
