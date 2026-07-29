"""Add unified training-file provenance and workout feedback persistence.

Revision ID: 0003_unified_training_import
Revises: 0002_apple_health_import
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003_unified_training_import"
down_revision: str | Sequence[str] | None = "0002_apple_health_import"
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
    "FILE_IMPORT_WAITING",
    "FILE_IMPORT_PROCESSING",
    "FILE_IMPORT_COMPLETE",
    "SUMMARY",
)
BASELINE_SOURCES = (
    "STRAVA",
    "APPLE_HEALTH_EXPORT",
    "FILE_IMPORT",
    "MANUAL",
    "CALIBRATION",
    "SKIP_FOR_NOW",
)
ACTIVITY_SOURCES = ("STRAVA", "APPLE_HEALTH", "TCX")
HEART_RATE_TEMPORAL_QUALITIES = (
    "EXACT_SAMPLE",
    "SHORT_INTERVAL",
    "COARSE_INTERVAL",
    "MANUAL",
    "UNKNOWN",
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
    """Apply the additive unified-import schema while preserving 0002 data."""

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
        ACTIVITY_SOURCES,
        length=16,
    )
    _replace_enum_check(
        "activities",
        "heart_rate_quality",
        "ck_activities_heart_rate_temporal_quality",
        HEART_RATE_TEMPORAL_QUALITIES,
        length=24,
    )
    _replace_enum_check(
        "heart_rate_observations",
        "temporal_quality",
        "ck_heart_rate_observations_heart_rate_observation_quality",
        HEART_RATE_TEMPORAL_QUALITIES,
        length=24,
    )

    with op.batch_alter_table("activities") as batch:
        batch.add_column(
            sa.Column(
                "average_heart_rate_source",
                sa.String(length=24),
                server_default="UNAVAILABLE",
                nullable=False,
            )
        )
        batch.add_column(sa.Column("average_cadence", sa.Float(), nullable=True))
        batch.add_column(
            sa.Column(
                "route_points",
                sa.JSON().with_variant(
                    postgresql.JSONB(astext_type=sa.Text()),
                    "postgresql",
                ),
                nullable=True,
            )
        )
        batch.create_check_constraint(
            op.f("ck_activities_heart_rate_source"),
            "average_heart_rate_source IN "
            "('MEASURED_SENSOR', 'PROVIDER_SUMMARY', 'DERIVED', "
            "'USER_REPORTED', 'UNAVAILABLE')",
        )
        batch.create_check_constraint(
            op.f("ck_activities_average_cadence_nonnegative"),
            "average_cadence IS NULL OR average_cadence >= 0",
        )

    op.execute(
        sa.text(
            "UPDATE activities "
            "SET average_heart_rate_source = CASE "
            "WHEN average_heart_rate IS NULL THEN 'UNAVAILABLE' "
            "WHEN source = 'APPLE_HEALTH' "
            "AND heart_rate_quality = 'EXACT_SAMPLE' THEN 'MEASURED_SENSOR' "
            "WHEN source = 'APPLE_HEALTH' THEN 'PROVIDER_SUMMARY' "
            "ELSE 'PROVIDER_SUMMARY' END"
        )
    )
    op.execute(
        sa.text(
            "UPDATE activities SET heart_rate_reliable = true "
            "WHERE source = 'STRAVA' AND average_heart_rate IS NOT NULL"
        )
    )

    with op.batch_alter_table("apple_health_import_jobs") as batch:
        batch.drop_constraint(
            op.f(
                "fk_apple_health_import_jobs_onboarding_session_id_onboarding_sessions"
            ),
            type_="foreignkey",
        )
        batch.alter_column(
            "onboarding_session_id",
            existing_type=sa.Uuid(),
            nullable=True,
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
        batch.add_column(sa.Column("activity_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            op.f("fk_apple_health_import_jobs_activity_id_activities"),
            "activities",
            ["activity_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.add_column(
            sa.Column("temporary_path", sa.String(length=1024), nullable=True)
        )
        batch.add_column(
            sa.Column(
                "file_format",
                sa.String(length=24),
                server_default="APPLE_HEALTH_ZIP",
                nullable=False,
            )
        )
        batch.add_column(
            sa.Column(
                "context",
                sa.String(length=16),
                server_default="ONBOARDING",
                nullable=False,
            )
        )
        batch.create_check_constraint(
            op.f("ck_apple_health_import_jobs_training_file_format"),
            "file_format IN ('APPLE_HEALTH_ZIP', 'TCX', 'UNKNOWN')",
        )
        batch.create_check_constraint(
            op.f("ck_apple_health_import_jobs_training_import_context"),
            "context IN ('ONBOARDING', 'DAILY')",
        )

    op.create_index(
        "ix_apple_health_import_jobs_user_file_sha256",
        "apple_health_import_jobs",
        ["user_id", "file_sha256"],
        unique=False,
    )

    op.create_table(
        "activity_source_links",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("activity_id", sa.Uuid(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("external_id", sa.String(length=128), nullable=False),
        sa.Column("file_sha256", sa.String(length=64), nullable=True),
        sa.Column("import_job_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "source IN ('STRAVA', 'APPLE_HEALTH', 'TCX')",
            name=op.f("ck_activity_source_links_activity_source_link_source"),
        ),
        sa.ForeignKeyConstraint(
            ["activity_id"],
            ["activities.id"],
            name=op.f("fk_activity_source_links_activity_id_activities"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["import_job_id"],
            ["apple_health_import_jobs.id"],
            name=op.f(
                "fk_activity_source_links_import_job_id_apple_health_import_jobs"
            ),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_activity_source_links_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_activity_source_links")),
        sa.UniqueConstraint(
            "user_id",
            "source",
            "external_id",
            name="uq_activity_source_links_user_source_external_id",
        ),
    )
    op.create_index(
        "ix_activity_source_links_activity_id",
        "activity_source_links",
        ["activity_id"],
        unique=False,
    )
    op.execute(
        sa.text(
            "INSERT INTO activity_source_links "
            "(id, user_id, activity_id, source, external_id, "
            "file_sha256, import_job_id, created_at, updated_at) "
            "SELECT id, user_id, id, source, external_id, "
            "NULL, NULL, created_at, updated_at FROM activities"
        )
    )

    op.create_table(
        "activity_feedback",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("activity_id", sa.Uuid(), nullable=False),
        sa.Column("manual_average_heart_rate", sa.Integer(), nullable=True),
        sa.Column("reported_rpe", sa.Integer(), nullable=True),
        sa.Column("reported_rpe_label", sa.String(length=32), nullable=True),
        sa.Column("reported_discomfort", sa.Boolean(), nullable=True),
        sa.Column("discomfort_body_area", sa.String(length=16), nullable=True),
        sa.Column("discomfort_severity", sa.String(length=16), nullable=True),
        sa.Column("discomfort_description", sa.Text(), nullable=True),
        sa.Column(
            "feedback_created_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "manual_average_heart_rate IS NULL "
            "OR (manual_average_heart_rate >= 30 "
            "AND manual_average_heart_rate <= 250)",
            name=op.f("ck_activity_feedback_manual_average_heart_rate_range"),
        ),
        sa.CheckConstraint(
            "reported_rpe IS NULL OR (reported_rpe >= 1 AND reported_rpe <= 10)",
            name=op.f("ck_activity_feedback_reported_rpe_range"),
        ),
        sa.CheckConstraint(
            "discomfort_body_area IN "
            "('SHOULDER', 'BACK', 'HIP', 'KNEE', 'ANKLE_FOOT', 'OTHER')",
            name=op.f("ck_activity_feedback_activity_feedback_body_area"),
        ),
        sa.CheckConstraint(
            "discomfort_severity IN ('MILD', 'MODERATE', 'SEVERE')",
            name=op.f("ck_activity_feedback_discomfort_severity"),
        ),
        sa.ForeignKeyConstraint(
            ["activity_id"],
            ["activities.id"],
            name=op.f("fk_activity_feedback_activity_id_activities"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_activity_feedback_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_activity_feedback")),
        sa.UniqueConstraint(
            "user_id",
            "activity_id",
            name="uq_activity_feedback_user_activity",
        ),
    )

    op.create_table(
        "workout_flow_sessions",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("activity_id", sa.Uuid(), nullable=True),
        sa.Column(
            "state",
            sa.String(length=32),
            server_default="WAITING_FOR_FILE",
            nullable=False,
        ),
        sa.Column(
            "pending_manual_average_heart_rate",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "pending_discomfort_description",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "return_to_onboarding",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "state IN "
            "('WAITING_FOR_FILE', 'HR_OFFER', 'HR_ENTRY', 'HR_CONFIRM', "
            "'RPE', 'DISCOMFORT', 'BODY_AREA', 'DESCRIPTION_ENTRY', "
            "'DESCRIPTION_CONFIRM', 'SEVERITY', 'COMPLETE', 'CANCELLED')",
            name=op.f("ck_workout_flow_sessions_workout_flow_step"),
        ),
        sa.CheckConstraint(
            "pending_manual_average_heart_rate IS NULL "
            "OR (pending_manual_average_heart_rate >= 30 "
            "AND pending_manual_average_heart_rate <= 250)",
            name=op.f(
                "ck_workout_flow_sessions_pending_manual_average_heart_rate_range"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["activity_id"],
            ["activities.id"],
            name=op.f("fk_workout_flow_sessions_activity_id_activities"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_workout_flow_sessions_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workout_flow_sessions")),
        sa.UniqueConstraint(
            "user_id",
            name="uq_workout_flow_sessions_user_id",
        ),
    )


def downgrade() -> None:
    """Return to 0002, mapping new enum values to their closest old values."""

    op.execute(
        sa.text(
            "UPDATE onboarding_sessions "
            "SET current_step = CASE current_step "
            "WHEN 'FILE_IMPORT_WAITING' THEN 'APPLE_HEALTH_WAITING_FOR_FILE' "
            "WHEN 'FILE_IMPORT_PROCESSING' THEN 'APPLE_HEALTH_PROCESSING' "
            "WHEN 'FILE_IMPORT_COMPLETE' THEN 'APPLE_HEALTH_IMPORT_COMPLETE' "
            "ELSE current_step END"
        )
    )
    op.execute(
        sa.text(
            "UPDATE onboarding_sessions "
            "SET pending_free_text_step = CASE pending_free_text_step "
            "WHEN 'FILE_IMPORT_WAITING' THEN 'APPLE_HEALTH_WAITING_FOR_FILE' "
            "WHEN 'FILE_IMPORT_PROCESSING' THEN 'APPLE_HEALTH_PROCESSING' "
            "WHEN 'FILE_IMPORT_COMPLETE' THEN 'APPLE_HEALTH_IMPORT_COMPLETE' "
            "ELSE pending_free_text_step END"
        )
    )
    op.execute(
        sa.text(
            "UPDATE llm_usage SET onboarding_step = CASE onboarding_step "
            "WHEN 'FILE_IMPORT_WAITING' THEN 'APPLE_HEALTH_WAITING_FOR_FILE' "
            "WHEN 'FILE_IMPORT_PROCESSING' THEN 'APPLE_HEALTH_PROCESSING' "
            "WHEN 'FILE_IMPORT_COMPLETE' THEN 'APPLE_HEALTH_IMPORT_COMPLETE' "
            "ELSE onboarding_step END"
        )
    )
    op.execute(
        sa.text(
            "UPDATE baseline_preferences "
            "SET selected_source = 'APPLE_HEALTH_EXPORT' "
            "WHERE selected_source = 'FILE_IMPORT'"
        )
    )
    op.execute(
        sa.text(
            "UPDATE athlete_baselines SET source = 'APPLE_HEALTH_EXPORT' "
            "WHERE source = 'FILE_IMPORT'"
        )
    )
    op.execute(
        sa.text(
            "UPDATE activities SET heart_rate_quality = 'UNKNOWN' "
            "WHERE heart_rate_quality = 'MANUAL'"
        )
    )
    op.execute(
        sa.text(
            "UPDATE heart_rate_observations SET temporal_quality = 'UNKNOWN' "
            "WHERE temporal_quality = 'MANUAL'"
        )
    )
    op.execute(
        sa.text(
            "UPDATE activities "
            "SET external_id = 'tcx:' || CAST(id AS VARCHAR(64)) "
            "WHERE source = 'TCX'"
        )
    )
    op.execute(
        sa.text("UPDATE activities SET source = 'APPLE_HEALTH' WHERE source = 'TCX'")
    )

    op.drop_table("workout_flow_sessions")
    op.drop_table("activity_feedback")
    op.drop_index(
        "ix_activity_source_links_activity_id",
        table_name="activity_source_links",
    )
    op.drop_table("activity_source_links")

    op.drop_index(
        "ix_apple_health_import_jobs_user_file_sha256",
        table_name="apple_health_import_jobs",
    )
    op.execute(
        sa.text(
            "UPDATE apple_health_import_jobs "
            "SET onboarding_session_id = "
            "(SELECT onboarding_sessions.id FROM onboarding_sessions "
            "WHERE onboarding_sessions.user_id = "
            "apple_health_import_jobs.user_id LIMIT 1) "
            "WHERE onboarding_session_id IS NULL"
        )
    )
    op.execute(
        sa.text(
            "DELETE FROM apple_health_import_jobs WHERE onboarding_session_id IS NULL"
        )
    )
    with op.batch_alter_table("apple_health_import_jobs") as batch:
        batch.drop_constraint(
            op.f("ck_apple_health_import_jobs_training_import_context"),
            type_="check",
        )
        batch.drop_constraint(
            op.f("ck_apple_health_import_jobs_training_file_format"),
            type_="check",
        )
        batch.drop_constraint(
            op.f("fk_apple_health_import_jobs_activity_id_activities"),
            type_="foreignkey",
        )
        batch.drop_column("context")
        batch.drop_column("file_format")
        batch.drop_column("temporary_path")
        batch.drop_column("activity_id")
        batch.drop_constraint(
            op.f(
                "fk_apple_health_import_jobs_onboarding_session_id_onboarding_sessions"
            ),
            type_="foreignkey",
        )
        batch.alter_column(
            "onboarding_session_id",
            existing_type=sa.Uuid(),
            nullable=False,
        )
        batch.create_foreign_key(
            op.f(
                "fk_apple_health_import_jobs_onboarding_session_id_onboarding_sessions"
            ),
            "onboarding_sessions",
            ["onboarding_session_id"],
            ["id"],
            ondelete="CASCADE",
        )

    with op.batch_alter_table("activities") as batch:
        batch.drop_constraint(
            op.f("ck_activities_average_cadence_nonnegative"),
            type_="check",
        )
        batch.drop_constraint(
            op.f("ck_activities_heart_rate_source"),
            type_="check",
        )
        batch.drop_column("route_points")
        batch.drop_column("average_cadence")
        batch.drop_column("average_heart_rate_source")

    old_onboarding_steps = tuple(
        step for step in ONBOARDING_STEPS if not step.startswith("FILE_IMPORT_")
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
    old_baseline_sources = tuple(
        source for source in BASELINE_SOURCES if source != "FILE_IMPORT"
    )
    _replace_enum_check(
        "athlete_baselines",
        "source",
        "ck_athlete_baselines_athlete_baseline_source",
        old_baseline_sources,
        length=32,
    )
    _replace_enum_check(
        "baseline_preferences",
        "selected_source",
        "ck_baseline_preferences_baseline_source",
        old_baseline_sources,
        length=32,
    )
    _replace_enum_check(
        "heart_rate_observations",
        "temporal_quality",
        "ck_heart_rate_observations_heart_rate_observation_quality",
        tuple(
            quality for quality in HEART_RATE_TEMPORAL_QUALITIES if quality != "MANUAL"
        ),
        length=24,
    )
    _replace_enum_check(
        "activities",
        "heart_rate_quality",
        "ck_activities_heart_rate_temporal_quality",
        tuple(
            quality for quality in HEART_RATE_TEMPORAL_QUALITIES if quality != "MANUAL"
        ),
        length=24,
    )
    _replace_enum_check(
        "activities",
        "source",
        "ck_activities_activity_source",
        ("STRAVA", "APPLE_HEALTH"),
        length=16,
    )
