"""Split universal workouts from discipline-specific detail records.

Revision ID: 0004_discipline_workout_models
Revises: 0003_unified_training_import
Create Date: 2026-07-30
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0004_discipline_workout_models"
down_revision: str | Sequence[str] | None = "0003_unified_training_import"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_DISCIPLINES = ("RUN", "RIDE", "SWIM", "STRENGTH", "WALK_HIKE", "OTHER")
NEW_DISCIPLINES = (
    "RUNNING",
    "CYCLING",
    "HIKING",
    "SWIMMING",
    "STRENGTH",
    "OTHER",
)
DISCIPLINE_UPGRADE_PAIRS = (
    ("RUN", "RUNNING"),
    ("RIDE", "CYCLING"),
    ("WALK_HIKE", "HIKING"),
    ("SWIM", "SWIMMING"),
    ("STRENGTH", "STRENGTH"),
    ("OTHER", "OTHER"),
)
OLD_SOURCES = ("STRAVA", "APPLE_HEALTH", "TCX")
NEW_SOURCES = (
    "MANUAL",
    "STRAVA",
    "APPLE_HEALTH",
    "TCX",
    "FIT",
    "OTHER_IMPORT",
)
HEART_RATE_SOURCES = (
    "MEASURED_SENSOR",
    "PROVIDER_SUMMARY",
    "DERIVED",
    "USER_REPORTED",
    "UNAVAILABLE",
)
HEART_RATE_QUALITIES = (
    "EXACT_SAMPLE",
    "SHORT_INTERVAL",
    "COARSE_INTERVAL",
    "MANUAL",
    "UNKNOWN",
)
WORKOUT_FLOW_STEPS = (
    "WAITING_FOR_FILE",
    "HR_OFFER",
    "HR_ENTRY",
    "HR_CONFIRM",
    "RPE",
    "MOBILITY",
    "DISCOMFORT",
    "BODY_AREA",
    "DESCRIPTION_ENTRY",
    "DESCRIPTION_CONFIRM",
    "SEVERITY",
    "COMPLETE",
    "CANCELLED",
)
OLD_WORKOUT_FLOW_STEPS = tuple(
    value for value in WORKOUT_FLOW_STEPS if value != "MOBILITY"
)

_SUPPORT_TABLES = (
    "apple_health_import_jobs",
    "activity_source_links",
    "activity_feedback",
    "workout_flow_sessions",
    "heart_rate_observations",
)
_MAIN_DETAIL_TABLES = (
    "running_workout_details",
    "cycling_workout_details",
    "hiking_workout_details",
    "swimming_workout_details",
    "strength_workout_details",
    "other_workout_details",
)
_MIGRATION_PROVENANCE_KEYS = {
    "migration_revision",
    "legacy_activity",
    "canonical_snapshot",
}
_CANONICAL_SNAPSHOT_KEYS = {
    "workout",
    "main_detail_table",
    "main_detail",
    "pool_detail",
    "source_links",
}
_BACKUP_PREFIX = "_0004_legacy_"
_POOL_LENGTH_PATTERN = re.compile(
    r"(?<![0-9])([0-9]+(?:\.[0-9]+)?)\s*"
    r"(?:m|meter|meters|metre|metres)\b",
    flags=re.IGNORECASE,
)


def _enum(
    values: Sequence[str],
    *,
    name: str,
    length: int,
) -> sa.Enum:
    return sa.Enum(
        *values,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=length,
    )


def _json_document() -> sa.JSON:
    return sa.JSON().with_variant(
        postgresql.JSONB(astext_type=sa.Text()),
        "postgresql",
    )


def _enum_value(value: object) -> str:
    enum_value = getattr(value, "value", value)
    return str(enum_value)


def _json_safe(value: object) -> object:
    if isinstance(value, (date, datetime, uuid.UUID)):
        return value.isoformat() if not isinstance(value, uuid.UUID) else str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _decoded_json(value: object) -> object:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


def _metadata_for_activity(activity: Mapping[str, object]) -> dict[str, object]:
    legacy = {
        "legacy_user_id": activity["user_id"],
        "legacy_source": _enum_value(activity["source"]),
        "legacy_external_id": activity["external_id"],
        "legacy_sport": _enum_value(activity["sport"]),
        "legacy_source_sport_type": activity["source_sport_type"],
        "legacy_name": activity["name"],
        "legacy_started_at": activity["started_at"],
        "legacy_ended_at": activity["ended_at"],
        "legacy_timezone": activity["timezone"],
        "legacy_duration_seconds": activity["duration_seconds"],
        "legacy_moving_time_seconds": activity["moving_time_seconds"],
        "legacy_distance_meters": activity["distance_meters"],
        "legacy_elevation_gain_meters": activity["elevation_gain_meters"],
        "legacy_calories_kcal": activity["calories_kcal"],
        "legacy_average_heart_rate": activity["average_heart_rate"],
        "legacy_average_heart_rate_source": _enum_value(
            activity["average_heart_rate_source"]
        ),
        "legacy_max_heart_rate": activity["max_heart_rate"],
        "legacy_heart_rate_sample_count": activity["heart_rate_sample_count"],
        "legacy_heart_rate_quality": _enum_value(activity["heart_rate_quality"]),
        "legacy_heart_rate_reliable": activity["heart_rate_reliable"],
        "legacy_average_cadence": activity["average_cadence"],
        "legacy_route_points": _decoded_json(activity["route_points"]),
        "legacy_average_speed_mps": activity["average_speed"],
        "legacy_average_watts": activity["average_watts"],
        "legacy_trainer": activity["trainer"],
        "legacy_commute": activity["commute"],
        "legacy_manual": activity["manual"],
        "legacy_raw_summary": _decoded_json(activity["raw_summary"]),
        "legacy_deleted_at": activity["deleted_at"],
    }
    return {
        "migration_revision": revision,
        "legacy_activity": _json_safe(legacy),
    }


def _source_text(activity: Mapping[str, object]) -> str:
    raw = _decoded_json(activity["raw_summary"])
    raw_text = json.dumps(_json_safe(raw), sort_keys=True) if raw is not None else ""
    return f"{activity['source_sport_type']} {activity['name']} {raw_text}".casefold()


def _nested_values(value: object) -> Iterable[tuple[str, object]]:
    if isinstance(value, Mapping):
        for key, item in value.items():
            yield str(key), item
            yield from _nested_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from _nested_values(item)


def _pool_length_from_activity(activity: Mapping[str, object]) -> float | None:
    raw_summary = _decoded_json(activity["raw_summary"])
    for key, value in _nested_values(raw_summary):
        normalized_key = re.sub(r"[^a-z0-9]", "", key.casefold())
        if normalized_key not in {
            "poollength",
            "poollengthmeters",
            "laplength",
            "laplengthmeters",
        }:
            continue
        candidate: object = value
        if isinstance(candidate, Mapping):
            candidate = candidate.get("value")
        try:
            numeric = float(candidate)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if numeric > 0:
            return numeric
    match = _POOL_LENGTH_PATTERN.search(_source_text(activity))
    if match is None:
        return None
    numeric = float(match.group(1))
    return numeric if numeric > 0 else None


def _swimming_backfill(
    activity: Mapping[str, object],
) -> tuple[str, float | None, str | None]:
    text = _source_text(activity)
    pool_evidence = any(
        token in text
        for token in (
            "pool",
            "lap swim",
            "lap swimming",
        )
    )
    open_water_evidence = any(
        token in text
        for token in (
            "open water",
            "openwater",
            "outdoor swim",
            "sea swim",
            "ocean swim",
        )
    )
    if pool_evidence and not open_water_evidence:
        pool_length = _pool_length_from_activity(activity)
        if pool_length is not None:
            return "POOL", pool_length, None
        return "", None, "pool_length_unavailable"
    if open_water_evidence and not pool_evidence:
        return "OPEN_WATER", None, None
    return "", None, "ambiguous_swimming_environment"


def _pace(
    moving_seconds: object,
    distance_meters: object,
    *,
    distance_unit_meters: float,
) -> float | None:
    if moving_seconds is None or distance_meters is None:
        return None
    moving = float(moving_seconds)
    distance = float(distance_meters)
    if moving <= 0 or distance <= 0:
        return None
    return round(moving * distance_unit_meters / distance, 4)


def _running_type(activity: Mapping[str, object]) -> str:
    text = _source_text(activity)
    if "trail" in text:
        return "TRAIL"
    if "track" in text:
        return "TRACK"
    if any(token in text for token in ("treadmill", "virtual", "indoor")):
        return "TREADMILL"
    return "OUTDOOR"


def _cycling_type(activity: Mapping[str, object]) -> str:
    text = _source_text(activity)
    if bool(activity["trainer"]) or any(
        token in text for token in ("stationary", "virtual", "indoor", "trainer")
    ):
        return "STATIONARY"
    if any(token in text for token in ("mountain", "mtb")):
        return "MTB"
    if "gravel" in text:
        return "GRAVEL"
    if any(token in text for token in ("road", "ride", "cycling", "bike")):
        return "ROAD"
    return "OTHER"


def _hiking_type(activity: Mapping[str, object]) -> str:
    text = _source_text(activity)
    if "snowshoe" in text:
        return "SNOWSHOEING"
    if any(token in text for token in ("mountaineer", "alpine")):
        return "MOUNTAINEERING"
    if "trek" in text:
        return "TREKKING"
    if any(token in text for token in ("hike", "hiking", "walk", "walking")):
        return "HIKING"
    return "OTHER"


def _strength_type(activity: Mapping[str, object]) -> str:
    text = _source_text(activity)
    if any(token in text for token in ("calisthenic", "bodyweight")):
        return "CALISTHENICS"
    if any(
        token in text for token in ("gym", "weight", "strength", "crossfit", "workout")
    ):
        return "GYM"
    return "OTHER"


def _other_activity_name(activity: Mapping[str, object]) -> str:
    name = str(activity["name"]).strip()
    if name:
        return name[:255]
    raw_sport = str(activity["source_sport_type"]).strip()
    if raw_sport and raw_sport.casefold() != "unknown":
        readable = re.sub(r"[_-]+", " ", raw_sport).strip()
        return readable[:1].upper() + readable[1:255]
    return "Other activity"


def _bulk_insert(
    bind: sa.Connection,
    table: sa.Table,
    rows: list[dict[str, object]],
    *,
    chunk_size: int = 500,
) -> None:
    for offset in range(0, len(rows), chunk_size):
        bind.execute(table.insert(), rows[offset : offset + chunk_size])


def _backup_name(table: str) -> str:
    return f"{_BACKUP_PREFIX}{table}"


def _backup_support_tables() -> None:
    for table in _SUPPORT_TABLES:
        op.execute(
            sa.text(f"CREATE TABLE {_backup_name(table)} AS SELECT * FROM {table}")
        )


def _drop_support_tables() -> None:
    op.drop_table("activity_source_links")
    op.drop_table("activity_feedback")
    op.drop_table("workout_flow_sessions")
    op.drop_table("heart_rate_observations")
    op.drop_table("apple_health_import_jobs")


def _drop_support_backups() -> None:
    for table in reversed(_SUPPORT_TABLES):
        op.drop_table(_backup_name(table))


def _create_workouts_table() -> None:
    op.create_table(
        "workouts",
        sa.Column("athlete_id", sa.Uuid(), nullable=False),
        sa.Column(
            "discipline",
            _enum(NEW_DISCIPLINES, name="workout_discipline", length=16),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_seconds", sa.Integer(), nullable=False),
        sa.Column(
            "source",
            _enum(NEW_SOURCES, name="workout_source", length=16),
            nullable=False,
        ),
        sa.Column("external_id", sa.String(length=128), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "duration_seconds > 0",
            name=op.f("ck_workouts_duration_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["athlete_id"],
            ["users.id"],
            name=op.f("fk_workouts_athlete_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workouts")),
        sa.UniqueConstraint(
            "athlete_id",
            "source",
            "external_id",
            name="uq_workouts_athlete_source_external_id",
        ),
    )
    op.create_index(
        "ix_workouts_athlete_started_at",
        "workouts",
        ["athlete_id", "started_at"],
        unique=False,
    )


def _create_detail_tables() -> None:
    op.create_table(
        "running_workout_details",
        sa.Column("workout_id", sa.Uuid(), nullable=False),
        sa.Column(
            "running_type",
            _enum(
                ("OUTDOOR", "TRAIL", "TRACK", "TREADMILL"),
                name="running_type",
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("distance_meters", sa.Float(), nullable=True),
        sa.Column("moving_duration_seconds", sa.Integer(), nullable=True),
        sa.Column("average_pace_seconds_per_km", sa.Float(), nullable=True),
        sa.Column("elevation_gain_meters", sa.Float(), nullable=True),
        sa.Column("elevation_loss_meters", sa.Float(), nullable=True),
        sa.Column("average_heart_rate", sa.Float(), nullable=True),
        sa.Column("max_heart_rate", sa.Float(), nullable=True),
        sa.Column("average_cadence_spm", sa.Float(), nullable=True),
        sa.Column("max_cadence_spm", sa.Float(), nullable=True),
        sa.CheckConstraint(
            "distance_meters IS NULL OR distance_meters >= 0",
            name=op.f("ck_running_workout_details_distance_nonnegative"),
        ),
        sa.CheckConstraint(
            "moving_duration_seconds IS NULL OR moving_duration_seconds >= 0",
            name=op.f("ck_running_workout_details_moving_duration_nonnegative"),
        ),
        sa.CheckConstraint(
            "average_pace_seconds_per_km IS NULL OR average_pace_seconds_per_km >= 0",
            name=op.f("ck_running_workout_details_average_pace_nonnegative"),
        ),
        sa.CheckConstraint(
            "elevation_gain_meters IS NULL OR elevation_gain_meters >= 0",
            name=op.f("ck_running_workout_details_elevation_gain_nonnegative"),
        ),
        sa.CheckConstraint(
            "elevation_loss_meters IS NULL OR elevation_loss_meters >= 0",
            name=op.f("ck_running_workout_details_elevation_loss_nonnegative"),
        ),
        sa.CheckConstraint(
            "average_heart_rate IS NULL OR average_heart_rate >= 0",
            name=op.f("ck_running_workout_details_average_heart_rate_nonnegative"),
        ),
        sa.CheckConstraint(
            "max_heart_rate IS NULL OR max_heart_rate >= 0",
            name=op.f("ck_running_workout_details_max_heart_rate_nonnegative"),
        ),
        sa.CheckConstraint(
            "average_cadence_spm IS NULL OR average_cadence_spm >= 0",
            name=op.f("ck_running_workout_details_average_cadence_nonnegative"),
        ),
        sa.CheckConstraint(
            "max_cadence_spm IS NULL OR max_cadence_spm >= 0",
            name=op.f("ck_running_workout_details_max_cadence_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["workout_id"],
            ["workouts.id"],
            name=op.f("fk_running_workout_details_workout_id_workouts"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "workout_id",
            name=op.f("pk_running_workout_details"),
        ),
    )
    op.create_table(
        "cycling_workout_details",
        sa.Column("workout_id", sa.Uuid(), nullable=False),
        sa.Column(
            "cycling_type",
            _enum(
                ("ROAD", "MTB", "GRAVEL", "STATIONARY", "OTHER"),
                name="cycling_type",
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("distance_meters", sa.Float(), nullable=True),
        sa.Column("moving_duration_seconds", sa.Integer(), nullable=True),
        sa.Column("average_speed_kph", sa.Float(), nullable=True),
        sa.Column("max_speed_kph", sa.Float(), nullable=True),
        sa.Column("elevation_gain_meters", sa.Float(), nullable=True),
        sa.Column("elevation_loss_meters", sa.Float(), nullable=True),
        sa.Column("average_heart_rate", sa.Float(), nullable=True),
        sa.Column("max_heart_rate", sa.Float(), nullable=True),
        sa.Column("average_cadence_rpm", sa.Float(), nullable=True),
        sa.Column("max_cadence_rpm", sa.Float(), nullable=True),
        sa.CheckConstraint(
            "distance_meters IS NULL OR distance_meters >= 0",
            name=op.f("ck_cycling_workout_details_distance_nonnegative"),
        ),
        sa.CheckConstraint(
            "moving_duration_seconds IS NULL OR moving_duration_seconds >= 0",
            name=op.f("ck_cycling_workout_details_moving_duration_nonnegative"),
        ),
        sa.CheckConstraint(
            "average_speed_kph IS NULL OR average_speed_kph >= 0",
            name=op.f("ck_cycling_workout_details_average_speed_nonnegative"),
        ),
        sa.CheckConstraint(
            "max_speed_kph IS NULL OR max_speed_kph >= 0",
            name=op.f("ck_cycling_workout_details_max_speed_nonnegative"),
        ),
        sa.CheckConstraint(
            "elevation_gain_meters IS NULL OR elevation_gain_meters >= 0",
            name=op.f("ck_cycling_workout_details_elevation_gain_nonnegative"),
        ),
        sa.CheckConstraint(
            "elevation_loss_meters IS NULL OR elevation_loss_meters >= 0",
            name=op.f("ck_cycling_workout_details_elevation_loss_nonnegative"),
        ),
        sa.CheckConstraint(
            "average_heart_rate IS NULL OR average_heart_rate >= 0",
            name=op.f("ck_cycling_workout_details_average_heart_rate_nonnegative"),
        ),
        sa.CheckConstraint(
            "max_heart_rate IS NULL OR max_heart_rate >= 0",
            name=op.f("ck_cycling_workout_details_max_heart_rate_nonnegative"),
        ),
        sa.CheckConstraint(
            "average_cadence_rpm IS NULL OR average_cadence_rpm >= 0",
            name=op.f("ck_cycling_workout_details_average_cadence_nonnegative"),
        ),
        sa.CheckConstraint(
            "max_cadence_rpm IS NULL OR max_cadence_rpm >= 0",
            name=op.f("ck_cycling_workout_details_max_cadence_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["workout_id"],
            ["workouts.id"],
            name=op.f("fk_cycling_workout_details_workout_id_workouts"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "workout_id",
            name=op.f("pk_cycling_workout_details"),
        ),
    )
    op.create_table(
        "hiking_workout_details",
        sa.Column("workout_id", sa.Uuid(), nullable=False),
        sa.Column(
            "hiking_type",
            _enum(
                ("HIKING", "TREKKING", "MOUNTAINEERING", "SNOWSHOEING", "OTHER"),
                name="hiking_type",
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("distance_meters", sa.Float(), nullable=True),
        sa.Column("moving_duration_seconds", sa.Integer(), nullable=True),
        sa.Column("average_pace_seconds_per_km", sa.Float(), nullable=True),
        sa.Column("elevation_gain_meters", sa.Float(), nullable=True),
        sa.Column("elevation_loss_meters", sa.Float(), nullable=True),
        sa.Column("average_heart_rate", sa.Float(), nullable=True),
        sa.Column("max_heart_rate", sa.Float(), nullable=True),
        sa.Column("pack_weight_kg", sa.Float(), nullable=True),
        sa.CheckConstraint(
            "distance_meters IS NULL OR distance_meters >= 0",
            name=op.f("ck_hiking_workout_details_distance_nonnegative"),
        ),
        sa.CheckConstraint(
            "moving_duration_seconds IS NULL OR moving_duration_seconds >= 0",
            name=op.f("ck_hiking_workout_details_moving_duration_nonnegative"),
        ),
        sa.CheckConstraint(
            "average_pace_seconds_per_km IS NULL OR average_pace_seconds_per_km >= 0",
            name=op.f("ck_hiking_workout_details_average_pace_nonnegative"),
        ),
        sa.CheckConstraint(
            "elevation_gain_meters IS NULL OR elevation_gain_meters >= 0",
            name=op.f("ck_hiking_workout_details_elevation_gain_nonnegative"),
        ),
        sa.CheckConstraint(
            "elevation_loss_meters IS NULL OR elevation_loss_meters >= 0",
            name=op.f("ck_hiking_workout_details_elevation_loss_nonnegative"),
        ),
        sa.CheckConstraint(
            "average_heart_rate IS NULL OR average_heart_rate >= 0",
            name=op.f("ck_hiking_workout_details_average_heart_rate_nonnegative"),
        ),
        sa.CheckConstraint(
            "max_heart_rate IS NULL OR max_heart_rate >= 0",
            name=op.f("ck_hiking_workout_details_max_heart_rate_nonnegative"),
        ),
        sa.CheckConstraint(
            "pack_weight_kg IS NULL OR pack_weight_kg >= 0",
            name=op.f("ck_hiking_workout_details_pack_weight_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["workout_id"],
            ["workouts.id"],
            name=op.f("fk_hiking_workout_details_workout_id_workouts"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "workout_id",
            name=op.f("pk_hiking_workout_details"),
        ),
    )
    op.create_table(
        "swimming_workout_details",
        sa.Column("workout_id", sa.Uuid(), nullable=False),
        sa.Column(
            "swimming_environment",
            _enum(
                ("POOL", "OPEN_WATER"),
                name="swimming_environment",
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("distance_meters", sa.Float(), nullable=True),
        sa.Column("moving_duration_seconds", sa.Integer(), nullable=True),
        sa.Column("average_pace_seconds_per_100m", sa.Float(), nullable=True),
        sa.Column("average_heart_rate", sa.Float(), nullable=True),
        sa.Column("max_heart_rate", sa.Float(), nullable=True),
        sa.CheckConstraint(
            "distance_meters IS NULL OR distance_meters >= 0",
            name=op.f("ck_swimming_workout_details_distance_nonnegative"),
        ),
        sa.CheckConstraint(
            "moving_duration_seconds IS NULL OR moving_duration_seconds >= 0",
            name=op.f("ck_swimming_workout_details_moving_duration_nonnegative"),
        ),
        sa.CheckConstraint(
            "average_pace_seconds_per_100m IS NULL "
            "OR average_pace_seconds_per_100m >= 0",
            name=op.f("ck_swimming_workout_details_average_pace_nonnegative"),
        ),
        sa.CheckConstraint(
            "average_heart_rate IS NULL OR average_heart_rate >= 0",
            name=op.f("ck_swimming_workout_details_average_heart_rate_nonnegative"),
        ),
        sa.CheckConstraint(
            "max_heart_rate IS NULL OR max_heart_rate >= 0",
            name=op.f("ck_swimming_workout_details_max_heart_rate_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["workout_id"],
            ["workouts.id"],
            name=op.f("fk_swimming_workout_details_workout_id_workouts"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "workout_id",
            name=op.f("pk_swimming_workout_details"),
        ),
    )
    op.create_table(
        "pool_swimming_details",
        sa.Column("workout_id", sa.Uuid(), nullable=False),
        sa.Column("pool_length_meters", sa.Float(), nullable=False),
        sa.Column("total_lengths", sa.Integer(), nullable=True),
        sa.Column(
            "primary_stroke",
            _enum(
                (
                    "FREESTYLE",
                    "BREASTSTROKE",
                    "BACKSTROKE",
                    "BUTTERFLY",
                    "MIXED",
                    "OTHER",
                ),
                name="swimming_stroke",
                length=16,
            ),
            nullable=True,
        ),
        sa.Column("average_swolf", sa.Float(), nullable=True),
        sa.Column("total_strokes", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "pool_length_meters > 0",
            name=op.f("ck_pool_swimming_details_pool_length_positive"),
        ),
        sa.CheckConstraint(
            "total_lengths IS NULL OR total_lengths >= 0",
            name=op.f("ck_pool_swimming_details_total_lengths_nonnegative"),
        ),
        sa.CheckConstraint(
            "average_swolf IS NULL OR average_swolf >= 0",
            name=op.f("ck_pool_swimming_details_average_swolf_nonnegative"),
        ),
        sa.CheckConstraint(
            "total_strokes IS NULL OR total_strokes >= 0",
            name=op.f("ck_pool_swimming_details_total_strokes_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["workout_id"],
            ["swimming_workout_details.workout_id"],
            name=op.f("fk_pool_swimming_details_workout_id_swimming_workout_details"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workout_id"],
            ["workouts.id"],
            name=op.f("fk_pool_swimming_details_workout_id_workouts"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "workout_id",
            name=op.f("pk_pool_swimming_details"),
        ),
    )
    op.create_table(
        "strength_workout_details",
        sa.Column("workout_id", sa.Uuid(), nullable=False),
        sa.Column(
            "strength_type",
            _enum(
                ("GYM", "CALISTHENICS", "OTHER"),
                name="strength_type",
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("session_focus", sa.String(length=255), nullable=True),
        sa.Column("exercises_jsonb", _json_document(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workout_id"],
            ["workouts.id"],
            name=op.f("fk_strength_workout_details_workout_id_workouts"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "workout_id",
            name=op.f("pk_strength_workout_details"),
        ),
    )
    op.create_table(
        "other_workout_details",
        sa.Column("workout_id", sa.Uuid(), nullable=False),
        sa.Column("activity_name", sa.String(length=255), nullable=False),
        sa.Column("activity_description", sa.Text(), nullable=True),
        sa.Column("raw_sport", sa.String(length=128), nullable=True),
        sa.Column("raw_sub_sport", sa.String(length=128), nullable=True),
        sa.Column("distance_meters", sa.Float(), nullable=True),
        sa.Column("average_heart_rate", sa.Float(), nullable=True),
        sa.Column("max_heart_rate", sa.Float(), nullable=True),
        sa.Column("metrics_jsonb", _json_document(), nullable=True),
        sa.CheckConstraint(
            "distance_meters IS NULL OR distance_meters >= 0",
            name=op.f("ck_other_workout_details_distance_nonnegative"),
        ),
        sa.CheckConstraint(
            "average_heart_rate IS NULL OR average_heart_rate >= 0",
            name=op.f("ck_other_workout_details_average_heart_rate_nonnegative"),
        ),
        sa.CheckConstraint(
            "max_heart_rate IS NULL OR max_heart_rate >= 0",
            name=op.f("ck_other_workout_details_max_heart_rate_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["workout_id"],
            ["workouts.id"],
            name=op.f("fk_other_workout_details_workout_id_workouts"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "workout_id",
            name=op.f("pk_other_workout_details"),
        ),
    )


def _drop_detail_tables() -> None:
    op.drop_table("pool_swimming_details")
    op.drop_table("other_workout_details")
    op.drop_table("strength_workout_details")
    op.drop_table("swimming_workout_details")
    op.drop_table("hiking_workout_details")
    op.drop_table("cycling_workout_details")
    op.drop_table("running_workout_details")


def _create_import_jobs_table(*, workout_column: bool) -> None:
    linked_column = "workout_id" if workout_column else "activity_id"
    linked_table = "workouts" if workout_column else "activities"
    op.create_table(
        "apple_health_import_jobs",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("onboarding_session_id", sa.Uuid(), nullable=True),
        sa.Column(linked_column, sa.Uuid(), nullable=True),
        sa.Column("telegram_update_id", sa.BigInteger(), nullable=True),
        sa.Column("telegram_file_id", sa.String(length=255), nullable=False),
        sa.Column(
            "telegram_file_unique_id",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column("display_filename", sa.String(length=255), nullable=False),
        sa.Column("temporary_path", sa.String(length=1024), nullable=True),
        sa.Column("file_sha256", sa.String(length=64), nullable=True),
        sa.Column(
            "file_format",
            _enum(
                ("APPLE_HEALTH_ZIP", "TCX", "UNKNOWN"),
                name="training_file_format",
                length=24,
            ),
            server_default="APPLE_HEALTH_ZIP",
            nullable=False,
        ),
        sa.Column(
            "context",
            _enum(
                ("ONBOARDING", "DAILY"),
                name="training_import_context",
                length=16,
            ),
            server_default="ONBOARDING",
            nullable=False,
        ),
        sa.Column(
            "status",
            _enum(
                ("RECEIVED", "PROCESSING", "SUCCEEDED", "FAILED", "CANCELLED"),
                name="apple_health_import_status",
                length=16,
            ),
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
        sa.ForeignKeyConstraint(
            ["onboarding_session_id"],
            ["onboarding_sessions.id"],
            name=op.f(
                "fk_apple_health_import_jobs_onboarding_session_id_onboarding_sessions"
            ),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            [linked_column],
            [f"{linked_table}.id"],
            name=op.f(f"fk_apple_health_import_jobs_{linked_column}_{linked_table}"),
            ondelete="SET NULL",
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
        "ix_apple_health_import_jobs_user_file_sha256",
        "apple_health_import_jobs",
        ["user_id", "file_sha256"],
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


def _create_source_links_table(*, workout_column: bool) -> None:
    linked_column = "workout_id" if workout_column else "activity_id"
    linked_table = "workouts" if workout_column else "activities"
    columns: list[Any] = [
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(linked_column, sa.Uuid(), nullable=False),
        sa.Column(
            "source",
            _enum(
                NEW_SOURCES if workout_column else OLD_SOURCES,
                name="activity_source_link_source",
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("external_id", sa.String(length=128), nullable=False),
    ]
    if workout_column:
        columns.extend(
            [
                sa.Column("raw_sport", sa.String(length=128), nullable=True),
                sa.Column("raw_sub_sport", sa.String(length=128), nullable=True),
                sa.Column("source_metadata_jsonb", _json_document(), nullable=True),
                sa.Column(
                    "heart_rate_source",
                    _enum(
                        HEART_RATE_SOURCES,
                        name="source_link_heart_rate_source",
                        length=24,
                    ),
                    server_default="UNAVAILABLE",
                    nullable=False,
                ),
                sa.Column(
                    "heart_rate_quality",
                    _enum(
                        HEART_RATE_QUALITIES,
                        name="source_link_heart_rate_quality",
                        length=24,
                    ),
                    server_default="UNKNOWN",
                    nullable=False,
                ),
                sa.Column(
                    "heart_rate_reliable",
                    sa.Boolean(),
                    server_default=sa.text("false"),
                    nullable=False,
                ),
                sa.Column(
                    "heart_rate_sample_count",
                    sa.Integer(),
                    server_default="0",
                    nullable=False,
                ),
                sa.Column(
                    "deleted_at",
                    sa.DateTime(timezone=True),
                    nullable=True,
                ),
            ]
        )
    columns.extend(
        [
            sa.Column("file_sha256", sa.String(length=64), nullable=True),
            sa.Column("import_job_id", sa.Uuid(), nullable=True),
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                [linked_column],
                [f"{linked_table}.id"],
                name=op.f(f"fk_activity_source_links_{linked_column}_{linked_table}"),
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
            sa.PrimaryKeyConstraint(
                "id",
                name=op.f("pk_activity_source_links"),
            ),
            sa.UniqueConstraint(
                "user_id",
                "source",
                "external_id",
                name="uq_activity_source_links_user_source_external_id",
            ),
        ]
    )
    op.create_table("activity_source_links", *columns)
    op.create_index(
        (
            "ix_activity_source_links_workout_id"
            if workout_column
            else "ix_activity_source_links_activity_id"
        ),
        "activity_source_links",
        [linked_column],
        unique=False,
    )


def _create_feedback_table(*, workout_column: bool) -> None:
    linked_column = "workout_id" if workout_column else "activity_id"
    linked_table = "workouts" if workout_column else "activities"
    columns: list[Any] = [
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(linked_column, sa.Uuid(), nullable=False),
        sa.Column("manual_average_heart_rate", sa.Integer(), nullable=True),
        sa.Column("reported_rpe", sa.Integer(), nullable=True),
        sa.Column("reported_rpe_label", sa.String(length=32), nullable=True),
        sa.Column("reported_discomfort", sa.Boolean(), nullable=True),
    ]
    if workout_column:
        columns.append(sa.Column("mobility_done", sa.Boolean(), nullable=True))
    columns.extend(
        [
            sa.Column(
                "discomfort_body_area",
                _enum(
                    ("SHOULDER", "BACK", "HIP", "KNEE", "ANKLE_FOOT", "OTHER"),
                    name="activity_feedback_body_area",
                    length=16,
                ),
                nullable=True,
            ),
            sa.Column(
                "discomfort_severity",
                _enum(
                    ("MILD", "MODERATE", "SEVERE"),
                    name="discomfort_severity",
                    length=16,
                ),
                nullable=True,
            ),
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
            sa.ForeignKeyConstraint(
                [linked_column],
                [f"{linked_table}.id"],
                name=op.f(f"fk_activity_feedback_{linked_column}_{linked_table}"),
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["user_id"],
                ["users.id"],
                name=op.f("fk_activity_feedback_user_id_users"),
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint(
                "id",
                name=op.f("pk_activity_feedback"),
            ),
            sa.UniqueConstraint(
                "user_id",
                linked_column,
                name=(
                    "uq_activity_feedback_user_workout"
                    if workout_column
                    else "uq_activity_feedback_user_activity"
                ),
            ),
        ]
    )
    op.create_table("activity_feedback", *columns)


def _create_workout_flow_table(*, workout_column: bool) -> None:
    linked_column = "workout_id" if workout_column else "activity_id"
    linked_table = "workouts" if workout_column else "activities"
    op.create_table(
        "workout_flow_sessions",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(linked_column, sa.Uuid(), nullable=True),
        sa.Column(
            "state",
            _enum(
                WORKOUT_FLOW_STEPS if workout_column else OLD_WORKOUT_FLOW_STEPS,
                name="workout_flow_step",
                length=32,
            ),
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
            "pending_manual_average_heart_rate IS NULL "
            "OR (pending_manual_average_heart_rate >= 30 "
            "AND pending_manual_average_heart_rate <= 250)",
            name=op.f(
                "ck_workout_flow_sessions_pending_manual_average_heart_rate_range"
            ),
        ),
        sa.ForeignKeyConstraint(
            [linked_column],
            [f"{linked_table}.id"],
            name=op.f(f"fk_workout_flow_sessions_{linked_column}_{linked_table}"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_workout_flow_sessions_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name=op.f("pk_workout_flow_sessions"),
        ),
        sa.UniqueConstraint(
            "user_id",
            name="uq_workout_flow_sessions_user_id",
        ),
    )


def _create_heart_rate_observations_table(*, workout_column: bool) -> None:
    linked_column = "workout_id" if workout_column else "activity_id"
    linked_table = "workouts" if workout_column else "activities"
    op.create_table(
        "heart_rate_observations",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(linked_column, sa.Uuid(), nullable=False),
        sa.Column("source_record_key", sa.String(length=64), nullable=False),
        sa.Column("source_name", sa.String(length=255), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("beats_per_minute", sa.Float(), nullable=False),
        sa.Column(
            "temporal_quality",
            _enum(
                HEART_RATE_QUALITIES,
                name="heart_rate_observation_quality",
                length=24,
            ),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            [linked_column],
            [f"{linked_table}.id"],
            name=op.f(f"fk_heart_rate_observations_{linked_column}_{linked_table}"),
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
        (
            "ix_heart_rate_observations_workout_started"
            if workout_column
            else "ix_heart_rate_observations_activity_started"
        ),
        "heart_rate_observations",
        [linked_column, "started_at"],
        unique=False,
    )


def _create_support_tables(*, workout_column: bool) -> None:
    _create_import_jobs_table(workout_column=workout_column)
    _create_source_links_table(workout_column=workout_column)
    _create_feedback_table(workout_column=workout_column)
    _create_workout_flow_table(workout_column=workout_column)
    _create_heart_rate_observations_table(workout_column=workout_column)


def _create_legacy_activities_table() -> None:
    op.create_table(
        "activities",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "source",
            _enum(OLD_SOURCES, name="activity_source", length=16),
            nullable=False,
        ),
        sa.Column("external_id", sa.String(length=128), nullable=False),
        sa.Column(
            "sport",
            _enum(OLD_DISCIPLINES, name="discipline", length=16),
            nullable=False,
        ),
        sa.Column("source_sport_type", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("timezone", sa.String(length=128), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=False),
        sa.Column("moving_time_seconds", sa.Integer(), nullable=True),
        sa.Column("distance_meters", sa.Float(), nullable=True),
        sa.Column("elevation_gain_meters", sa.Float(), nullable=True),
        sa.Column("calories_kcal", sa.Float(), nullable=True),
        sa.Column("average_heart_rate", sa.Float(), nullable=True),
        sa.Column(
            "average_heart_rate_source",
            _enum(
                HEART_RATE_SOURCES,
                name="heart_rate_source",
                length=24,
            ),
            server_default="UNAVAILABLE",
            nullable=False,
        ),
        sa.Column("max_heart_rate", sa.Float(), nullable=True),
        sa.Column(
            "heart_rate_sample_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "heart_rate_quality",
            _enum(
                HEART_RATE_QUALITIES,
                name="heart_rate_temporal_quality",
                length=24,
            ),
            server_default="UNKNOWN",
            nullable=False,
        ),
        sa.Column(
            "heart_rate_reliable",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("average_cadence", sa.Float(), nullable=True),
        sa.Column("route_points", _json_document(), nullable=True),
        sa.Column("average_speed", sa.Float(), nullable=True),
        sa.Column("average_watts", sa.Float(), nullable=True),
        sa.Column(
            "trainer",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "commute",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "manual",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("raw_summary", _json_document(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "distance_meters IS NULL OR distance_meters >= 0",
            name=op.f("ck_activities_distance_nonnegative"),
        ),
        sa.CheckConstraint(
            "duration_seconds >= 0",
            name=op.f("ck_activities_duration_nonnegative"),
        ),
        sa.CheckConstraint(
            "elevation_gain_meters IS NULL OR elevation_gain_meters >= 0",
            name=op.f("ck_activities_elevation_nonnegative"),
        ),
        sa.CheckConstraint(
            "moving_time_seconds IS NULL OR moving_time_seconds >= 0",
            name=op.f("ck_activities_moving_time_nonnegative"),
        ),
        sa.CheckConstraint(
            "average_cadence IS NULL OR average_cadence >= 0",
            name=op.f("ck_activities_average_cadence_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_activities_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_activities")),
        sa.UniqueConstraint(
            "user_id",
            "source",
            "external_id",
            name="uq_activities_user_source_external_id",
        ),
    )
    op.create_index(
        "ix_activities_user_started_at",
        "activities",
        ["user_id", "started_at"],
        unique=False,
    )


def _quoted_values(values: Sequence[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _replace_baseline_disciplines(*, to_canonical: bool) -> None:
    constraint = "ck_discipline_baselines_baseline_discipline"
    with op.batch_alter_table("discipline_baselines") as batch:
        batch.drop_constraint(op.f(constraint), type_="check")

    pairs = (
        DISCIPLINE_UPGRADE_PAIRS
        if to_canonical
        else tuple(
            (canonical, legacy) for legacy, canonical in DISCIPLINE_UPGRADE_PAIRS
        )
    )
    cases = " ".join(
        f"WHEN '{old_value}' THEN '{new_value}'" for old_value, new_value in pairs
    )
    op.execute(
        sa.text(
            "UPDATE discipline_baselines "
            f"SET discipline = CASE discipline {cases} ELSE discipline END"
        )
    )

    accepted_values = NEW_DISCIPLINES if to_canonical else OLD_DISCIPLINES
    with op.batch_alter_table("discipline_baselines") as batch:
        batch.create_check_constraint(
            op.f(constraint),
            f"discipline IN ({_quoted_values(accepted_values)})",
        )


def _reflected_table(bind: sa.Connection, table_name: str) -> sa.Table:
    return sa.Table(table_name, sa.MetaData(), autoload_with=bind)


def _count(bind: sa.Connection, table: sa.Table) -> int:
    return int(bind.scalar(sa.select(sa.func.count()).select_from(table)) or 0)


def _canonical_snapshot_from_database(
    bind: sa.Connection,
    *,
    workout_id: object,
) -> dict[str, object]:
    workouts = _reflected_table(bind, "workouts")
    workout = (
        bind.execute(sa.select(workouts).where(workouts.c.id == workout_id))
        .mappings()
        .one()
    )
    main_details: list[tuple[str, Mapping[str, object]]] = []
    for table_name in _MAIN_DETAIL_TABLES:
        detail_table = _reflected_table(bind, table_name)
        detail = (
            bind.execute(
                sa.select(detail_table).where(detail_table.c.workout_id == workout_id)
            )
            .mappings()
            .one_or_none()
        )
        if detail is not None:
            main_details.append((table_name, detail))
    if len(main_details) != 1:
        raise RuntimeError(
            "Cannot create migration snapshot without exactly one main detail"
        )

    pool_table = _reflected_table(bind, "pool_swimming_details")
    pool_detail = (
        bind.execute(sa.select(pool_table).where(pool_table.c.workout_id == workout_id))
        .mappings()
        .one_or_none()
    )
    detail_table_name, main_detail = main_details[0]
    return {
        "workout": _json_safe(dict(workout)),
        "main_detail_table": detail_table_name,
        "main_detail": _json_safe(dict(main_detail)),
        "pool_detail": (
            _json_safe(dict(pool_detail)) if pool_detail is not None else None
        ),
    }


def _source_link_snapshot_from_database(
    bind: sa.Connection,
    *,
    workout_id: object,
) -> list[dict[str, object]]:
    source_links = _reflected_table(bind, "activity_source_links")
    rows = (
        bind.execute(
            sa.select(source_links)
            .where(source_links.c.workout_id == workout_id)
            .order_by(source_links.c.id)
        )
        .mappings()
        .all()
    )
    return [
        {
            str(key): _json_safe(value)
            for key, value in row.items()
            if key != "source_metadata_jsonb"
        }
        for row in rows
    ]


def _copy_support_rows(
    bind: sa.Connection,
    *,
    to_workout: bool,
) -> None:
    source_link_column = "activity_id" if to_workout else "workout_id"
    target_link_column = "workout_id" if to_workout else "activity_id"
    inspector = sa.inspect(bind)
    quote = bind.dialect.identifier_preparer.quote
    for table_name in _SUPPORT_TABLES:
        backup_name = _backup_name(table_name)
        source_names = [
            str(column["name"]) for column in inspector.get_columns(backup_name)
        ]
        target_names = {
            str(column["name"]) for column in inspector.get_columns(table_name)
        }
        selected_source_names: list[str] = []
        selected_target_names: list[str] = []
        for source_name in source_names:
            target_name = (
                target_link_column if source_name == source_link_column else source_name
            )
            if target_name in target_names:
                selected_source_names.append(source_name)
                selected_target_names.append(target_name)
        if not selected_source_names:
            continue
        target_columns = ", ".join(quote(name) for name in selected_target_names)
        source_columns = ", ".join(quote(name) for name in selected_source_names)
        bind.exec_driver_sql(
            f"INSERT INTO {quote(table_name)} ({target_columns}) "
            f"SELECT {source_columns} FROM {quote(backup_name)}"
        )


def _raw_sub_sport(activity: Mapping[str, object]) -> str | None:
    raw_summary = _decoded_json(activity["raw_summary"])
    for key, value in _nested_values(raw_summary):
        normalized_key = re.sub(r"[^a-z0-9]", "", key.casefold())
        if normalized_key not in {"subsport", "subsporttype"}:
            continue
        if isinstance(value, (str, int, float)):
            candidate = str(value).strip()
            if candidate:
                return candidate[:128]
    return None


def _nonnegative_float(value: object) -> float | None:
    if value is None:
        return None
    numeric = float(value)
    return numeric if numeric >= 0 else None


def _nonnegative_int(value: object) -> int | None:
    if value is None:
        return None
    numeric = int(value)
    return numeric if numeric >= 0 else None


def _cycling_speed_kph(activity: Mapping[str, object]) -> float | None:
    distance = _nonnegative_float(activity["distance_meters"])
    moving = _nonnegative_int(activity["moving_time_seconds"])
    if distance is not None and moving is not None and moving > 0:
        return round(distance * 3.6 / moving, 4)
    legacy_speed = _nonnegative_float(activity["average_speed"])
    if _enum_value(activity["source"]) == "STRAVA" and legacy_speed is not None:
        return round(legacy_speed * 3.6, 4)
    return None


def _backfill_workouts(
    bind: sa.Connection,
) -> tuple[list[dict[str, object]], dict[str, dict[str, object]]]:
    activities = _reflected_table(bind, "activities")
    workouts = _reflected_table(bind, "workouts")
    running = _reflected_table(bind, "running_workout_details")
    cycling = _reflected_table(bind, "cycling_workout_details")
    hiking = _reflected_table(bind, "hiking_workout_details")
    swimming = _reflected_table(bind, "swimming_workout_details")
    pool = _reflected_table(bind, "pool_swimming_details")
    strength = _reflected_table(bind, "strength_workout_details")
    other = _reflected_table(bind, "other_workout_details")

    activity_rows = [
        dict(row) for row in bind.execute(sa.select(activities)).mappings()
    ]
    workout_rows: list[dict[str, object]] = []
    running_rows: list[dict[str, object]] = []
    cycling_rows: list[dict[str, object]] = []
    hiking_rows: list[dict[str, object]] = []
    swimming_rows: list[dict[str, object]] = []
    pool_rows: list[dict[str, object]] = []
    strength_rows: list[dict[str, object]] = []
    other_rows: list[dict[str, object]] = []
    metadata_by_workout: dict[str, dict[str, object]] = {}
    discipline_mapping = dict(DISCIPLINE_UPGRADE_PAIRS)

    for activity in activity_rows:
        legacy_discipline = _enum_value(activity["sport"])
        discipline = discipline_mapping.get(legacy_discipline, "OTHER")
        swimming_environment: str | None = None
        pool_length: float | None = None
        fallback_reason: str | None = (
            None
            if legacy_discipline in discipline_mapping
            else "unrecognized_legacy_discipline"
        )
        if legacy_discipline == "SWIM":
            (
                swimming_environment,
                pool_length,
                fallback_reason,
            ) = _swimming_backfill(activity)
            if fallback_reason is not None:
                discipline = "OTHER"

        metadata = _metadata_for_activity(activity)
        metadata_by_workout[str(activity["id"])] = metadata
        duration_seconds = int(activity["duration_seconds"])
        workout_rows.append(
            {
                "athlete_id": activity["user_id"],
                "discipline": discipline,
                "started_at": activity["started_at"],
                "duration_seconds": max(duration_seconds, 1),
                "source": _enum_value(activity["source"]),
                "external_id": activity["external_id"],
                "title": activity["name"],
                "notes": None,
                "id": activity["id"],
                "created_at": activity["created_at"],
                "updated_at": activity["updated_at"],
            }
        )

        common_metrics = {
            "workout_id": activity["id"],
            "distance_meters": _nonnegative_float(activity["distance_meters"]),
            "moving_duration_seconds": _nonnegative_int(
                activity["moving_time_seconds"]
            ),
            "average_heart_rate": _nonnegative_float(activity["average_heart_rate"]),
            "max_heart_rate": _nonnegative_float(activity["max_heart_rate"]),
        }
        if discipline == "RUNNING":
            running_rows.append(
                {
                    **common_metrics,
                    "running_type": _running_type(activity),
                    "average_pace_seconds_per_km": _pace(
                        activity["moving_time_seconds"],
                        activity["distance_meters"],
                        distance_unit_meters=1000,
                    ),
                    "elevation_gain_meters": _nonnegative_float(
                        activity["elevation_gain_meters"]
                    ),
                    "elevation_loss_meters": None,
                    "average_cadence_spm": _nonnegative_float(
                        activity["average_cadence"]
                    ),
                    "max_cadence_spm": None,
                }
            )
        elif discipline == "CYCLING":
            cycling_rows.append(
                {
                    **common_metrics,
                    "cycling_type": _cycling_type(activity),
                    "average_speed_kph": _cycling_speed_kph(activity),
                    "max_speed_kph": None,
                    "elevation_gain_meters": _nonnegative_float(
                        activity["elevation_gain_meters"]
                    ),
                    "elevation_loss_meters": None,
                    "average_cadence_rpm": _nonnegative_float(
                        activity["average_cadence"]
                    ),
                    "max_cadence_rpm": None,
                }
            )
        elif discipline == "HIKING":
            hiking_rows.append(
                {
                    **common_metrics,
                    "hiking_type": _hiking_type(activity),
                    "average_pace_seconds_per_km": _pace(
                        activity["moving_time_seconds"],
                        activity["distance_meters"],
                        distance_unit_meters=1000,
                    ),
                    "elevation_gain_meters": _nonnegative_float(
                        activity["elevation_gain_meters"]
                    ),
                    "elevation_loss_meters": None,
                    "pack_weight_kg": None,
                }
            )
        elif discipline == "SWIMMING":
            if swimming_environment is None:
                raise RuntimeError("Swimming backfill is missing its environment")
            swimming_rows.append(
                {
                    **common_metrics,
                    "swimming_environment": swimming_environment,
                    "average_pace_seconds_per_100m": _pace(
                        activity["moving_time_seconds"],
                        activity["distance_meters"],
                        distance_unit_meters=100,
                    ),
                }
            )
            if swimming_environment == "POOL":
                if pool_length is None:
                    raise RuntimeError("Pool swimming backfill is missing pool length")
                pool_rows.append(
                    {
                        "workout_id": activity["id"],
                        "pool_length_meters": pool_length,
                        "total_lengths": None,
                        "primary_stroke": None,
                        "average_swolf": None,
                        "total_strokes": None,
                    }
                )
        elif discipline == "STRENGTH":
            strength_rows.append(
                {
                    "workout_id": activity["id"],
                    "strength_type": _strength_type(activity),
                    "session_focus": None,
                    "exercises_jsonb": [],
                }
            )
        else:
            metrics = dict(metadata)
            if fallback_reason is not None:
                metrics["fallback_reason"] = fallback_reason
            other_rows.append(
                {
                    "workout_id": activity["id"],
                    "activity_name": _other_activity_name(activity),
                    "activity_description": None,
                    "raw_sport": str(activity["source_sport_type"])[:128],
                    "raw_sub_sport": _raw_sub_sport(activity),
                    "distance_meters": _nonnegative_float(activity["distance_meters"]),
                    "average_heart_rate": _nonnegative_float(
                        activity["average_heart_rate"]
                    ),
                    "max_heart_rate": _nonnegative_float(activity["max_heart_rate"]),
                    "metrics_jsonb": metrics,
                }
            )

    _bulk_insert(bind, workouts, workout_rows)
    _bulk_insert(bind, running, running_rows)
    _bulk_insert(bind, cycling, cycling_rows)
    _bulk_insert(bind, hiking, hiking_rows)
    _bulk_insert(bind, swimming, swimming_rows)
    _bulk_insert(bind, pool, pool_rows)
    _bulk_insert(bind, strength, strength_rows)
    _bulk_insert(bind, other, other_rows)
    for activity in activity_rows:
        metadata_by_workout[str(activity["id"])]["canonical_snapshot"] = (
            _canonical_snapshot_from_database(
                bind,
                workout_id=activity["id"],
            )
        )
    return activity_rows, metadata_by_workout


def _backfill_source_link_provenance(
    bind: sa.Connection,
    *,
    activities: Sequence[Mapping[str, object]],
    metadata_by_workout: Mapping[str, dict[str, object]],
) -> int:
    source_links = _reflected_table(bind, "activity_source_links")
    inserted = 0
    primary_link_ids: dict[str, object] = {}
    for activity in activities:
        source = _enum_value(activity["source"])
        matching_ids = list(
            bind.scalars(
                sa.select(source_links.c.id).where(
                    source_links.c.user_id == activity["user_id"],
                    source_links.c.source == source,
                    source_links.c.external_id == activity["external_id"],
                )
            )
        )
        if len(matching_ids) > 1:
            raise RuntimeError(
                "Multiple primary source links found for one legacy activity"
            )
        if matching_ids:
            source_link_id = matching_ids[0]
        else:
            source_link_id: object = activity["id"]
            if bind.scalar(
                sa.select(source_links.c.id).where(source_links.c.id == source_link_id)
            ):
                source_link_id = uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"{revision}:source-link:{activity['id']}",
                )
            bind.execute(
                source_links.insert().values(
                    user_id=activity["user_id"],
                    workout_id=activity["id"],
                    source=source,
                    external_id=activity["external_id"],
                    file_sha256=None,
                    import_job_id=None,
                    id=source_link_id,
                    created_at=activity["created_at"],
                    updated_at=activity["updated_at"],
                )
            )
            inserted += 1
        primary_link_ids[str(activity["id"])] = source_link_id

        bind.execute(
            source_links.update()
            .where(source_links.c.id == source_link_id)
            .values(
                workout_id=activity["id"],
                raw_sport=str(activity["source_sport_type"])[:128],
                raw_sub_sport=_raw_sub_sport(activity),
                source_metadata_jsonb=metadata_by_workout[str(activity["id"])],
                heart_rate_source=_enum_value(activity["average_heart_rate_source"]),
                heart_rate_quality=_enum_value(activity["heart_rate_quality"]),
                heart_rate_reliable=bool(activity["heart_rate_reliable"]),
                heart_rate_sample_count=int(activity["heart_rate_sample_count"]),
                deleted_at=activity["deleted_at"],
            )
        )
    for activity in activities:
        workout_id = activity["id"]
        metadata = metadata_by_workout[str(workout_id)]
        snapshot = metadata["canonical_snapshot"]
        if not isinstance(snapshot, dict):
            raise RuntimeError("Canonical migration snapshot is invalid")
        snapshot["source_links"] = _source_link_snapshot_from_database(
            bind,
            workout_id=workout_id,
        )
        bind.execute(
            source_links.update()
            .where(source_links.c.id == primary_link_ids[str(workout_id)])
            .values(source_metadata_jsonb=metadata)
        )
    return inserted


def _assert_upgrade_integrity(
    bind: sa.Connection,
    *,
    legacy_activity_count: int,
    inserted_source_links: int,
) -> None:
    workouts = _reflected_table(bind, "workouts")
    if _count(bind, workouts) != legacy_activity_count:
        raise RuntimeError("Workout count differs from legacy activity count")

    detail_tables = tuple(
        _reflected_table(bind, table_name) for table_name in _MAIN_DETAIL_TABLES
    )
    detail_ids: list[str] = []
    for detail_table in detail_tables:
        detail_ids.extend(
            str(value) for value in bind.scalars(sa.select(detail_table.c.workout_id))
        )
    if len(detail_ids) != legacy_activity_count:
        raise RuntimeError("Every migrated workout must have one main detail")
    if len(set(detail_ids)) != len(detail_ids):
        raise RuntimeError("A migrated workout has multiple main details")

    for table_name in _SUPPORT_TABLES:
        backup = _reflected_table(bind, _backup_name(table_name))
        target = _reflected_table(bind, table_name)
        expected = _count(bind, backup)
        if table_name == "activity_source_links":
            expected += inserted_source_links
        if _count(bind, target) != expected:
            raise RuntimeError(f"{table_name} row count changed during migration")

    source_links = _reflected_table(bind, "activity_source_links")
    provenance_count = 0
    for value in bind.scalars(sa.select(source_links.c.source_metadata_jsonb)):
        decoded = _decoded_json(value)
        if (
            isinstance(decoded, Mapping)
            and set(decoded) == _MIGRATION_PROVENANCE_KEYS
            and decoded.get("migration_revision") == revision
            and isinstance(decoded.get("legacy_activity"), Mapping)
            and isinstance(decoded.get("canonical_snapshot"), Mapping)
        ):
            provenance_count += 1
    if provenance_count != legacy_activity_count:
        raise RuntimeError(
            "Every migrated workout must retain one legacy provenance record"
        )


def _foreign_key_check(bind: sa.Connection) -> None:
    if bind.dialect.name != "sqlite":
        return
    violations = bind.execute(sa.text("PRAGMA foreign_key_check")).all()
    if violations:
        raise RuntimeError(f"SQLite foreign key violations: {violations!r}")


def _datetime_value(value: object) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        raise RuntimeError(f"Invalid serialized datetime {value!r}")
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized)


def _uuid_value(value: object) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"Invalid serialized UUID {value!r}") from exc


def _database_uuid_value(
    bind: sa.Connection,
    value: object,
) -> uuid.UUID | str:
    parsed = _uuid_value(value)
    return parsed.hex if bind.dialect.name == "sqlite" else parsed


def _normalized_snapshot_mapping(
    value: object,
    *,
    label: str,
) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"Cannot downgrade; {label} snapshot is invalid")
    normalized = _json_safe(dict(value))
    if not isinstance(normalized, dict):
        raise RuntimeError(f"Cannot downgrade; {label} snapshot is invalid")
    return normalized


def _assert_snapshot_row_unchanged(
    *,
    current: Mapping[str, object],
    expected: object,
    label: str,
) -> None:
    expected_mapping = _normalized_snapshot_mapping(expected, label=label)
    current_mapping = _normalized_snapshot_mapping(current, label=label)
    if current_mapping != expected_mapping:
        changed_fields = sorted(
            key
            for key in set(current_mapping) | set(expected_mapping)
            if current_mapping.get(key) != expected_mapping.get(key)
        )
        raise RuntimeError(
            f"Cannot downgrade; canonical {label} data changed after migration: "
            f"{changed_fields!r}"
        )


def _assert_canonical_snapshot_unchanged(
    bind: sa.Connection,
    *,
    workout: Mapping[str, object],
    provenance: Mapping[str, object],
) -> None:
    snapshot_value = provenance.get("canonical_snapshot")
    if not isinstance(snapshot_value, Mapping):
        raise RuntimeError("Cannot downgrade; canonical migration snapshot is missing")
    if set(snapshot_value) != _CANONICAL_SNAPSHOT_KEYS:
        raise RuntimeError("Cannot downgrade; canonical migration snapshot is invalid")

    _assert_snapshot_row_unchanged(
        current=workout,
        expected=snapshot_value["workout"],
        label="workout",
    )

    detail_table_name = snapshot_value["main_detail_table"]
    if detail_table_name not in _MAIN_DETAIL_TABLES:
        raise RuntimeError(
            "Cannot downgrade; canonical migration detail table is invalid"
        )

    current_details: list[tuple[str, Mapping[str, object]]] = []
    for table_name in _MAIN_DETAIL_TABLES:
        detail_table = _reflected_table(bind, table_name)
        detail_row = (
            bind.execute(
                sa.select(detail_table).where(
                    detail_table.c.workout_id == workout["id"]
                )
            )
            .mappings()
            .one_or_none()
        )
        if detail_row is not None:
            current_details.append((table_name, detail_row))
    if len(current_details) != 1 or current_details[0][0] != detail_table_name:
        raise RuntimeError(
            "Cannot downgrade; canonical main-detail structure changed after migration"
        )
    _assert_snapshot_row_unchanged(
        current=current_details[0][1],
        expected=snapshot_value["main_detail"],
        label="main-detail",
    )

    pool_table = _reflected_table(bind, "pool_swimming_details")
    current_pool = (
        bind.execute(
            sa.select(pool_table).where(pool_table.c.workout_id == workout["id"])
        )
        .mappings()
        .one_or_none()
    )
    expected_pool = snapshot_value["pool_detail"]
    if expected_pool is None:
        if current_pool is not None:
            raise RuntimeError(
                "Cannot downgrade; canonical pool-detail structure changed "
                "after migration"
            )
    elif current_pool is None:
        raise RuntimeError(
            "Cannot downgrade; canonical pool-detail structure changed after migration"
        )
    else:
        _assert_snapshot_row_unchanged(
            current=current_pool,
            expected=expected_pool,
            label="pool-detail",
        )

    expected_source_links = snapshot_value["source_links"]
    if not isinstance(expected_source_links, list):
        raise RuntimeError(
            "Cannot downgrade; canonical source-link snapshot is invalid"
        )
    current_source_links = _source_link_snapshot_from_database(
        bind,
        workout_id=workout["id"],
    )
    if current_source_links != expected_source_links:
        raise RuntimeError(
            "Cannot downgrade; canonical source-link data changed after migration"
        )


def _legacy_activity_rows_for_downgrade(
    bind: sa.Connection,
) -> list[dict[str, object]]:
    workouts = _reflected_table(bind, "workouts")
    source_links = _reflected_table(bind, "activity_source_links")
    feedback = _reflected_table(bind, "activity_feedback")
    flow_sessions = _reflected_table(bind, "workout_flow_sessions")

    if bind.scalar(
        sa.select(sa.func.count())
        .select_from(feedback)
        .where(feedback.c.mobility_done.is_not(None))
    ):
        raise RuntimeError("Cannot downgrade feedback with a recorded mobility answer")
    if bind.scalar(
        sa.select(sa.func.count())
        .select_from(flow_sessions)
        .where(flow_sessions.c.state == "MOBILITY")
    ):
        raise RuntimeError("Cannot downgrade while a workout flow is at MOBILITY")
    unsupported_link_sources = list(
        bind.scalars(
            sa.select(source_links.c.source)
            .where(source_links.c.source.not_in(OLD_SOURCES))
            .distinct()
        )
    )
    if unsupported_link_sources:
        raise RuntimeError(
            "Cannot downgrade source links with post-0003 sources: "
            f"{unsupported_link_sources!r}"
        )

    links_by_workout: dict[str, list[Mapping[str, object]]] = {}
    for row in bind.execute(sa.select(source_links)).mappings():
        metadata = _decoded_json(row["source_metadata_jsonb"])
        if metadata is not None and not (
            isinstance(metadata, Mapping)
            and set(metadata) == _MIGRATION_PROVENANCE_KEYS
            and metadata.get("migration_revision") == revision
            and isinstance(metadata.get("legacy_activity"), Mapping)
            and isinstance(metadata.get("canonical_snapshot"), Mapping)
        ):
            raise RuntimeError(
                "Cannot downgrade source metadata created or changed after 0004"
            )
        links_by_workout.setdefault(str(row["workout_id"]), []).append(row)

    required_legacy_keys = {
        "legacy_user_id",
        "legacy_source",
        "legacy_external_id",
        "legacy_sport",
        "legacy_source_sport_type",
        "legacy_name",
        "legacy_started_at",
        "legacy_ended_at",
        "legacy_timezone",
        "legacy_duration_seconds",
        "legacy_moving_time_seconds",
        "legacy_distance_meters",
        "legacy_elevation_gain_meters",
        "legacy_calories_kcal",
        "legacy_average_heart_rate",
        "legacy_average_heart_rate_source",
        "legacy_max_heart_rate",
        "legacy_heart_rate_sample_count",
        "legacy_heart_rate_quality",
        "legacy_heart_rate_reliable",
        "legacy_average_cadence",
        "legacy_route_points",
        "legacy_average_speed_mps",
        "legacy_average_watts",
        "legacy_trainer",
        "legacy_commute",
        "legacy_manual",
        "legacy_raw_summary",
        "legacy_deleted_at",
    }
    restored_rows: list[dict[str, object]] = []
    for workout in bind.execute(sa.select(workouts)).mappings():
        provenance_records: list[Mapping[str, object]] = []
        for source_link in links_by_workout.get(str(workout["id"]), []):
            metadata = _decoded_json(source_link["source_metadata_jsonb"])
            if (
                isinstance(metadata, Mapping)
                and metadata.get("migration_revision") == revision
                and isinstance(metadata.get("legacy_activity"), Mapping)
                and isinstance(metadata.get("canonical_snapshot"), Mapping)
            ):
                provenance_records.append(metadata)
        if len(provenance_records) != 1:
            raise RuntimeError(
                "Each workout must have exactly one 0004 provenance record "
                "before downgrade"
            )
        provenance = provenance_records[0]
        _assert_canonical_snapshot_unchanged(
            bind,
            workout=workout,
            provenance=provenance,
        )
        legacy = provenance["legacy_activity"]
        if not isinstance(legacy, Mapping):
            raise RuntimeError("Cannot reconstruct legacy activity provenance")
        missing = required_legacy_keys.difference(legacy)
        unexpected = set(legacy).difference(required_legacy_keys)
        if missing or unexpected:
            raise RuntimeError(
                "Cannot reconstruct legacy activity; provenance keys differ: "
                f"missing={sorted(missing)!r}, unexpected={sorted(unexpected)!r}"
            )
        legacy_source = str(legacy["legacy_source"])
        legacy_sport = str(legacy["legacy_sport"])
        if legacy_source not in OLD_SOURCES or legacy_sport not in OLD_DISCIPLINES:
            raise RuntimeError(
                "Cannot reconstruct an activity with unsupported legacy enums"
            )
        if str(workout["athlete_id"]) != str(legacy["legacy_user_id"]):
            raise RuntimeError(
                "Workout ownership no longer matches its migration provenance"
            )
        restored_rows.append(
            {
                "user_id": _database_uuid_value(
                    bind,
                    legacy["legacy_user_id"],
                ),
                "source": legacy_source,
                "external_id": legacy["legacy_external_id"],
                "sport": legacy_sport,
                "source_sport_type": legacy["legacy_source_sport_type"],
                "name": legacy["legacy_name"],
                "started_at": _datetime_value(legacy["legacy_started_at"]),
                "ended_at": _datetime_value(legacy["legacy_ended_at"]),
                "timezone": legacy["legacy_timezone"],
                "duration_seconds": int(legacy["legacy_duration_seconds"]),
                "moving_time_seconds": legacy["legacy_moving_time_seconds"],
                "distance_meters": legacy["legacy_distance_meters"],
                "elevation_gain_meters": legacy["legacy_elevation_gain_meters"],
                "calories_kcal": legacy["legacy_calories_kcal"],
                "average_heart_rate": legacy["legacy_average_heart_rate"],
                "average_heart_rate_source": legacy["legacy_average_heart_rate_source"],
                "max_heart_rate": legacy["legacy_max_heart_rate"],
                "heart_rate_sample_count": int(
                    legacy["legacy_heart_rate_sample_count"]
                ),
                "heart_rate_quality": legacy["legacy_heart_rate_quality"],
                "heart_rate_reliable": bool(legacy["legacy_heart_rate_reliable"]),
                "average_cadence": legacy["legacy_average_cadence"],
                "route_points": legacy["legacy_route_points"],
                "average_speed": legacy["legacy_average_speed_mps"],
                "average_watts": legacy["legacy_average_watts"],
                "trainer": bool(legacy["legacy_trainer"]),
                "commute": bool(legacy["legacy_commute"]),
                "manual": bool(legacy["legacy_manual"]),
                "raw_summary": legacy["legacy_raw_summary"],
                "deleted_at": _datetime_value(legacy["legacy_deleted_at"]),
                "id": workout["id"],
                "created_at": workout["created_at"],
                "updated_at": workout["updated_at"],
            }
        )
    return restored_rows


def upgrade() -> None:
    """Migrate legacy activities without discarding provider-only data."""

    bind = op.get_bind()
    _backup_support_tables()
    _drop_support_tables()
    _create_workouts_table()
    _create_detail_tables()
    activities, metadata_by_workout = _backfill_workouts(bind)
    _create_support_tables(workout_column=True)
    _copy_support_rows(bind, to_workout=True)
    inserted_source_links = _backfill_source_link_provenance(
        bind,
        activities=activities,
        metadata_by_workout=metadata_by_workout,
    )
    _replace_baseline_disciplines(to_canonical=True)
    _assert_upgrade_integrity(
        bind,
        legacy_activity_count=len(activities),
        inserted_source_links=inserted_source_links,
    )
    op.drop_table("activities")
    _drop_support_backups()
    _foreign_key_check(bind)


def downgrade() -> None:
    """Restore 0003 exactly when all post-0004 values are representable."""

    bind = op.get_bind()
    legacy_activity_rows = _legacy_activity_rows_for_downgrade(bind)
    _backup_support_tables()
    _drop_support_tables()
    _drop_detail_tables()
    _create_legacy_activities_table()
    activities = _reflected_table(bind, "activities")
    _bulk_insert(bind, activities, legacy_activity_rows)
    _create_support_tables(workout_column=False)
    _copy_support_rows(bind, to_workout=False)

    for table_name in _SUPPORT_TABLES:
        backup = _reflected_table(bind, _backup_name(table_name))
        target = _reflected_table(bind, table_name)
        if _count(bind, backup) != _count(bind, target):
            raise RuntimeError(f"{table_name} row count changed during downgrade")
    if _count(bind, activities) != len(legacy_activity_rows):
        raise RuntimeError("Legacy activity count changed during downgrade")

    op.drop_table("workouts")
    _drop_support_backups()
    _replace_baseline_disciplines(to_canonical=False)
    _foreign_key_check(bind)
