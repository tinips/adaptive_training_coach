"""Retire the non-endurance primary goals.

Revision ID: 0032_retire_non_endurance_goals
Revises: 0031_remove_legacy_primary_sport

Rows are retired rather than deleted. An athlete may already have chosen one,
and training_goals.goal_template_id references them. The catalog status enum
has no dedicated "retired" member, so this reuses DISABLED, which already
means "can no longer drive new planning decisions" (see
app.domain.enums.CatalogItemStatus).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op

revision: str = "0032_retire_non_endurance_goals"
down_revision: str | None = "0031_remove_legacy_primary_sport"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RETIRED_CODES = ("GENERAL_HIKING", "GENERAL_STRENGTH", "HYROX", "OBSTACLE_RACE")


def _goal_templates_table() -> sa.Table:
    return sa.table(
        "goal_templates",
        sa.column("code", sa.String()),
        sa.column("status", sa.String()),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )


def upgrade() -> None:
    goal_templates = _goal_templates_table()
    op.execute(
        goal_templates.update()
        .where(goal_templates.c.code.in_(_RETIRED_CODES))
        .values(status="DISABLED", updated_at=datetime.now(UTC))
    )


def downgrade() -> None:
    goal_templates = _goal_templates_table()
    op.execute(
        goal_templates.update()
        .where(goal_templates.c.code.in_(_RETIRED_CODES))
        .values(status="ACTIVE", updated_at=datetime.now(UTC))
    )
