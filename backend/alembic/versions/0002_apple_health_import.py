"""Add secure Apple Health import persistence.

Revision ID: 0002_apple_health_import
Revises: 0001_initial
Create Date: 2026-07-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_apple_health_import"
down_revision: str | Sequence[str] | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ONBOARDING_STEPS = (
    "CONSENT",
    "PRIMARY_SPORT",
    "GOAL_TYPE",
    "EVENT_STATUS",
    "EVENT_NAME",
    "EVENT_DATE",
    "GOAL_PRIORITY",
    "AGE",
    "HEIGHT",
    "WEIGHT",
    "TRAINING_DAYS",
    "WEEKDAY_DURATION",
    "WEEKEND_DURATION",
    "EQUIPMENT",
    "POOL_ACCESS",
    "BIKE_ACCESS",
    "HEALTH_AREAS",
    "HEALTH_TIMING",
    "HEALTH_DESCRIPTION",
    "COACH_TONE",
    "COACH_DETAIL",
    "BASELINE_SOURCE",
    "APPLE_HEALTH_PRIVACY_NOTICE",
    "APPLE_HEALTH_WAITING_FOR_FILE",
    "APPLE_HEALTH_PROCESSING",
    "APPLE_HEALTH_IMPORT_COMPLETE",
    "APPLE_HEALTH_IMPORT_FAILED",
    "SUMMARY",
)
BASELINE_SOURCES = (
    "STRAVA",
    "APPLE_HEALTH_EXPORT",
    "MANUAL",
    "CALIBRATION",
    "SKIP_FOR_NOW",
)


def _quoted_values(values: Sequence[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _replace_enum_check(
    table: str,
    column: str,
    constraint: str,
    values: Sequence[str],
    *,
    length: int,
    nullable: bool = False,
) -> None:
    with op.batch_alter_table(table) as batch:
        batch.drop_constraint(op.f(constraint), type_="check")
        batch.alter_column(
            column,
            existing_type=sa.String(),
            type_=sa.String(length=length),
            existing_nullable=nullable,
        )
        batch.create_check_constraint(
            op.f(constraint),
            f"{column} IN ({_quoted_values(values)})",
        )


def upgrade() -> None:
    """Apply this revision."""

    _replace_enum_check(
        "onboarding_sessions",
        "current_step",
        "ck_onboarding_sessions_onboarding_step",
        ONBOARDING_STEPS,
        length=32,
    )
    _replace_enum_check(
        "onboarding_sessions",
        "pending_free_text_step",
        "ck_onboarding_sessions_pending_onboarding_step",
        ONBOARDING_STEPS,
        length=32,
        nullable=True,
    )
    _replace_enum_check(
        "llm_usage",
        "onboarding_step",
        "ck_llm_usage_llm_onboarding_step",
        ONBOARDING_STEPS,
        length=32,
    )
    _replace_enum_check(
        "baseline_preferences",
        "selected_source",
        "ck_baseline_preferences_baseline_source",
        BASELINE_SOURCES,
        length=32,
    )
    _replace_enum_check(
        "athlete_baselines",
        "source",
        "ck_athlete_baselines_athlete_baseline_source",
        BASELINE_SOURCES,
        length=32,
    )
    _replace_enum_check(
        "activities",
        "source",
        "ck_activities_activity_source",
        ("STRAVA", "APPLE_HEALTH"),
        length=16,
    )

    with op.batch_alter_table("activities") as batch:
        batch.drop_constraint(
            "uq_activities_source_external_id",
            type_="unique",
        )
        batch.create_unique_constraint(
            "uq_activities_user_source_external_id",
            ["user_id", "source", "external_id"],
        )
        batch.add_column(
            sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.add_column(sa.Column("calories_kcal", sa.Float(), nullable=True))
        batch.add_column(
            sa.Column(
                "heart_rate_sample_count",
                sa.Integer(),
                server_default="0",
                nullable=False,
            )
        )
        batch.add_column(
            sa.Column(
                "heart_rate_quality",
                sa.String(length=24),
                server_default="UNKNOWN",
                nullable=False,
            )
        )
        batch.add_column(
            sa.Column(
                "heart_rate_reliable",
                sa.Boolean(),
                server_default=sa.text("false"),
                nullable=False,
            )
        )
        batch.create_check_constraint(
            op.f("ck_activities_heart_rate_temporal_quality"),
            "heart_rate_quality IN "
            "('EXACT_SAMPLE', 'SHORT_INTERVAL', 'COARSE_INTERVAL', 'UNKNOWN')",
        )

    op.create_table(
        "apple_health_import_jobs",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("onboarding_session_id", sa.Uuid(), nullable=False),
        sa.Column("telegram_update_id", sa.BigInteger(), nullable=True),
        sa.Column("telegram_file_id", sa.String(length=255), nullable=False),
        sa.Column("telegram_file_unique_id", sa.String(length=255), nullable=False),
        sa.Column("display_filename", sa.String(length=255), nullable=False),
        sa.Column("file_sha256", sa.String(length=64), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default="RECEIVED",
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "workouts_found",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "activities_imported",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "activities_updated",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "activities_skipped",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "heart_rate_records_matched",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "warning_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column("safe_error_code", sa.String(length=64), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('RECEIVED', 'PROCESSING', 'SUCCEEDED', 'FAILED', 'CANCELLED')",
            name=op.f(
                "ck_apple_health_import_jobs_apple_health_import_status"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["onboarding_session_id"],
            ["onboarding_sessions.id"],
            name=op.f(
                "fk_apple_health_import_jobs_onboarding_session_id_onboarding_sessions"
            ),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_apple_health_import_jobs_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name=op.f("pk_apple_health_import_jobs"),
        ),
    )
    op.create_index(
        "ix_apple_health_import_jobs_user_created",
        "apple_health_import_jobs",
        ["user_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "uq_apple_health_import_jobs_active_user",
        "apple_health_import_jobs",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('RECEIVED', 'PROCESSING')"),
        sqlite_where=sa.text("status IN ('RECEIVED', 'PROCESSING')"),
    )

    op.create_table(
        "heart_rate_observations",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("activity_id", sa.Uuid(), nullable=False),
        sa.Column("source_record_key", sa.String(length=64), nullable=False),
        sa.Column("source_name", sa.String(length=255), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("beats_per_minute", sa.Float(), nullable=False),
        sa.Column("temporal_quality", sa.String(length=24), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "temporal_quality IN "
            "('EXACT_SAMPLE', 'SHORT_INTERVAL', 'COARSE_INTERVAL', 'UNKNOWN')",
            name=op.f(
                "ck_heart_rate_observations_heart_rate_observation_quality"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["activity_id"],
            ["activities.id"],
            name=op.f("fk_heart_rate_observations_activity_id_activities"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_heart_rate_observations_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name=op.f("pk_heart_rate_observations"),
        ),
        sa.UniqueConstraint(
            "user_id",
            "source_record_key",
            name="uq_heart_rate_observations_user_source_key",
        ),
    )
    op.create_index(
        "ix_heart_rate_observations_activity_started",
        "heart_rate_observations",
        ["activity_id", "started_at"],
        unique=False,
    )


def downgrade() -> None:
    """Revert this revision."""

    op.drop_index(
        "ix_heart_rate_observations_activity_started",
        table_name="heart_rate_observations",
    )
    op.drop_table("heart_rate_observations")
    op.drop_index(
        "uq_apple_health_import_jobs_active_user",
        table_name="apple_health_import_jobs",
    )
    op.drop_index(
        "ix_apple_health_import_jobs_user_created",
        table_name="apple_health_import_jobs",
    )
    op.drop_table("apple_health_import_jobs")

    with op.batch_alter_table("activities") as batch:
        batch.drop_constraint(
            op.f("ck_activities_heart_rate_temporal_quality"),
            type_="check",
        )
        batch.drop_column("heart_rate_reliable")
        batch.drop_column("heart_rate_quality")
        batch.drop_column("heart_rate_sample_count")
        batch.drop_column("calories_kcal")
        batch.drop_column("ended_at")
        batch.drop_constraint(
            "uq_activities_user_source_external_id",
            type_="unique",
        )
        batch.create_unique_constraint(
            "uq_activities_source_external_id",
            ["source", "external_id"],
        )

    _replace_enum_check(
        "activities",
        "source",
        "ck_activities_activity_source",
        ("STRAVA",),
        length=16,
    )
    old_baseline_sources = ("STRAVA", "MANUAL", "CALIBRATION", "SKIP_FOR_NOW")
    _replace_enum_check(
        "athlete_baselines",
        "source",
        "ck_athlete_baselines_athlete_baseline_source",
        old_baseline_sources,
        length=16,
    )
    _replace_enum_check(
        "baseline_preferences",
        "selected_source",
        "ck_baseline_preferences_baseline_source",
        old_baseline_sources,
        length=16,
    )
    old_onboarding_steps = tuple(
        step for step in ONBOARDING_STEPS if not step.startswith("APPLE_HEALTH_")
    )
    _replace_enum_check(
        "llm_usage",
        "onboarding_step",
        "ck_llm_usage_llm_onboarding_step",
        old_onboarding_steps,
        length=32,
    )
    _replace_enum_check(
        "onboarding_sessions",
        "pending_free_text_step",
        "ck_onboarding_sessions_pending_onboarding_step",
        old_onboarding_steps,
        length=32,
        nullable=True,
    )
    _replace_enum_check(
        "onboarding_sessions",
        "current_step",
        "ck_onboarding_sessions_onboarding_step",
        old_onboarding_steps,
        length=32,
    )
