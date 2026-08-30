"""Add the goal-adaptive self-reported athlete baseline."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0038_self_reported_baselines"
down_revision: str | None = "0037_prune_equipment_catalog"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ONBOARDING_STEPS = (
    "CONSENT",
    "SETUP_INTRODUCTION",
    "GOAL_INTAKE",
    "GOAL_MANUAL_TARGETS",
    "GOAL_EVENT_DATE",
    "GOAL_CONFIRMED",
    "PROFILE_BIRTH_YEAR_INTAKE",
    "PROFILE_GENDER_INTAKE",
    "PROFILE_WEIGHT_INTAKE",
    "PROFILE_HEIGHT_INTAKE",
    "AVAILABILITY_INTAKE",
    "EQUIPMENT_RECOMMENDATION",
    "EQUIPMENT_INTAKE",
    "HEALTH_LIMITATIONS_INTAKE",
    "BASELINE_INTAKE",
    "TRAINING_HISTORY_IMPORT",
)
_PREVIOUS_ONBOARDING_STEPS = tuple(
    value for value in _ONBOARDING_STEPS if value != "BASELINE_INTAKE"
)


def _replace_step_check(
    table: str,
    column: str,
    constraint: str,
    values: Sequence[str],
) -> None:
    quoted = ", ".join(f"'{value}'" for value in values)
    with op.batch_alter_table(table) as batch:
        batch.drop_constraint(constraint, type_="check")
        batch.create_check_constraint(constraint, f"{column} IN ({quoted})")


def upgrade() -> None:
    _replace_step_check(
        "onboarding_sessions",
        "current_step",
        op.f("ck_onboarding_sessions_onboarding_step"),
        _ONBOARDING_STEPS,
    )
    _replace_step_check(
        "llm_usage",
        "onboarding_step",
        op.f("ck_llm_usage_llm_onboarding_step"),
        _ONBOARDING_STEPS,
    )
    op.create_table(
        "athlete_self_reported_baselines",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("athlete_id", sa.Uuid(), nullable=False),
        sa.Column("goal_signature", sa.String(length=128), nullable=False),
        sa.Column("form_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("baseline_jsonb", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("form_version > 0", name="baseline_form_version_positive"),
        sa.ForeignKeyConstraint(["athlete_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "athlete_id", name="uq_athlete_self_reported_baselines_athlete"
        ),
    )


def downgrade() -> None:
    op.execute(
        "UPDATE onboarding_sessions SET current_step = 'HEALTH_LIMITATIONS_INTAKE' "
        "WHERE current_step = 'BASELINE_INTAKE'"
    )
    op.execute(
        "UPDATE llm_usage SET onboarding_step = 'HEALTH_LIMITATIONS_INTAKE' "
        "WHERE onboarding_step = 'BASELINE_INTAKE'"
    )
    _replace_step_check(
        "llm_usage",
        "onboarding_step",
        op.f("ck_llm_usage_llm_onboarding_step"),
        _PREVIOUS_ONBOARDING_STEPS,
    )
    _replace_step_check(
        "onboarding_sessions",
        "current_step",
        op.f("ck_onboarding_sessions_onboarding_step"),
        _PREVIOUS_ONBOARDING_STEPS,
    )
    op.drop_table("athlete_self_reported_baselines")
