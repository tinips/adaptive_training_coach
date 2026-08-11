"""Allow editing every athlete-owned training-goal text field.

Revision ID: 0019_expand_goal_settings
Revises: 0018_remove_obsolete_equipment
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0019_expand_goal_settings"
down_revision: str | None = "0018_remove_obsolete_equipment"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_STEPS = (
    "'MENU','GOAL_MAIN','GOAL_OUTCOME','GOAL_DATE','AVAILABILITY','EQUIPMENT',"
    "'HEALTH','PERSONAL_MENU','PERSONAL_BIRTH_YEAR','PERSONAL_GENDER',"
    "'PERSONAL_WEIGHT','PERSONAL_HEIGHT'"
)
_NEW_STEPS = (
    "'MENU','GOAL_MAIN','GOAL_OUTCOME','GOAL_DATE','GOAL_SECONDARY',"
    "'GOAL_DESCRIPTION','AVAILABILITY','EQUIPMENT','HEALTH','PERSONAL_MENU',"
    "'PERSONAL_BIRTH_YEAR','PERSONAL_GENDER','PERSONAL_WEIGHT','PERSONAL_HEIGHT'"
)


def _replace_constraint(values: str) -> None:
    with op.batch_alter_table("profile_settings_sessions") as batch:
        batch.drop_constraint(
            op.f("ck_profile_settings_sessions_profile_settings_step"),
            type_="check",
        )
        batch.create_check_constraint(
            "profile_settings_step",
            f"current_step IN ({values})",
        )


def upgrade() -> None:
    _replace_constraint(_NEW_STEPS)


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE profile_settings_sessions SET current_step = 'MENU', "
            "pending_answers = '{}' WHERE current_step IN "
            "('GOAL_SECONDARY', 'GOAL_DESCRIPTION')"
        )
    )
    _replace_constraint(_OLD_STEPS)
