"""Add deterministic mandatory athlete profile onboarding.

Revision ID: 0009_mandatory_profile
Revises: 0008_remove_legacy_onboarding
Create Date: 2026-08-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_mandatory_profile"
down_revision: str | None = "0008_remove_legacy_onboarding"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

USER_STATUSES = (
    "NEW",
    "ONBOARDING_IN_PROGRESS",
    "ONBOARDING_COMPLETED",
    "PROFILE_COMPLETED",
    "BASELINE_PENDING",
    "BASELINE_IMPORTING",
    "BASELINE_READY",
    "BASELINE_FAILED",
)
PREVIOUS_USER_STATUSES = tuple(
    value for value in USER_STATUSES if value != "ONBOARDING_COMPLETED"
)
ONBOARDING_STATUSES = ("ACTIVE", "COMPLETED", "CANCELLED")
PREVIOUS_ONBOARDING_STATUSES = ("ACTIVE", "CANCELLED")
ONBOARDING_STEPS = (
    "CONSENT",
    "SETUP_INTRODUCTION",
    "GOAL_INTAKE",
    "GOAL_CONFIRMED",
    "PROFILE_BIRTH_YEAR_INTAKE",
    "PROFILE_GENDER_INTAKE",
    "PROFILE_WEIGHT_INTAKE",
    "PROFILE_HEIGHT_INTAKE",
)
PREVIOUS_ONBOARDING_STEPS = ONBOARDING_STEPS[:4]


def _quoted(values: Sequence[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _replace_enum_check(
    table: str,
    column: str,
    constraint: str,
    values: Sequence[str],
    *,
    length: int,
) -> None:
    with op.batch_alter_table(table) as batch:
        batch.drop_constraint(op.f(constraint), type_="check")
        batch.alter_column(
            column,
            existing_type=sa.String(),
            type_=sa.String(length=length),
            existing_nullable=False,
        )
        batch.create_check_constraint(
            op.f(constraint),
            f"{column} IN ({_quoted(values)})",
        )


def upgrade() -> None:
    """Extend lifecycle/state checks and add canonical demographic fields."""

    _replace_enum_check(
        "users",
        "status",
        "ck_users_user_status",
        USER_STATUSES,
        length=32,
    )
    _replace_enum_check(
        "onboarding_sessions",
        "status",
        "ck_onboarding_sessions_onboarding_status",
        ONBOARDING_STATUSES,
        length=16,
    )
    _replace_enum_check(
        "onboarding_sessions",
        "current_step",
        "ck_onboarding_sessions_onboarding_step",
        ONBOARDING_STEPS,
        length=32,
    )
    _replace_enum_check(
        "llm_usage",
        "onboarding_step",
        "ck_llm_usage_llm_onboarding_step",
        ONBOARDING_STEPS,
        length=32,
    )
    op.execute(
        sa.text(
            "UPDATE onboarding_sessions SET current_step = "
            "'PROFILE_BIRTH_YEAR_INTAKE' "
            "WHERE current_step = 'GOAL_CONFIRMED' AND EXISTS "
            "(SELECT 1 FROM users WHERE users.id = onboarding_sessions.user_id "
            "AND users.status IN ('NEW', 'ONBOARDING_IN_PROGRESS'))"
        )
    )

    with op.batch_alter_table("athlete_profiles") as batch:
        batch.add_column(sa.Column("birth_year", sa.SmallInteger(), nullable=True))
        batch.add_column(sa.Column("gender", sa.String(length=24), nullable=True))
        batch.create_check_constraint(
            op.f("ck_athlete_profiles_birth_year_range"),
            "birth_year IS NULL OR (birth_year >= 1940 AND birth_year <= 2008)",
        )
        batch.create_check_constraint(
            op.f("ck_athlete_profiles_athlete_gender"),
            "gender IS NULL OR gender IN ('MALE', 'FEMALE', 'OTHER_UNSPECIFIED')",
        )


def downgrade() -> None:
    """Remove the new intake while preserving profiles in legacy columns."""

    op.execute(
        sa.text(
            "UPDATE users SET status = 'PROFILE_COMPLETED' "
            "WHERE status = 'ONBOARDING_COMPLETED'"
        )
    )
    op.execute(
        sa.text(
            "UPDATE onboarding_sessions SET status = 'ACTIVE', "
            "current_step = 'GOAL_CONFIRMED' "
            "WHERE status = 'COMPLETED' OR current_step IN "
            "('PROFILE_BIRTH_YEAR_INTAKE', 'PROFILE_GENDER_INTAKE', "
            "'PROFILE_WEIGHT_INTAKE', 'PROFILE_HEIGHT_INTAKE')"
        )
    )

    with op.batch_alter_table("athlete_profiles") as batch:
        batch.drop_constraint(
            op.f("ck_athlete_profiles_athlete_gender"),
            type_="check",
        )
        batch.drop_constraint(
            op.f("ck_athlete_profiles_birth_year_range"),
            type_="check",
        )
        batch.drop_column("gender")
        batch.drop_column("birth_year")

    _replace_enum_check(
        "llm_usage",
        "onboarding_step",
        "ck_llm_usage_llm_onboarding_step",
        PREVIOUS_ONBOARDING_STEPS,
        length=32,
    )
    _replace_enum_check(
        "onboarding_sessions",
        "current_step",
        "ck_onboarding_sessions_onboarding_step",
        PREVIOUS_ONBOARDING_STEPS,
        length=32,
    )
    _replace_enum_check(
        "onboarding_sessions",
        "status",
        "ck_onboarding_sessions_onboarding_status",
        PREVIOUS_ONBOARDING_STATUSES,
        length=16,
    )
    _replace_enum_check(
        "users",
        "status",
        "ck_users_user_status",
        PREVIOUS_USER_STATUSES,
        length=32,
    )
