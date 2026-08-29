"""Delete obsolete non-endurance goal templates.

Revision ID: 0036_prune_non_endurance_goals
Revises: 0035_retire_mtb_race

The product supports running, cycling, swimming, and triathlon goals. These
catalog rows are obsolete and must not remain visible in database tooling.
The migration refuses to remove a row referenced by an athlete goal; preserve
that athlete's history and migrate it explicitly before retrying.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0036_prune_non_endurance_goals"
down_revision: str | None = "0035_retire_mtb_race"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OBSOLETE_CODES = (
    "GENERAL_HIKING",
    "GENERAL_STRENGTH",
    "HYROX",
    "MTB_RACE",
    "SPARTAN_RACE",
)


def upgrade() -> None:
    bind = op.get_bind()
    goal_templates = sa.table(
        "goal_templates",
        sa.column("id", sa.Uuid()),
        sa.column("code", sa.String()),
        sa.column("source", sa.String()),
        sa.column("definition_version", sa.Integer()),
    )
    training_goals = sa.table(
        "training_goals",
        sa.column("goal_template_id", sa.Uuid()),
        sa.column("supporting_goal_template_id", sa.Uuid()),
    )
    goal_contexts = sa.table(
        "goal_template_contexts",
        sa.column("goal_template_id", sa.Uuid()),
    )

    rows = tuple(
        bind.execute(
            sa.select(goal_templates.c.id, goal_templates.c.code).where(
                goal_templates.c.code.in_(_OBSOLETE_CODES)
            )
        )
    )

    goal_ids = tuple(row.id for row in rows)
    if not goal_ids:
        return
    references = tuple(
        bind.execute(
            sa.select(goal_templates.c.code)
            .join(
                training_goals,
                sa.or_(
                    training_goals.c.goal_template_id == goal_templates.c.id,
                    training_goals.c.supporting_goal_template_id == goal_templates.c.id,
                ),
            )
            .where(goal_templates.c.id.in_(goal_ids))
        ).scalars()
    )
    if references:
        raise RuntimeError(
            "refusing to delete goal templates referenced by athletes: "
            + ", ".join(sorted(set(references)))
        )

    bind.execute(
        goal_contexts.delete().where(goal_contexts.c.goal_template_id.in_(goal_ids))
    )
    bind.execute(goal_templates.delete().where(goal_templates.c.id.in_(goal_ids)))


def downgrade() -> None:
    raise RuntimeError(
        "0036 permanently deletes obsolete goal templates; restore a database backup "
        "to reverse it"
    )
