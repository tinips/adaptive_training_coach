"""Add the discipline catalog and global athlete equipment access.

Revision ID: 0017_equipment_catalog
Revises: 0016_restrict_athlete_gender
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0017_equipment_catalog"
down_revision: str | None = "0016_restrict_athlete_gender"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")
_NAMESPACE = uuid.UUID("36d470bc-c860-4ba7-ac0d-c90156c79ad7")

# discipline, key, label, importance, substitutions
CATALOG: tuple[tuple[str, str, str, str, tuple[str, ...]], ...] = (
    (
        "RUNNING",
        "running_shoes",
        "Running shoes",
        "essential",
        ("trail_running_shoes",),
    ),
    (
        "RUNNING",
        "safe_running_route",
        "Safe running route",
        "recommended",
        ("treadmill_access",),
    ),
    ("RUNNING", "trail_running_shoes", "Trail running shoes", "recommended", ()),
    ("RUNNING", "treadmill_access", "Treadmill access", "optional", ()),
    ("RUNNING", "sports_watch", "Sports watch", "optional", ()),
    (
        "CYCLING",
        "bike",
        "Bike",
        "essential",
        ("road_bike", "mountain_bike", "stationary_bike"),
    ),
    ("CYCLING", "road_bike", "Road bike", "recommended", ()),
    ("CYCLING", "helmet", "Helmet", "recommended", ()),
    ("CYCLING", "mountain_bike", "Mountain bike", "optional", ()),
    ("CYCLING", "stationary_bike", "Stationary bike", "optional", ()),
    ("CYCLING", "repair_kit", "Repair kit", "optional", ()),
    (
        "SWIMMING",
        "swimming_access",
        "Swimming access",
        "essential",
        ("pool_access", "open_water_access"),
    ),
    ("SWIMMING", "pool_access", "Pool access", "recommended", ()),
    ("SWIMMING", "goggles", "Goggles", "recommended", ()),
    ("SWIMMING", "open_water_access", "Open-water access", "optional", ()),
    ("SWIMMING", "wetsuit", "Wetsuit", "optional", ()),
    (
        "HIKING",
        "suitable_hiking_footwear",
        "Suitable hiking footwear",
        "essential",
        ("hiking_shoes", "trail_running_shoes"),
    ),
    ("HIKING", "hiking_shoes", "Hiking shoes", "recommended", ()),
    ("HIKING", "backpack", "Backpack", "recommended", ()),
    ("HIKING", "trail_running_shoes", "Trail running shoes", "optional", ()),
    ("HIKING", "trekking_poles", "Trekking poles", "optional", ()),
    (
        "STRENGTH",
        "training_space",
        "Training space",
        "essential",
        ("gym_access", "home_training_space"),
    ),
    ("STRENGTH", "resistance_bands", "Resistance bands", "recommended", ()),
    ("STRENGTH", "gym_access", "Gym access", "optional", ()),
    ("STRENGTH", "home_training_space", "Home training space", "optional", ()),
    ("STRENGTH", "free_weights", "Free weights", "optional", ()),
)

_OLD_RESOURCE_MAP = {
    "running_shoes": ("RUNNING", "running_shoes"),
    "safe_running_route": ("RUNNING", "safe_running_route"),
    "trail_shoes": ("RUNNING", "trail_running_shoes"),
    "road_or_tri_bike": ("CYCLING", "road_bike"),
    "mountain_bike": ("CYCLING", "mountain_bike"),
    "indoor_bike": ("CYCLING", "stationary_bike"),
    "helmet": ("CYCLING", "helmet"),
    "pool_access": ("SWIMMING", "pool_access"),
    "open_water_access": ("SWIMMING", "open_water_access"),
    "wetsuit": ("SWIMMING", "wetsuit"),
}


def _catalog_id(discipline: str, equipment: str) -> uuid.UUID:
    return uuid.uuid5(_NAMESPACE, f"{discipline}:{equipment}")


def _normalize_sessions(bind: sa.Connection) -> None:
    sessions = sa.table(
        "onboarding_sessions",
        sa.column("id", sa.Uuid()),
        sa.column("current_step", sa.String()),
        sa.column("answers", _JSON),
    )
    old_steps = (
        "EQUIPMENT_RECOMMENDATION",
        "EQUIPMENT_INTAKE",
        "EQUIPMENT_DETAILS_INTAKE",
    )
    obsolete_keys = {
        "equipment_recommendation_text",
        "equipment_resource_ids",
        "equipment_resource_labels",
        "equipment_selection",
        "equipment_settings_review",
        "_context_retry_error",
    }
    for row in bind.execute(
        sa.select(sessions.c.id, sessions.c.answers).where(
            sessions.c.current_step.in_(old_steps)
        )
    ):
        answers = dict(row.answers) if isinstance(row.answers, dict) else {}
        for key in obsolete_keys:
            answers.pop(key, None)
        bind.execute(
            sessions.update()
            .where(sessions.c.id == row.id)
            .values(current_step="EQUIPMENT_RECOMMENDATION", answers=answers)
        )

    settings = sa.table(
        "profile_settings_sessions",
        sa.column("current_step", sa.String()),
        sa.column("pending_answers", _JSON),
    )
    bind.execute(
        settings.update()
        .where(settings.c.current_step == "EQUIPMENT")
        .values(current_step="MENU", pending_answers={})
    )


def _backfill_available(bind: sa.Connection) -> None:
    resources = sa.table(
        "equipment_resources",
        sa.column("id", sa.Uuid()),
        sa.column("code", sa.String()),
    )
    statuses = sa.table(
        "athlete_goal_equipment_statuses",
        sa.column("user_id", sa.Uuid()),
        sa.column("training_goal_id", sa.Uuid()),
        sa.column("goal_revision", sa.Integer()),
        sa.column("resource_id", sa.Uuid()),
        sa.column("status", sa.String()),
    )
    goals = sa.table(
        "training_goals",
        sa.column("id", sa.Uuid()),
        sa.column("equipment_context_revision", sa.Integer()),
    )
    target = sa.table(
        "athlete_equipment",
        sa.column("id", sa.Uuid()),
        sa.column("athlete_id", sa.Uuid()),
        sa.column("equipment_id", sa.Uuid()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    rows = bind.execute(
        sa.select(statuses.c.user_id, resources.c.code)
        .join(goals, goals.c.id == statuses.c.training_goal_id)
        .join(resources, resources.c.id == statuses.c.resource_id)
        .where(
            statuses.c.status == "AVAILABLE",
            statuses.c.goal_revision == goals.c.equipment_context_revision,
        )
        .distinct()
    )
    now = datetime.now(UTC)
    seen: set[tuple[uuid.UUID, uuid.UUID]] = set()
    for athlete_id, old_code in rows:
        mapped = _OLD_RESOURCE_MAP.get(old_code)
        if mapped is None:
            continue
        equipment_id = _catalog_id(*mapped)
        key = (athlete_id, equipment_id)
        if key in seen:
            continue
        seen.add(key)
        existing = bind.execute(
            sa.select(target.c.id).where(
                target.c.athlete_id == athlete_id,
                target.c.equipment_id == equipment_id,
            )
        ).first()
        if existing is None:
            bind.execute(
                target.insert().values(
                    id=uuid.uuid4(),
                    athlete_id=athlete_id,
                    equipment_id=equipment_id,
                    created_at=now,
                    updated_at=now,
                )
            )


def upgrade() -> None:
    op.create_table(
        "equipment_catalog",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("discipline", sa.String(16), nullable=False),
        sa.Column("equipment", sa.String(64), nullable=False),
        sa.Column("display_name", sa.String(120), nullable=False),
        sa.Column("importance", sa.String(16), nullable=False),
        sa.Column("substitutions", _JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "discipline IN ('RUNNING','CYCLING','HIKING','SWIMMING','STRENGTH','OTHER')",
            name=op.f("ck_equipment_catalog_equipment_catalog_discipline"),
        ),
        sa.CheckConstraint(
            "importance IN ('essential','recommended','optional')",
            name=op.f("ck_equipment_catalog_equipment_importance"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_equipment_catalog")),
        sa.UniqueConstraint(
            "discipline",
            "equipment",
            name="uq_equipment_catalog_discipline_item",
        ),
    )
    op.create_table(
        "athlete_equipment",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("athlete_id", sa.Uuid(), nullable=False),
        sa.Column("equipment_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["athlete_id"],
            ["users.id"],
            name=op.f("fk_athlete_equipment_athlete_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["equipment_id"],
            ["equipment_catalog.id"],
            name=op.f("fk_athlete_equipment_equipment_id_equipment_catalog"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_athlete_equipment")),
        sa.UniqueConstraint(
            "athlete_id", "equipment_id", name="uq_athlete_equipment_item"
        ),
    )
    op.create_index(
        "ix_athlete_equipment_athlete_id",
        "athlete_equipment",
        ["athlete_id"],
        unique=False,
    )

    bind = op.get_bind()
    now = datetime.now(UTC)
    catalog = sa.table(
        "equipment_catalog",
        sa.column("id", sa.Uuid()),
        sa.column("discipline", sa.String()),
        sa.column("equipment", sa.String()),
        sa.column("display_name", sa.String()),
        sa.column("importance", sa.String()),
        sa.column("substitutions", _JSON),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    bind.execute(
        catalog.insert(),
        [
            {
                "id": _catalog_id(discipline, equipment),
                "discipline": discipline,
                "equipment": equipment,
                "display_name": display_name,
                "importance": importance,
                "substitutions": list(substitutions),
                "created_at": now,
                "updated_at": now,
            }
            for discipline, equipment, display_name, importance, substitutions in CATALOG
        ],
    )
    _backfill_available(bind)
    _normalize_sessions(bind)


def downgrade() -> None:
    op.drop_index("ix_athlete_equipment_athlete_id", table_name="athlete_equipment")
    op.drop_table("athlete_equipment")
    op.drop_table("equipment_catalog")
