"""Retain only confirmed structured weekly availability.

Revision ID: 0046_structured_availability
Revises: 0045_remove_mobile_sync
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.schemas.availability import ConfirmedWeeklyAvailability

revision: str = "0046_structured_availability"
down_revision: str | None = "0045_remove_mobile_sync"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    profiles = sa.table(
        "athlete_profiles",
        sa.column("id"),
        sa.column("weekly_availability_jsonb", sa.JSON()),
    )
    connection = op.get_bind()
    rows = connection.execute(
        sa.select(profiles.c.id, profiles.c.weekly_availability_jsonb)
    ).mappings()
    for row in rows:
        normalized = _normalized_availability(row["weekly_availability_jsonb"])
        connection.execute(
            profiles.update()
            .where(profiles.c.id == row["id"])
            .values(weekly_availability_jsonb=normalized)
        )
    with op.batch_alter_table("athlete_profiles") as batch:
        batch.drop_column("availability_text")


def _normalized_availability(raw: object) -> dict[str, object] | None:
    if not isinstance(raw, dict):
        return None
    try:
        return ConfirmedWeeklyAvailability(days=raw["days"]).model_dump(mode="json")
    except (KeyError, ValueError):
        return None


def downgrade() -> None:
    raise NotImplementedError("Raw availability text was permanently deleted.")
