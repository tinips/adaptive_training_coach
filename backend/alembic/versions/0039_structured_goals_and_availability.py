"""Store machine-readable goal metrics and confirmed weekly availability."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0040_structured_goals"
down_revision: str | None = "0039_structured_goal_targets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ONBOARDING_STEPS = (
    "CONSENT", "SETUP_INTRODUCTION", "GOAL_INTAKE", "GOAL_MANUAL_TARGETS",
    "GOAL_SWIMMING_TYPE", "GOAL_METRIC_INTAKE", "GOAL_EVENT_DATE", "GOAL_CONFIRMED",
    "PROFILE_BIRTH_YEAR_INTAKE", "PROFILE_GENDER_INTAKE", "PROFILE_WEIGHT_INTAKE",
    "PROFILE_HEIGHT_INTAKE", "AVAILABILITY_INTAKE", "AVAILABILITY_REVIEW",
    "EQUIPMENT_RECOMMENDATION", "EQUIPMENT_INTAKE", "HEALTH_LIMITATIONS_INTAKE",
    "BASELINE_INTAKE", "TRAINING_HISTORY_IMPORT",
)


def _replace_step_check(table: str, column: str, values: Sequence[str]) -> None:
    constraint = op.f(
        "ck_onboarding_sessions_onboarding_step"
        if table == "onboarding_sessions"
        else "ck_llm_usage_llm_onboarding_step"
    )
    quoted = ", ".join(f"'{value}'" for value in values)
    with op.batch_alter_table(table) as batch:
        batch.drop_constraint(constraint, type_="check")
        batch.create_check_constraint(constraint, f"{column} IN ({quoted})")


def upgrade() -> None:
    _replace_step_check("onboarding_sessions", "current_step", _ONBOARDING_STEPS)
    _replace_step_check("llm_usage", "onboarding_step", _ONBOARDING_STEPS)
    with op.batch_alter_table("athlete_profiles") as batch:
        batch.add_column(sa.Column("weekly_availability_jsonb", sa.JSON(), nullable=True))
    with op.batch_alter_table("training_goals") as batch:
        batch.add_column(
            sa.Column("target_swim_pace_seconds_per_100m", sa.Float(), nullable=True)
        )
        batch.add_column(sa.Column("target_average_speed_kph", sa.Float(), nullable=True))
        batch.add_column(sa.Column("target_finish_time_seconds", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("goal_metadata_jsonb", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.execute("UPDATE onboarding_sessions SET current_step = 'GOAL_INTAKE' WHERE current_step IN ('GOAL_SWIMMING_TYPE', 'GOAL_METRIC_INTAKE')")
    op.execute("UPDATE onboarding_sessions SET current_step = 'AVAILABILITY_INTAKE' WHERE current_step = 'AVAILABILITY_REVIEW'")
    op.execute("UPDATE llm_usage SET onboarding_step = 'GOAL_INTAKE' WHERE onboarding_step IN ('GOAL_SWIMMING_TYPE', 'GOAL_METRIC_INTAKE')")
    op.execute("UPDATE llm_usage SET onboarding_step = 'AVAILABILITY_INTAKE' WHERE onboarding_step = 'AVAILABILITY_REVIEW'")
    with op.batch_alter_table("training_goals") as batch:
        batch.drop_column("goal_metadata_jsonb")
        batch.drop_column("target_finish_time_seconds")
        batch.drop_column("target_average_speed_kph")
        batch.drop_column("target_swim_pace_seconds_per_100m")
    with op.batch_alter_table("athlete_profiles") as batch:
        batch.drop_column("weekly_availability_jsonb")
    old_steps = tuple(
        value for value in _ONBOARDING_STEPS
        if value not in {"GOAL_SWIMMING_TYPE", "GOAL_METRIC_INTAKE", "AVAILABILITY_REVIEW"}
    )
    _replace_step_check("llm_usage", "onboarding_step", old_steps)
    _replace_step_check("onboarding_sessions", "current_step", old_steps)
