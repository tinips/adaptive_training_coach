"""Remove original-description editing from profile settings.

Revision ID: 0021_remove_goal_description
Revises: 0020_add_goal_menu
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0021_remove_goal_description"
down_revision: str | None = "0020_add_goal_menu"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_WITH_DESCRIPTION = (
    "'MENU','GOAL_MENU','GOAL_MAIN','GOAL_OUTCOME','GOAL_DATE','GOAL_SECONDARY',"
    "'GOAL_DESCRIPTION','AVAILABILITY','EQUIPMENT','HEALTH','PERSONAL_MENU',"
    "'PERSONAL_BIRTH_YEAR','PERSONAL_GENDER','PERSONAL_WEIGHT','PERSONAL_HEIGHT'"
)
_WITHOUT_DESCRIPTION = (
    "'MENU','GOAL_MENU','GOAL_MAIN','GOAL_OUTCOME','GOAL_DATE','GOAL_SECONDARY',"
    "'AVAILABILITY','EQUIPMENT','HEALTH','PERSONAL_MENU','PERSONAL_BIRTH_YEAR',"
    "'PERSONAL_GENDER','PERSONAL_WEIGHT','PERSONAL_HEIGHT'"
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
    op.execute(
        sa.text(
            "UPDATE profile_settings_sessions SET current_step = 'GOAL_MENU', "
            "pending_answers = '{}' WHERE current_step = 'GOAL_DESCRIPTION'"
        )
    )
    _replace_constraint(_WITHOUT_DESCRIPTION)


def downgrade() -> None:
    _replace_constraint(_WITH_DESCRIPTION)
