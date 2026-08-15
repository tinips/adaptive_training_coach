"""Add optional onboarding history import and retained heart-rate facts.

Revision ID: 0024_training_history_import
Revises: 0023_prune_training_catalog_seed
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0024_training_history_import"
down_revision: str | None = "0023_prune_training_catalog_seed"
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
)
_ONBOARDING_STEPS = _PREVIOUS_ONBOARDING_STEPS + ("TRAINING_HISTORY_IMPORT",)


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
    _replace_check(
        "swimming_workout_details",
        "swimming_environment",
        "ck_swimming_workout_details_swimming_environment",
        ("POOL", "OPEN_WATER", "UNKNOWN"),
    )

    with op.batch_alter_table("apple_health_import_jobs") as batch:
        batch.add_column(
            sa.Column(
                "context",
                sa.String(length=24),
                server_default="POST_ONBOARDING",
                nullable=False,
            )
        )
        batch.add_column(sa.Column("onboarding_session_id", sa.Uuid(), nullable=True))
        batch.create_check_constraint(
            op.f("ck_apple_health_import_jobs_training_import_context"),
            "context IN ('ONBOARDING_HISTORY', 'POST_ONBOARDING')",
        )
        batch.create_foreign_key(
            op.f(
                "fk_apple_health_import_jobs_onboarding_session_id_onboarding_sessions"
            ),
            "onboarding_sessions",
            ["onboarding_session_id"],
            ["id"],
            ondelete="SET NULL",
        )

    op.create_table(
        "workout_heart_rate_observations",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("workout_id", sa.Uuid(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("source_record_key", sa.String(length=64), nullable=False),
        sa.Column("source_name", sa.String(length=255), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("beats_per_minute", sa.Float(), nullable=False),
        sa.Column("temporal_quality", sa.String(length=24), nullable=False),
        sa.Column("import_job_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "source IN ('MANUAL', 'APPLE_HEALTH', 'TCX', 'FIT', 'OTHER_IMPORT')",
            name=op.f(
                "ck_workout_heart_rate_observations_heart_rate_observation_source"
            ),
        ),
        sa.CheckConstraint(
            "temporal_quality IN "
            "('EXACT_SAMPLE', 'SHORT_INTERVAL', 'COARSE_INTERVAL', 'UNKNOWN')",
            name=op.f(
                "ck_workout_heart_rate_observations_heart_rate_observation_quality"
            ),
        ),
        sa.CheckConstraint(
            "beats_per_minute > 0",
            name=op.f("ck_workout_heart_rate_observations_beats_per_minute_positive"),
        ),
        sa.CheckConstraint(
            "ended_at >= started_at",
            name=op.f("ck_workout_heart_rate_observations_observation_period_order"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_workout_heart_rate_observations_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workout_id"],
            ["workouts.id"],
            name=op.f("fk_workout_heart_rate_observations_workout_id_workouts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["import_job_id"],
            ["apple_health_import_jobs.id"],
            name=op.f(
                "fk_workout_heart_rate_observations_import_job_id_apple_health_import_jobs"
            ),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workout_heart_rate_observations")),
        sa.UniqueConstraint(
            "user_id",
            "source",
            "source_record_key",
            name="uq_workout_hr_observations_user_source_key",
        ),
    )
    op.create_index(
        "ix_workout_hr_observations_workout_started",
        "workout_heart_rate_observations",
        ["workout_id", "started_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_workout_hr_observations_workout_started",
        table_name="workout_heart_rate_observations",
    )
    op.drop_table("workout_heart_rate_observations")

    with op.batch_alter_table("apple_health_import_jobs") as batch:
        batch.drop_constraint(
            op.f(
                "fk_apple_health_import_jobs_onboarding_session_id_onboarding_sessions"
            ),
            type_="foreignkey",
        )
        batch.drop_constraint(
            op.f("ck_apple_health_import_jobs_training_import_context"),
            type_="check",
        )
        batch.drop_column("onboarding_session_id")
        batch.drop_column("context")

    op.execute(
        sa.text(
            "UPDATE onboarding_sessions "
            "SET current_step = 'HEALTH_LIMITATIONS_INTAKE' "
            "WHERE current_step = 'TRAINING_HISTORY_IMPORT'"
        )
    )
    op.execute(
        sa.text(
            "UPDATE llm_usage SET onboarding_step = 'HEALTH_LIMITATIONS_INTAKE' "
            "WHERE onboarding_step = 'TRAINING_HISTORY_IMPORT'"
        )
    )
    _replace_check(
        "swimming_workout_details",
        "swimming_environment",
        "ck_swimming_workout_details_swimming_environment",
        ("POOL", "OPEN_WATER"),
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
