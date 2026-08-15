"""Replace equipment rows with a dynamic training-capability catalog.

Revision ID: 0022_dynamic_training_catalog
Revises: 0021_remove_goal_description
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.training_catalog_seed import (
    CAPABILITIES,
    EXECUTION_OPTIONS,
    GOAL_CONTEXTS,
    GOAL_TEMPLATES,
    OPTION_CAPABILITIES,
    TRAINING_CONTEXTS,
    catalog_id,
)

revision: str = "0022_dynamic_training_catalog"
down_revision: str | None = "0021_remove_goal_description"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")
_PROFILE_STEPS = (
    "'MENU','GOAL_MENU','GOAL_MAIN','GOAL_OUTCOME','GOAL_DATE','GOAL_SECONDARY',"
    "'GOAL_CLASSIFICATION_CONFIRM','AVAILABILITY','EQUIPMENT','HEALTH',"
    "'PERSONAL_MENU','PERSONAL_BIRTH_YEAR','PERSONAL_GENDER','PERSONAL_WEIGHT',"
    "'PERSONAL_HEIGHT'"
)
_EQUIPMENT_MAP = {
    "running_shoes": "running_shoes",
    "trail_running_shoes": "trail_running_shoes",
    "treadmill_access": "treadmill_access",
    "sports_watch": "sports_watch",
    "bike": "bike_access_unspecified",
    "road_bike": "road_bike",
    "helmet": "helmet",
    "mountain_bike": "mountain_bike",
    "stationary_bike": "stationary_bike",
    "repair_kit": "repair_kit",
    "swimming_access": "swimming_access_unspecified",
    "pool_access": "pool_access",
    "goggles": "goggles",
    "open_water_access": "open_water_access",
    "wetsuit": "wetsuit",
    "suitable_hiking_footwear": "hiking_footwear_unspecified",
    "hiking_shoes": "hiking_shoes",
    "resistance_bands": "resistance_bands",
    "gym_access": "gym_access",
    "home_training_space": "home_training_space",
    "free_weights": "free_weights",
}


def _timestamps() -> tuple[datetime, datetime]:
    now = datetime.now(UTC)
    return now, now


def _create_tables() -> None:
    source = "source IN ('SEEDED','LLM_GENERATED')"
    status = "status IN ('ACTIVE','DISABLED')"
    op.create_table(
        "goal_templates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("display_name", sa.String(120), nullable=False),
        sa.Column("description", sa.String(500), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("definition_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('PRIMARY','SUPPORTING')", name="goal_template_kind"
        ),
        sa.CheckConstraint(source, name="catalog_item_source"),
        sa.CheckConstraint(status, name="catalog_item_status"),
        sa.CheckConstraint(
            "code = upper(code) AND length(code) BETWEEN 3 AND 64",
            name="goal_template_code",
        ),
        sa.CheckConstraint(
            "definition_version > 0", name="definition_version_positive"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_goal_templates_code"),
    )
    op.create_table(
        "training_contexts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("display_name", sa.String(120), nullable=False),
        sa.Column("description", sa.String(500), nullable=False),
        sa.Column("discipline", sa.String(16), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("definition_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "discipline IN "
            "('RUNNING','CYCLING','HIKING','SWIMMING','STRENGTH','OTHER')",
            name="training_context_discipline",
        ),
        sa.CheckConstraint(source, name="catalog_item_source"),
        sa.CheckConstraint(status, name="catalog_item_status"),
        sa.CheckConstraint(
            "code = lower(code) AND length(code) BETWEEN 3 AND 64",
            name="training_context_code",
        ),
        sa.CheckConstraint(
            "definition_version > 0", name="definition_version_positive"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_training_contexts_code"),
    )
    op.create_table(
        "capabilities",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("display_name", sa.String(120), nullable=False),
        sa.Column("description", sa.String(500), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("definition_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('EQUIPMENT','ACCESS','FACILITY')", name="capability_kind"
        ),
        sa.CheckConstraint(source, name="catalog_item_source"),
        sa.CheckConstraint(status, name="catalog_item_status"),
        sa.CheckConstraint(
            "code = lower(code) AND length(code) BETWEEN 3 AND 64",
            name="capability_code",
        ),
        sa.CheckConstraint(
            "definition_version > 0", name="definition_version_positive"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_capabilities_code"),
    )
    op.create_table(
        "goal_template_contexts",
        sa.Column("goal_template_id", sa.Uuid(), nullable=False),
        sa.Column("training_context_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("role IN ('TARGET','SUPPORTING')", name="goal_context_role"),
        sa.CheckConstraint("priority >= 0", name="priority_nonnegative"),
        sa.ForeignKeyConstraint(
            ["goal_template_id"], ["goal_templates.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["training_context_id"], ["training_contexts.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("goal_template_id", "training_context_id"),
    )
    op.create_table(
        "context_execution_options",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("target_context_id", sa.Uuid(), nullable=False),
        sa.Column("execution_context_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("display_name", sa.String(120), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("limitations", _JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "role IN ('PREFERRED','SUBSTITUTE')", name="execution_option_role"
        ),
        sa.CheckConstraint("priority >= 0", name="priority_nonnegative"),
        sa.CheckConstraint(
            "code = lower(code) AND length(code) BETWEEN 3 AND 64",
            name="execution_option_code",
        ),
        sa.ForeignKeyConstraint(
            ["target_context_id"], ["training_contexts.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["execution_context_id"], ["training_contexts.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "target_context_id",
            "code",
            name="uq_context_execution_options_context_code",
        ),
    )
    op.create_table(
        "execution_option_capabilities",
        sa.Column("execution_option_id", sa.Uuid(), nullable=False),
        sa.Column("capability_id", sa.Uuid(), nullable=False),
        sa.Column("importance", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "importance IN ('REQUIRED','RECOMMENDED','OPTIONAL')",
            name="execution_capability_importance",
        ),
        sa.ForeignKeyConstraint(
            ["execution_option_id"],
            ["context_execution_options.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["capability_id"], ["capabilities.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("execution_option_id", "capability_id"),
    )
    op.create_table(
        "athlete_capabilities",
        sa.Column("athlete_id", sa.Uuid(), nullable=False),
        sa.Column("capability_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('AVAILABLE','UNAVAILABLE')", name="athlete_capability_status"
        ),
        sa.ForeignKeyConstraint(["athlete_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["capability_id"], ["capabilities.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("athlete_id", "capability_id"),
    )
    op.create_index(
        "ix_athlete_capabilities_athlete_id", "athlete_capabilities", ["athlete_id"]
    )
    if op.get_bind().dialect.name == "postgresql":
        op.create_check_constraint(
            "goal_template_code_pattern",
            "goal_templates",
            "code ~ '^[A-Z][A-Z0-9_]{2,63}$'",
        )
        for table_name, constraint_name in (
            ("training_contexts", "training_context_code_pattern"),
            ("capabilities", "capability_code_pattern"),
            ("context_execution_options", "execution_option_code_pattern"),
        ):
            op.create_check_constraint(
                constraint_name,
                table_name,
                "code ~ '^[a-z][a-z0-9_]{2,63}$'",
            )


def _seed(bind: sa.Connection) -> None:
    now, updated = _timestamps()
    goals = sa.table(
        "goal_templates",
        *(
            sa.column(name)
            for name in (
                "id",
                "code",
                "kind",
                "display_name",
                "description",
                "source",
                "status",
                "definition_version",
                "created_at",
                "updated_at",
            )
        ),
    )
    contexts = sa.table(
        "training_contexts",
        *(
            sa.column(name)
            for name in (
                "id",
                "code",
                "discipline",
                "display_name",
                "description",
                "source",
                "status",
                "definition_version",
                "created_at",
                "updated_at",
            )
        ),
    )
    capabilities = sa.table(
        "capabilities",
        *(
            sa.column(name)
            for name in (
                "id",
                "code",
                "display_name",
                "kind",
                "description",
                "source",
                "status",
                "definition_version",
                "created_at",
                "updated_at",
            )
        ),
    )
    goal_contexts = sa.table(
        "goal_template_contexts",
        *(
            sa.column(name)
            for name in (
                "goal_template_id",
                "training_context_id",
                "role",
                "priority",
                "created_at",
                "updated_at",
            )
        ),
    )
    options = sa.table(
        "context_execution_options",
        sa.column("id", sa.Uuid()),
        sa.column("target_context_id", sa.Uuid()),
        sa.column("execution_context_id", sa.Uuid()),
        sa.column("code"),
        sa.column("display_name"),
        sa.column("role"),
        sa.column("priority"),
        sa.column("limitations", _JSON),
        sa.column("created_at"),
        sa.column("updated_at"),
    )
    requirements = sa.table(
        "execution_option_capabilities",
        *(
            sa.column(name)
            for name in (
                "execution_option_id",
                "capability_id",
                "importance",
                "created_at",
                "updated_at",
            )
        ),
    )
    bind.execute(
        goals.insert(),
        [
            {
                "id": catalog_id("goal", code),
                "code": code,
                "kind": kind,
                "display_name": label,
                "description": description,
                "source": "SEEDED",
                "status": "ACTIVE",
                "definition_version": 1,
                "created_at": now,
                "updated_at": updated,
            }
            for code, kind, label, description in GOAL_TEMPLATES
        ],
    )
    bind.execute(
        contexts.insert(),
        [
            {
                "id": catalog_id("context", code),
                "code": code,
                "discipline": discipline,
                "display_name": label,
                "description": description,
                "source": "SEEDED",
                "status": "ACTIVE",
                "definition_version": 1,
                "created_at": now,
                "updated_at": updated,
            }
            for code, discipline, label, description in TRAINING_CONTEXTS
        ],
    )
    bind.execute(
        capabilities.insert(),
        [
            {
                "id": catalog_id("capability", code),
                "code": code,
                "display_name": label,
                "kind": kind,
                "description": description,
                "source": "SEEDED",
                "status": "ACTIVE",
                "definition_version": 1,
                "created_at": now,
                "updated_at": updated,
            }
            for code, label, kind, description in CAPABILITIES
        ],
    )
    bind.execute(
        goal_contexts.insert(),
        [
            {
                "goal_template_id": catalog_id("goal", goal),
                "training_context_id": catalog_id("context", context),
                "role": role,
                "priority": priority,
                "created_at": now,
                "updated_at": updated,
            }
            for goal, context, role, priority in GOAL_CONTEXTS
        ],
    )
    bind.execute(
        options.insert(),
        [
            {
                "id": catalog_id("option", f"{target}:{code}"),
                "target_context_id": catalog_id("context", target),
                "execution_context_id": catalog_id("context", execution),
                "code": code,
                "display_name": label,
                "role": role,
                "priority": priority,
                "limitations": list(limitations),
                "created_at": now,
                "updated_at": updated,
            }
            for target, code, label, execution, role, priority, limitations in (
                EXECUTION_OPTIONS
            )
        ],
    )
    bind.execute(
        requirements.insert(),
        [
            {
                "execution_option_id": catalog_id("option", f"{target}:{option}"),
                "capability_id": catalog_id("capability", capability),
                "importance": importance,
                "created_at": now,
                "updated_at": updated,
            }
            for target, option, capability, importance in OPTION_CAPABILITIES
        ],
    )


def _classify_primary(value: str) -> str | None:
    text = " ".join(value.casefold().split())
    rules = (
        (r"\b(?:ironman\s*)?70[.]3\b|\bhalf ironman\b", "TRIATHLON_HALF_DISTANCE"),
        (r"\b(?:full[- ]distance triathlon|ironman)\b", "TRIATHLON_FULL_DISTANCE"),
        (r"\bolympic triathlon\b", "TRIATHLON_OLYMPIC"),
        (r"\bsprint triathlon\b", "TRIATHLON_SPRINT"),
        (r"\bhyrox\b", "HYROX"),
        (r"\b(?:spartan|obstacle)\b", "OBSTACLE_RACE"),
        (r"\bhalf marathon\b", "HALF_MARATHON"),
        (r"\bmarathon\b", "MARATHON"),
        (r"\b10\s?k\b", "RUNNING_10K"),
        (r"\b5\s?k\b", "RUNNING_5K"),
        (r"\btrail race\b", "TRAIL_RACE"),
        (r"\b(?:mtb|mountain bike)\b", "MTB_RACE"),
        (r"\b(?:road cycling|gran fondo)\b", "ROAD_CYCLING_EVENT"),
        (r"\bopen water\b", "OPEN_WATER_SWIM"),
        (r"\bpool swim", "POOL_SWIMMING_EVENT"),
        (r"\bstrength\b", "GENERAL_STRENGTH"),
        (r"\b(?:hiking|trekking)\b", "GENERAL_HIKING"),
    )
    return next((code for pattern, code in rules if re.search(pattern, text)), None)


def _classify_supporting(value: str | None) -> str | None:
    if not value:
        return None
    text = value.casefold()
    if "muscle" in text:
        return "MUSCLE_RETENTION"
    if "strength" in text:
        return "STRENGTH_MAINTENANCE"
    for word, code in (
        ("running", "IMPROVE_RUNNING"),
        ("cycling", "IMPROVE_CYCLING"),
        ("swimming", "IMPROVE_SWIMMING"),
    ):
        if word in text:
            return code
    return None


def _backfill(bind: sa.Connection) -> None:
    goals = sa.table(
        "training_goals",
        sa.column("id", sa.Uuid()),
        sa.column("main_goal"),
        sa.column("original_description"),
        sa.column("secondary_priority"),
        sa.column("goal_template_id", sa.Uuid()),
        sa.column("supporting_goal_template_id", sa.Uuid()),
    )
    for row in bind.execute(
        sa.select(
            goals.c.id,
            goals.c.main_goal,
            goals.c.original_description,
            goals.c.secondary_priority,
        )
    ):
        primary = _classify_primary(f"{row.main_goal} {row.original_description}")
        supporting = _classify_supporting(row.secondary_priority)
        bind.execute(
            goals.update()
            .where(goals.c.id == row.id)
            .values(
                goal_template_id=catalog_id("goal", primary) if primary else None,
                supporting_goal_template_id=catalog_id("goal", supporting)
                if supporting
                else None,
            )
        )

    old_catalog = sa.table(
        "equipment_catalog",
        sa.column("id", sa.Uuid()),
        sa.column("equipment"),
    )
    old_access = sa.table(
        "athlete_equipment",
        sa.column("athlete_id", sa.Uuid()),
        sa.column("equipment_id", sa.Uuid()),
    )
    target = sa.table(
        "athlete_capabilities",
        sa.column("athlete_id", sa.Uuid()),
        sa.column("capability_id", sa.Uuid()),
        sa.column("status"),
        sa.column("created_at"),
        sa.column("updated_at"),
    )
    rows = tuple(
        bind.execute(
            sa.select(old_access.c.athlete_id, old_catalog.c.equipment).join(
                old_catalog, old_catalog.c.id == old_access.c.equipment_id
            )
        )
    )
    unmatched = sorted(
        {row.equipment for row in rows if row.equipment not in _EQUIPMENT_MAP}
    )
    if unmatched:
        raise RuntimeError(
            "dynamic catalog cannot backfill equipment codes: " + ", ".join(unmatched)
        )
    now, updated = _timestamps()
    seen: set[tuple[uuid.UUID, uuid.UUID]] = set()
    for row in rows:
        capability_id = catalog_id("capability", _EQUIPMENT_MAP[row.equipment])
        key = (row.athlete_id, capability_id)
        if key in seen:
            continue
        seen.add(key)
        bind.execute(
            target.insert().values(
                athlete_id=row.athlete_id,
                capability_id=capability_id,
                status="AVAILABLE",
                created_at=now,
                updated_at=updated,
            )
        )
    migrated = bind.execute(
        sa.select(sa.func.count())
        .select_from(target)
        .where(target.c.status == "AVAILABLE")
    ).scalar_one()
    if migrated < len(seen):
        raise RuntimeError("dynamic catalog equipment backfill verification failed")


def _normalize_sessions() -> None:
    op.execute(
        sa.text(
            "UPDATE onboarding_sessions "
            "SET current_step = 'EQUIPMENT_RECOMMENDATION', "
            "answers = answers - 'equipment_selection' "
            "WHERE current_step = 'EQUIPMENT_INTAKE'"
        )
    )
    op.execute(
        sa.text(
            "UPDATE profile_settings_sessions SET current_step = 'MENU', "
            "pending_answers = '{}' WHERE current_step = 'EQUIPMENT'"
        )
    )
    with op.batch_alter_table("profile_settings_sessions") as batch:
        batch.drop_constraint(
            op.f("ck_profile_settings_sessions_profile_settings_step"), type_="check"
        )
        batch.create_check_constraint(
            "profile_settings_step", f"current_step IN ({_PROFILE_STEPS})"
        )


def upgrade() -> None:
    _create_tables()
    with op.batch_alter_table("training_goals") as batch:
        batch.add_column(sa.Column("goal_template_id", sa.Uuid(), nullable=True))
        batch.add_column(
            sa.Column("supporting_goal_template_id", sa.Uuid(), nullable=True)
        )
        batch.create_foreign_key(
            "fk_training_goals_goal_template_id_goal_templates",
            "goal_templates",
            ["goal_template_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_foreign_key(
            "fk_training_goals_supporting_goal_template_id_goal_templates",
            "goal_templates",
            ["supporting_goal_template_id"],
            ["id"],
            ondelete="RESTRICT",
        )
    bind = op.get_bind()
    _seed(bind)
    _backfill(bind)
    _normalize_sessions()
    if bind.dialect.name == "postgresql":
        op.alter_column(
            "profile_settings_sessions",
            "pending_answers",
            existing_type=sa.JSON(),
            type_=postgresql.JSONB(),
            postgresql_using="pending_answers::jsonb",
        )
    op.drop_index("ix_athlete_equipment_athlete_id", table_name="athlete_equipment")
    op.drop_table("athlete_equipment")
    op.drop_table("equipment_catalog")


def downgrade() -> None:
    raise RuntimeError(
        "0022 replaced equipment knowledge with a dynamic global catalog; "
        "restore the required pre-migration backup"
    )
