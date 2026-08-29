"""Retire the mountain-bike race primary goal.

Revision ID: 0035_retire_mtb_race
Revises: 0034_add_goal_event_date_step

Row is retired rather than deleted. An athlete may already have chosen it,
and training_goals.goal_template_id references it. The catalog status enum
has no dedicated "retired" member, so this reuses DISABLED, which already
means "can no longer drive new planning decisions" (see
app.domain.enums.CatalogItemStatus). Same pattern as
0032_retire_non_endurance_goals.

The cycling_mountain training context stays ACTIVE and untouched: it is
still used as a substitute execution context for ROAD_CYCLING_EVENT (an
athlete whose goal is a road cycling event but who only has a mountain
bike). Only the goal template itself, and the rows that existed solely for
an MTB_RACE athlete's own equipment choice, are pruned from the seed
module — this migration only needs to flip the goal template's status.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op

revision: str = "0035_retire_mtb_race"
down_revision: str | None = "0034_add_goal_event_date_step"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RETIRED_CODES = ("MTB_RACE",)


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
