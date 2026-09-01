"""Add profile-settings states and revision-aware weekly plans.

Revision ID: 0042_profile_plan_revisions
Revises: 0041_remove_baseline
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0042_profile_plan_revisions"
down_revision: str | None = "0041_remove_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PROFILE_STEPS = (
    "'MENU','GOAL_MENU','GOAL_MAIN','GOAL_OUTCOME','GOAL_METRICS','GOAL_DATE',"
    "'GOAL_SECONDARY','GOAL_CLASSIFICATION_CONFIRM','AVAILABILITY',"
    "'AVAILABILITY_REVIEW','EQUIPMENT','HEALTH','PERSONAL_MENU',"
    "'PERSONAL_BIRTH_YEAR','PERSONAL_GENDER','PERSONAL_WEIGHT','PERSONAL_HEIGHT'"
)


def _replace_profile_settings_step_check(values: str) -> None:
    with op.batch_alter_table("profile_settings_sessions") as batch:
        batch.drop_constraint(
            op.f("ck_profile_settings_sessions_profile_settings_step"),
            type_="check",
        )
        batch.create_check_constraint("profile_settings_step", f"current_step IN ({values})")


def upgrade() -> None:
    _replace_profile_settings_step_check(_PROFILE_STEPS)
    with op.batch_alter_table("weekly_training_plans") as batch:
        batch.add_column(
            sa.Column("revision", sa.Integer(), nullable=False, server_default="1")
        )
        batch.add_column(sa.Column("superseded_at", sa.DateTime(timezone=True)))
        batch.drop_constraint(
            op.f("uq_weekly_training_plans_athlete_week_start"), type_="unique"
        )
        batch.create_unique_constraint(
            "uq_weekly_training_plans_athlete_week_revision",
            ["athlete_id", "week_start", "revision"],
        )


def downgrade() -> None:
    op.execute(
        "UPDATE profile_settings_sessions SET current_step = 'MENU' "
        "WHERE current_step IN ('GOAL_METRICS', 'AVAILABILITY_REVIEW')"
    )
    old_steps = _PROFILE_STEPS.replace(",'GOAL_METRICS'", "").replace(",'AVAILABILITY_REVIEW'", "")
    _replace_profile_settings_step_check(old_steps)
    with op.batch_alter_table("weekly_training_plans") as batch:
        batch.drop_constraint(
            op.f("uq_weekly_training_plans_athlete_week_revision"), type_="unique"
        )
        batch.create_unique_constraint(
            "uq_weekly_training_plans_athlete_week_start",
            ["athlete_id", "week_start"],
        )
        batch.drop_column("superseded_at")
        batch.drop_column("revision")
