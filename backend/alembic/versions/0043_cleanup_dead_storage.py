"""Remove unused storage and normalize persisted profile states.

Revision ID: 0043_cleanup_dead_storage
Revises: 0042_profile_plan_revisions
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0043_cleanup_dead_storage"
down_revision: str | None = "0042_profile_plan_revisions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ONBOARDING_STEPS = (
    "'CONSENT','SETUP_INTRODUCTION','GOAL_INTAKE','GOAL_SWIMMING_TYPE',"
    "'GOAL_METRIC_INTAKE','GOAL_EVENT_DATE','GOAL_CONFIRMED',"
    "'PROFILE_BIRTH_YEAR_INTAKE','PROFILE_GENDER_INTAKE',"
    "'PROFILE_WEIGHT_INTAKE','PROFILE_HEIGHT_INTAKE','PROFILE_TIMEZONE_INTAKE',"
    "'AVAILABILITY_INTAKE','AVAILABILITY_REVIEW','EQUIPMENT_RECOMMENDATION',"
    "'EQUIPMENT_INTAKE','HEALTH_LIMITATIONS_INTAKE','BASELINE_INTAKE',"
    "'TRAINING_HISTORY_IMPORT'"
)
_PROFILE_STEPS = (
    "'MENU','GOAL_MENU','GOAL_MAIN','GOAL_METRICS','GOAL_DATE',"
    "'GOAL_SECONDARY','GOAL_CLASSIFICATION_CONFIRM','AVAILABILITY',"
    "'AVAILABILITY_REVIEW','EQUIPMENT','HEALTH','PERSONAL_MENU',"
    "'PERSONAL_BIRTH_YEAR','PERSONAL_GENDER','PERSONAL_WEIGHT',"
    "'PERSONAL_HEIGHT','PERSONAL_TIMEZONE'"
)


def _replace_check(table: str, column: str, name: str, values: str) -> None:
    with op.batch_alter_table(table) as batch:
        batch.drop_constraint(op.f(f"ck_{table}_{name}"), type_="check")
        batch.create_check_constraint(name, f"{column} IN ({values})")


def upgrade() -> None:
    # No live checkpoint can remain in the retired manual-goal state.
    op.execute(
        "UPDATE onboarding_sessions SET current_step = 'GOAL_INTAKE' "
        "WHERE current_step = 'GOAL_MANUAL_TARGETS'"
    )
    op.execute(
        "UPDATE profile_settings_sessions SET current_step = 'MENU' "
        "WHERE current_step = 'GOAL_OUTCOME'"
    )
    _replace_check(
        "onboarding_sessions", "current_step", "onboarding_step", _ONBOARDING_STEPS
    )
    _replace_check(
        "llm_usage", "onboarding_step", "llm_onboarding_step", _ONBOARDING_STEPS
    )
    _replace_check(
        "profile_settings_sessions",
        "current_step",
        "profile_settings_step",
        _PROFILE_STEPS,
    )

    with op.batch_alter_table("athlete_profiles") as batch:
        batch.drop_constraint(op.f("ck_athlete_profiles_age_range"), type_="check")
        batch.drop_column("age")
    with op.batch_alter_table("training_goals") as batch:
        batch.drop_constraint(
            op.f("ck_training_goals_ck_training_goals_training_goal_status"),
            type_="check",
        )
        batch.drop_column("status")
        batch.drop_column("original_description")
    with op.batch_alter_table("hiking_workout_details") as batch:
        batch.drop_constraint(
            op.f("ck_hiking_workout_details_pack_weight_nonnegative"), type_="check"
        )
        batch.drop_column("pack_weight_kg")
    with op.batch_alter_table("pool_swimming_details") as batch:
        batch.drop_constraint(
            op.f("ck_pool_swimming_details_average_swolf_nonnegative"), type_="check"
        )
        batch.drop_column("average_swolf")
    with op.batch_alter_table("activity_source_links") as batch:
        batch.drop_column("deleted_at")
    op.drop_table("workout_flow_sessions")


def downgrade() -> None:
    op.create_table(
        "workout_flow_sessions",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("workout_id", sa.Uuid(), nullable=True),
        sa.Column(
            "state", sa.String(32), nullable=False, server_default="WAITING_FOR_FILE"
        ),
        sa.Column("pending_manual_average_heart_rate", sa.Integer(), nullable=True),
        sa.Column("pending_discomfort_description", sa.Text(), nullable=True),
        sa.Column(
            "return_to_onboarding",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "pending_manual_average_heart_rate IS NULL OR "
            "(pending_manual_average_heart_rate >= 30 AND "
            "pending_manual_average_heart_rate <= 250)",
            name=op.f(
                "ck_workout_flow_sessions_pending_manual_average_heart_rate_range"
            ),
        ),
        sa.ForeignKeyConstraint(["workout_id"], ["workouts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_workout_flow_sessions_user_id"),
    )
    with op.batch_alter_table("activity_source_links") as batch:
        batch.add_column(
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True)
        )
    with op.batch_alter_table("pool_swimming_details") as batch:
        batch.add_column(sa.Column("average_swolf", sa.Float(), nullable=True))
        batch.create_check_constraint(
            "average_swolf_nonnegative", "average_swolf IS NULL OR average_swolf >= 0"
        )
    with op.batch_alter_table("hiking_workout_details") as batch:
        batch.add_column(sa.Column("pack_weight_kg", sa.Float(), nullable=True))
        batch.create_check_constraint(
            "pack_weight_nonnegative", "pack_weight_kg IS NULL OR pack_weight_kg >= 0"
        )
    with op.batch_alter_table("training_goals") as batch:
        batch.add_column(
            sa.Column(
                "original_description", sa.Text(), nullable=False, server_default=""
            )
        )
        batch.add_column(
            sa.Column(
                "status", sa.String(32), nullable=False, server_default="CONFIRMED"
            )
        )
        batch.create_check_constraint("training_goal_status", "status = 'CONFIRMED'")
    with op.batch_alter_table("athlete_profiles") as batch:
        batch.add_column(
            sa.Column("age", sa.SmallInteger(), nullable=False, server_default="16")
        )
        batch.create_check_constraint("age_range", "age >= 16 AND age <= 100")

    op.execute(
        "UPDATE onboarding_sessions SET current_step = 'GOAL_INTAKE' "
        "WHERE current_step = 'PROFILE_TIMEZONE_INTAKE'"
    )
    op.execute(
        "UPDATE profile_settings_sessions SET current_step = 'MENU' "
        "WHERE current_step = 'PERSONAL_TIMEZONE'"
    )
    old_onboarding_steps = _ONBOARDING_STEPS.replace(",'PROFILE_TIMEZONE_INTAKE'", "")
    old_profile_steps = (
        _PROFILE_STEPS.replace(",'PERSONAL_TIMEZONE'", "") + ",'GOAL_OUTCOME'"
    )
    _replace_check(
        "llm_usage", "onboarding_step", "llm_onboarding_step", old_onboarding_steps
    )
    _replace_check(
        "onboarding_sessions", "current_step", "onboarding_step", old_onboarding_steps
    )
    _replace_check(
        "profile_settings_sessions",
        "current_step",
        "profile_settings_step",
        old_profile_steps,
    )
