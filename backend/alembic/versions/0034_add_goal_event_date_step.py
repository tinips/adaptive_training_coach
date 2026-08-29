"""Add the GOAL_EVENT_DATE onboarding step.

`OnboardingStep` is a persisted enum implemented as `native_enum=False`
(see `app.db.models.persisted_enum`), so it is a VARCHAR column plus a CHECK
constraint listing the allowed values, not a native PostgreSQL enum type.
Adding a member therefore means dropping and recreating the check
constraints on both `onboarding_sessions.current_step` and
`llm_usage.onboarding_step`, rather than `ALTER TYPE ... ADD VALUE`.

Revision ID: 0034_add_goal_event_date_step
Revises: 0032_retire_non_endurance_goals
Create Date: 2026-08-29

Numbering note: 0032_retire_non_endurance_goals is the actual current head
(verified via `alembic heads`) with nothing at 0033. Per the controller's
explicit numbering ruling for this exact situation, this migration is named
0034, deliberately leaving 0033 unused; alembic resolves migration order via
`revision`/`down_revision`, not filename, so this gap is cosmetic.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0034_add_goal_event_date_step"
down_revision: str | None = "0032_retire_non_endurance_goals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PREVIOUS_ONBOARDING_STEPS = (
    "CONSENT",
    "SETUP_INTRODUCTION",
    "GOAL_INTAKE",
    "GOAL_CONFIRMED",
    "PROFILE_BIRTH_YEAR_INTAKE",
    "PROFILE_GENDER_INTAKE",
    "PROFILE_WEIGHT_INTAKE",
    "PROFILE_HEIGHT_INTAKE",
    "AVAILABILITY_INTAKE",
    "EQUIPMENT_RECOMMENDATION",
    "EQUIPMENT_INTAKE",
    "HEALTH_LIMITATIONS_INTAKE",
    "TRAINING_HISTORY_IMPORT",
)
_ONBOARDING_STEPS = (
    "CONSENT",
    "SETUP_INTRODUCTION",
    "GOAL_INTAKE",
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
    "TRAINING_HISTORY_IMPORT",
)


def _quoted(values: Sequence[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _replace_check(
    table: str,
    column: str,
    constraint: str,
    values: Sequence[str],
) -> None:
    with op.batch_alter_table(table) as batch:
        batch.drop_constraint(op.f(constraint), type_="check")
        batch.create_check_constraint(
            op.f(constraint),
            f"{column} IN ({_quoted(values)})",
        )


def upgrade() -> None:
    _replace_check(
        "onboarding_sessions",
        "current_step",
        "ck_onboarding_sessions_onboarding_step",
        _ONBOARDING_STEPS,
    )
    _replace_check(
        "llm_usage",
        "onboarding_step",
        "ck_llm_usage_llm_onboarding_step",
        _ONBOARDING_STEPS,
    )


def downgrade() -> None:
    # No pre-0034 value describes "template chosen, date not yet asked", so
    # any session paused there falls back to GOAL_CONFIRMED, exactly where
    # both submit_event_date and skip_event_date advance to once answered.
    op.execute(
        sa.text(
            "UPDATE onboarding_sessions SET current_step = 'GOAL_CONFIRMED' "
            "WHERE current_step = 'GOAL_EVENT_DATE'"
        )
    )
    op.execute(
        sa.text(
            "UPDATE llm_usage SET onboarding_step = 'GOAL_CONFIRMED' "
            "WHERE onboarding_step = 'GOAL_EVENT_DATE'"
        )
    )
    _replace_check(
        "llm_usage",
        "onboarding_step",
        "ck_llm_usage_llm_onboarding_step",
        _PREVIOUS_ONBOARDING_STEPS,
    )
    _replace_check(
        "onboarding_sessions",
        "current_step",
        "ck_onboarding_sessions_onboarding_step",
        _PREVIOUS_ONBOARDING_STEPS,
    )
