"""Remove obsolete goal-scoped and raw-text equipment storage.

Revision ID: 0018_remove_obsolete_equipment
Revises: 0017_equipment_catalog

This cleanup is intentionally destructive. Restoring discarded raw text or
interpretations requires the pre-cleanup database backup.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op

revision: str = "0018_remove_obsolete_equipment"
down_revision: str | None = "0017_equipment_catalog"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NAMESPACE = uuid.UUID("36d470bc-c860-4ba7-ac0d-c90156c79ad7")
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
_ONBOARDING_STEPS = (
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


def _catalog_id(discipline: str, equipment: str) -> uuid.UUID:
    return uuid.uuid5(_NAMESPACE, f"{discipline}:{equipment}")


def _eligible_available_rows(
    bind: sa.Connection,
) -> tuple[tuple[uuid.UUID, str], ...]:
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
    return tuple((athlete_id, code) for athlete_id, code in rows)


def _final_backfill(bind: sa.Connection) -> None:
    """Capture late Release A writes and abort on knowledge we cannot map."""

    rows = _eligible_available_rows(bind)
    unmatched = sorted({code for _, code in rows if code not in _OLD_RESOURCE_MAP})
    if unmatched:
        raise RuntimeError(
            "obsolete equipment cleanup has unmatched AVAILABLE resource codes: "
            + ", ".join(unmatched)
        )

    target = sa.table(
        "athlete_equipment",
        sa.column("id", sa.Uuid()),
        sa.column("athlete_id", sa.Uuid()),
        sa.column("equipment_id", sa.Uuid()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    now = datetime.now(UTC)
    expected: set[tuple[uuid.UUID, uuid.UUID]] = set()
    for athlete_id, old_code in rows:
        equipment_id = _catalog_id(*_OLD_RESOURCE_MAP[old_code])
        expected.add((athlete_id, equipment_id))
        exists = bind.execute(
            sa.select(target.c.id).where(
                target.c.athlete_id == athlete_id,
                target.c.equipment_id == equipment_id,
            )
        ).first()
        if exists is None:
            bind.execute(
                target.insert().values(
                    id=uuid.uuid4(),
                    athlete_id=athlete_id,
                    equipment_id=equipment_id,
                    created_at=now,
                    updated_at=now,
                )
            )

    for athlete_id, equipment_id in expected:
        migrated = bind.execute(
            sa.select(target.c.id).where(
                target.c.athlete_id == athlete_id,
                target.c.equipment_id == equipment_id,
            )
        ).first()
        if migrated is None:
            raise RuntimeError(
                "obsolete equipment cleanup backfill verification failed"
            )


def _normalize_and_tighten_steps() -> None:
    op.execute(
        sa.text(
            "UPDATE onboarding_sessions "
            "SET current_step = 'EQUIPMENT_RECOMMENDATION' "
            "WHERE current_step = 'EQUIPMENT_DETAILS_INTAKE'"
        )
    )
    op.execute(
        sa.text(
            "UPDATE llm_usage SET onboarding_step = 'EQUIPMENT_RECOMMENDATION' "
            "WHERE onboarding_step = 'EQUIPMENT_DETAILS_INTAKE'"
        )
    )
    allowed = ", ".join(f"'{step}'" for step in _ONBOARDING_STEPS)
    with op.batch_alter_table("onboarding_sessions") as batch:
        batch.drop_constraint(
            op.f("ck_onboarding_sessions_onboarding_step"),
            type_="check",
        )
        batch.create_check_constraint(
            op.f("ck_onboarding_sessions_onboarding_step"),
            f"current_step IN ({allowed})",
        )
    with op.batch_alter_table("llm_usage") as batch:
        batch.drop_constraint(
            op.f("ck_llm_usage_llm_onboarding_step"),
            type_="check",
        )
        batch.create_check_constraint(
            op.f("ck_llm_usage_llm_onboarding_step"),
            f"onboarding_step IN ({allowed})",
        )


def upgrade() -> None:
    bind = op.get_bind()
    _final_backfill(bind)
    _normalize_and_tighten_steps()

    for table_name in (
        "athlete_goal_equipment_interpretations",
        "athlete_goal_equipment_statuses",
        "equipment_resource_substitutions",
        "equipment_resource_requirements",
        "equipment_stage_windows",
        "equipment_goal_types",
        "equipment_resources",
    ):
        op.drop_table(table_name)
    op.execute(sa.text("DROP TABLE IF EXISTS equipment_access"))
    op.drop_column("training_goals", "equipment_context_revision")
    op.drop_column("athlete_profiles", "equipment_recommendation_text")
    op.drop_column("athlete_profiles", "equipment_text")


def downgrade() -> None:
    raise RuntimeError(
        "0018 discarded obsolete equipment data; restore the pre-cleanup backup"
    )
