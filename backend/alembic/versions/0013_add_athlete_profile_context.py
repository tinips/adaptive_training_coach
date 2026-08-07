"""Add raw athlete context fields for conversational onboarding.

Revision ID: 0013_add_athlete_profile_context
Revises: 0012_remove_fitness_level
Create Date: 2026-08-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0013_add_athlete_profile_context"
down_revision: str | None = "0012_remove_fitness_level"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


PREVIOUS_STEPS = (
    "CONSENT",
    "SETUP_INTRODUCTION",
    "GOAL_INTAKE",
    "GOAL_CONFIRMED",
    "PROFILE_BIRTH_YEAR_INTAKE",
    "PROFILE_GENDER_INTAKE",
    "PROFILE_WEIGHT_INTAKE",
    "PROFILE_HEIGHT_INTAKE",
)
CONTEXT_STEPS = (
    "AVAILABILITY_INTAKE",
    "EQUIPMENT_RECOMMENDATION",
    "EQUIPMENT_INTAKE",
    "EQUIPMENT_DETAILS_INTAKE",
    "HEALTH_LIMITATIONS_INTAKE",
)
ONBOARDING_STEPS = PREVIOUS_STEPS + CONTEXT_STEPS


def _quoted(values: Sequence[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _replace_enum_check(
    table: str,
    column: str,
    constraint: str,
    values: Sequence[str],
) -> None:
    """Extend portable SQLAlchemy enum checks on both affected tables."""

    with op.batch_alter_table(table) as batch:
        batch.drop_constraint(op.f(constraint), type_="check")
        batch.alter_column(
            column,
            existing_type=sa.String(length=32),
            type_=sa.String(length=32),
            existing_nullable=False,
        )
        batch.create_check_constraint(
            op.f(constraint),
            f"{column} IN ({_quoted(values)})",
        )


def upgrade() -> None:
    """Add nullable raw context without changing existing profile data."""

    op.add_column(
        "athlete_profiles",
        sa.Column("availability_text", sa.Text(), nullable=True),
    )
    op.add_column(
        "athlete_profiles",
        sa.Column("equipment_recommendation_text", sa.Text(), nullable=True),
    )
    op.add_column(
        "athlete_profiles",
        sa.Column("equipment_text", sa.Text(), nullable=True),
    )
    op.add_column(
        "athlete_profiles",
        sa.Column("health_limitations_text", sa.Text(), nullable=True),
    )
    _replace_enum_check(
        "onboarding_sessions",
        "current_step",
        "ck_onboarding_sessions_onboarding_step",
        ONBOARDING_STEPS,
    )
    _replace_enum_check(
        "llm_usage",
        "onboarding_step",
        "ck_llm_usage_llm_onboarding_step",
        ONBOARDING_STEPS,
    )


def downgrade() -> None:
    """Remove the raw conversational context fields."""

    op.execute(
        sa.text(
            "UPDATE onboarding_sessions SET status = 'ACTIVE', "
            "current_step = 'GOAL_CONFIRMED' "
            "WHERE current_step IN "
            f"({_quoted(CONTEXT_STEPS)})"
        )
    )
    op.execute(
        sa.text(
            "UPDATE llm_usage SET onboarding_step = 'GOAL_CONFIRMED' "
            "WHERE onboarding_step IN "
            f"({_quoted(CONTEXT_STEPS)})"
        )
    )
    _replace_enum_check(
        "llm_usage",
        "onboarding_step",
        "ck_llm_usage_llm_onboarding_step",
        PREVIOUS_STEPS,
    )
    _replace_enum_check(
        "onboarding_sessions",
        "current_step",
        "ck_onboarding_sessions_onboarding_step",
        PREVIOUS_STEPS,
    )
    op.drop_column("athlete_profiles", "health_limitations_text")
    op.drop_column("athlete_profiles", "equipment_text")
    op.drop_column("athlete_profiles", "equipment_recommendation_text")
    op.drop_column("athlete_profiles", "availability_text")
