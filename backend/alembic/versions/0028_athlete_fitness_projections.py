"""Add immutable athlete baseline assessments.

Revision ID: 0028_athlete_fitness_projections
Revises: 0027_catalog_option_standard
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0028_athlete_fitness_projections"
down_revision: str | None = "0027_catalog_option_standard"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def _discipline() -> sa.Enum:
    return sa.Enum(
        "RUNNING",
        "CYCLING",
        "HIKING",
        "SWIMMING",
        "STRENGTH",
        "OTHER",
        name="athlete_fitness_discipline",
        native_enum=False,
        create_constraint=True,
        length=16,
    )


def _source() -> sa.Enum:
    return sa.Enum(
        "IMPORTED_WORKOUT_WINDOW",
        name="fitness_baseline_source",
        native_enum=False,
        create_constraint=True,
        length=32,
    )


def _evidence_columns() -> tuple[sa.Column[object], ...]:
    return (
        sa.Column("source", _source(), nullable=False),
        sa.Column("analysis_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("analysis_ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("calculated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("session_count", sa.Integer(), nullable=False),
        sa.Column("active_day_count", sa.SmallInteger(), nullable=False),
        sa.Column("total_duration_seconds", sa.Integer(), nullable=False),
        sa.Column("known_distance_meters", sa.Float(), nullable=True),
        sa.Column("distance_session_count", sa.SmallInteger(), nullable=False),
        sa.Column("longest_duration_seconds", sa.Integer(), nullable=True),
        sa.Column("longest_distance_meters", sa.Float(), nullable=True),
        sa.Column("total_calories_kcal", sa.Float(), nullable=True),
        sa.Column("reliable_hr_sample_count", sa.Integer(), nullable=False),
        sa.Column("reliable_average_hr_bpm", sa.Float(), nullable=True),
        sa.Column("reliable_max_hr_bpm", sa.Float(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("discipline_metrics_jsonb", _JSON, nullable=False),
        sa.Column("evidence_summary_jsonb", _JSON, nullable=False),
        sa.Column("quality_flags_jsonb", _JSON, nullable=False),
        sa.Column(
            "source_workout_through_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "input_updated_through_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("input_digest", sa.CHAR(length=64), nullable=False),
        sa.Column("calculation_version", sa.Integer(), nullable=False),
    )


def _evidence_constraints(table: str) -> tuple[sa.CheckConstraint, ...]:
    return (
        sa.CheckConstraint(
            "analysis_ended_at >= analysis_started_at",
            name=op.f(f"ck_{table}_analysis_window_order"),
        ),
        sa.CheckConstraint(
            "session_count >= 0",
            name=op.f(f"ck_{table}_session_count_nonnegative"),
        ),
        sa.CheckConstraint(
            "active_day_count >= 0 AND active_day_count <= session_count",
            name=op.f(f"ck_{table}_active_day_count_range"),
        ),
        sa.CheckConstraint(
            "total_duration_seconds >= 0",
            name=op.f(f"ck_{table}_total_duration_nonnegative"),
        ),
        sa.CheckConstraint(
            "known_distance_meters IS NULL OR known_distance_meters >= 0",
            name=op.f(f"ck_{table}_known_distance_nonnegative"),
        ),
        sa.CheckConstraint(
            "distance_session_count >= 0 AND distance_session_count <= session_count",
            name=op.f(f"ck_{table}_distance_session_count_range"),
        ),
        sa.CheckConstraint(
            "longest_duration_seconds IS NULL OR longest_duration_seconds >= 0",
            name=op.f(f"ck_{table}_longest_duration_nonnegative"),
        ),
        sa.CheckConstraint(
            "longest_distance_meters IS NULL OR longest_distance_meters >= 0",
            name=op.f(f"ck_{table}_longest_distance_nonnegative"),
        ),
        sa.CheckConstraint(
            "total_calories_kcal IS NULL OR total_calories_kcal >= 0",
            name=op.f(f"ck_{table}_total_calories_nonnegative"),
        ),
        sa.CheckConstraint(
            "reliable_hr_sample_count >= 0",
            name=op.f(f"ck_{table}_reliable_hr_sample_count_nonnegative"),
        ),
        sa.CheckConstraint(
            "reliable_average_hr_bpm IS NULL OR reliable_average_hr_bpm > 0",
            name=op.f(f"ck_{table}_reliable_average_hr_positive"),
        ),
        sa.CheckConstraint(
            "reliable_max_hr_bpm IS NULL OR reliable_max_hr_bpm > 0",
            name=op.f(f"ck_{table}_reliable_max_hr_positive"),
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name=op.f(f"ck_{table}_confidence_range"),
        ),
        sa.CheckConstraint(
            "calculation_version > 0",
            name=op.f(f"ck_{table}_calculation_version_positive"),
        ),
    )


def upgrade() -> None:
    with op.batch_alter_table("workouts") as batch:
        batch.add_column(
            sa.Column(
                "fitness_input_updated_at",
                sa.DateTime(timezone=True),
                nullable=True,
            )
        )
    op.execute(
        "UPDATE workouts "
        "SET fitness_input_updated_at = updated_at "
        "WHERE fitness_input_updated_at IS NULL"
    )
    with op.batch_alter_table("workouts") as batch:
        batch.alter_column("fitness_input_updated_at", nullable=False)
    op.create_index(
        "ix_workouts_athlete_discipline_started_at",
        "workouts",
        ["athlete_id", "discipline", "started_at"],
        unique=False,
    )

    op.create_table(
        "athlete_baseline_assessments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("athlete_id", sa.Uuid(), nullable=False),
        sa.Column("discipline", _discipline(), nullable=False),
        *_evidence_columns(),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        *_evidence_constraints("athlete_baseline_assessments"),
        sa.ForeignKeyConstraint(
            ["athlete_id"],
            ["users.id"],
            name=op.f("fk_athlete_baseline_assessments_athlete_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_athlete_baseline_assessments")),
        sa.UniqueConstraint(
            "athlete_id",
            "discipline",
            name="uq_athlete_baseline_assessments_athlete_discipline",
        ),
    )


def downgrade() -> None:
    # This revision was only used locally while it briefly also created current
    # fitness and snapshot projections.  Make the requested local downgrade
    # clean if that pre-simplification form was applied before this migration
    # file was replaced.  Fresh upgrades never create these tables.
    bind = op.get_bind()
    for stale_table in (
        "athlete_fitness_snapshots",
        "athlete_current_fitness",
    ):
        if bind.dialect.has_table(bind, stale_table):
            op.drop_table(stale_table)
    op.drop_table("athlete_baseline_assessments")
    op.drop_index(
        "ix_workouts_athlete_discipline_started_at",
        table_name="workouts",
    )
    with op.batch_alter_table("workouts") as batch:
        batch.drop_column("fitness_input_updated_at")
