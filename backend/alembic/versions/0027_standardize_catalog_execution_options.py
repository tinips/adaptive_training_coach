"""Remove the invalid ROWING_REGATTA to HYROX context relation."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0027_catalog_option_standard"
down_revision: str | None = "0026_complete_hyrox_catalog"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    goals = sa.table(
        "goal_templates", sa.column("id", sa.Uuid()), sa.column("code", sa.String())
    )
    contexts = sa.table(
        "training_contexts",
        sa.column("id", sa.Uuid()),
        sa.column("code", sa.String()),
    )
    links = sa.table(
        "goal_template_contexts",
        sa.column("goal_template_id", sa.Uuid()),
        sa.column("training_context_id", sa.Uuid()),
    )
    rowing_goal_id = sa.select(goals.c.id).where(goals.c.code == "ROWING_REGATTA")
    hyrox_row_id = sa.select(contexts.c.id).where(contexts.c.code == "hyrox_row")
    bind.execute(
        links.delete().where(
            links.c.goal_template_id.in_(rowing_goal_id),
            links.c.training_context_id.in_(hyrox_row_id),
        )
    )


def downgrade() -> None:
    raise RuntimeError(
        "0027 removes an invalid generated catalog relation; restore the catalog "
        "from a backup instead of recreating it automatically"
    )
