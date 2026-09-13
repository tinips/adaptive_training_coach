"""Advance in-progress onboarding sessions past the retired timezone step.

Revision ID: 0055_skip_onboarding_timezone
Revises: 0054_retire_swimming_goggles
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0055_skip_onboarding_timezone"
down_revision: str | None = "0054_retire_swimming_goggles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Give paused athletes a usable default and resume their next step."""

    bind = op.get_bind()
    users = sa.table("users", sa.column("id", sa.Uuid()), sa.column("timezone"))
    onboarding = sa.table(
        "onboarding_sessions",
        sa.column("user_id", sa.Uuid()),
        sa.column("current_step"),
    )
    goals = sa.table("training_goals", sa.column("user_id", sa.Uuid()))
    paused_user_ids = sa.select(onboarding.c.user_id).where(
        onboarding.c.current_step == "PROFILE_TIMEZONE_INTAKE"
    )

    bind.execute(
        users.update()
        .where(users.c.id.in_(paused_user_ids), users.c.timezone.is_(None))
        .values(timezone="UTC")
    )
    has_goal = sa.exists(
        sa.select(1).select_from(goals).where(goals.c.user_id == onboarding.c.user_id)
    )
    bind.execute(
        onboarding.update()
        .where(onboarding.c.current_step == "PROFILE_TIMEZONE_INTAKE")
        .values(
            current_step=sa.case((has_goal, "AVAILABILITY_INTAKE"), else_="GOAL_INTAKE")
        )
    )


def downgrade() -> None:
    raise NotImplementedError(
        "0055 advanced in-progress onboarding sessions and cannot infer their "
        "previous timezone choices"
    )
