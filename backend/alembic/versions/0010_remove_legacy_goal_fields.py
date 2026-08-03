"""Remove redundant legacy training-goal fields.

Revision ID: 0010_remove_legacy_goal_fields
Revises: 0009_mandatory_profile
Create Date: 2026-08-03
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0010_remove_legacy_goal_fields"
down_revision: str | None = "0009_mandatory_profile"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

GOAL_TYPES = (
    "FIVE_K",
    "TEN_K",
    "HALF_MARATHON",
    "MARATHON",
    "TRAIL",
    "CYCLING_EVENT",
    "GRAN_FONDO",
    "SPRINT_TRIATHLON",
    "OLYMPIC_TRIATHLON",
    "HALF_IRONMAN_70_3",
    "IRONMAN",
    "FIRST_TRIATHLON",
    "IMPROVE_TECHNIQUE",
    "OPEN_WATER_SWIMMING",
    "SPECIFIC_EVENT",
    "GENERAL_HEALTH",
    "IMPROVE_ENDURANCE",
    "IMPROVE_PERFORMANCE",
    "LOSE_BODY_FAT",
    "BUILD_STRENGTH",
    "OTHER",
)
GOAL_PRIORITIES = (
    "FINISH_SAFELY",
    "PERSONAL_BEST",
    "TARGET_TIME",
    "HEALTH_CONSISTENCY",
    "OTHER",
)
GOAL_LABELS = {
    "FIVE_K": "Complete a 5K",
    "TEN_K": "Complete a 10K",
    "HALF_MARATHON": "Complete a half marathon",
    "MARATHON": "Complete a marathon",
    "TRAIL": "Complete a trail event",
    "CYCLING_EVENT": "Complete a cycling event",
    "GRAN_FONDO": "Complete a gran fondo",
    "SPRINT_TRIATHLON": "Complete a sprint triathlon",
    "OLYMPIC_TRIATHLON": "Complete an Olympic triathlon",
    "HALF_IRONMAN_70_3": "Complete an Ironman 70.3",
    "IRONMAN": "Complete an Ironman",
    "FIRST_TRIATHLON": "Complete a first triathlon",
    "IMPROVE_TECHNIQUE": "Improve technique",
    "OPEN_WATER_SWIMMING": "Improve open-water swimming",
    "SPECIFIC_EVENT": "Prepare for a specific event",
    "GENERAL_HEALTH": "Improve general health",
    "IMPROVE_ENDURANCE": "Improve endurance",
    "IMPROVE_PERFORMANCE": "Improve performance",
    "LOSE_BODY_FAT": "Reduce body fat",
    "BUILD_STRENGTH": "Build strength",
    "OTHER": "Achieve the stated training goal",
}
PRIORITY_LABELS = {
    "FINISH_SAFELY": "Finish safely",
    "PERSONAL_BEST": "Achieve a personal best",
    "TARGET_TIME": "Reach the target time",
    "HEALTH_CONSISTENCY": "Improve health and consistency",
    "OTHER": "Achieve the stated goal",
}

_GOALS = sa.table(
    "training_goals",
    sa.column("id", sa.Uuid()),
    sa.column("goal_type", sa.String(32)),
    sa.column("event_name", sa.String(255)),
    sa.column("goal_priority", sa.String(24)),
    sa.column("main_goal", sa.String(500)),
    sa.column("target_outcome", sa.String(500)),
    sa.column("original_description", sa.Text()),
)


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _backfill_canonical_goals() -> None:
    connection = op.get_bind()
    rows = connection.execute(sa.select(_GOALS)).mappings().all()
    for raw_row in rows:
        row: Mapping[str, Any] = raw_row
        event_name = _text(row["event_name"])
        goal_type = _text(row["goal_type"])
        main_goal = (
            _text(row["main_goal"])
            or event_name
            or GOAL_LABELS.get(goal_type or "")
            or "Achieve the stated training goal"
        )
        target_outcome = (
            _text(row["target_outcome"])
            or PRIORITY_LABELS.get(_text(row["goal_priority"]) or "")
            or "Achieve the stated goal"
        )
        original_description = (
            _text(row["original_description"]) or event_name or main_goal
        )
        connection.execute(
            _GOALS.update()
            .where(_GOALS.c.id == row["id"])
            .values(
                main_goal=main_goal,
                target_outcome=target_outcome,
                original_description=original_description,
            )
        )


def _quoted(values: Sequence[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    """Backfill canonical data before removing all three legacy columns."""

    _backfill_canonical_goals()
    with op.batch_alter_table("training_goals") as batch:
        batch.drop_constraint(
            op.f("ck_training_goals_goal_type"),
            type_="check",
        )
        batch.drop_constraint(
            op.f("ck_training_goals_goal_priority"),
            type_="check",
        )
        batch.alter_column(
            "main_goal",
            existing_type=sa.String(length=500),
            nullable=False,
        )
        batch.alter_column(
            "target_outcome",
            existing_type=sa.String(length=500),
            nullable=False,
        )
        batch.alter_column(
            "original_description",
            existing_type=sa.Text(),
            nullable=False,
        )
        batch.drop_column("goal_priority")
        batch.drop_column("event_name")
        batch.drop_column("goal_type")


def downgrade() -> None:
    """Restore nullable legacy columns with neutral compatibility values."""

    with op.batch_alter_table("training_goals") as batch:
        batch.add_column(sa.Column("goal_type", sa.String(32), nullable=True))
        batch.add_column(sa.Column("event_name", sa.String(255), nullable=True))
        batch.add_column(sa.Column("goal_priority", sa.String(24), nullable=True))
        batch.alter_column(
            "main_goal",
            existing_type=sa.String(length=500),
            nullable=True,
        )
        batch.alter_column(
            "target_outcome",
            existing_type=sa.String(length=500),
            nullable=True,
        )
        batch.alter_column(
            "original_description",
            existing_type=sa.Text(),
            nullable=True,
        )
        batch.create_check_constraint(
            op.f("ck_training_goals_goal_type"),
            f"goal_type IS NULL OR goal_type IN ({_quoted(GOAL_TYPES)})",
        )
        batch.create_check_constraint(
            op.f("ck_training_goals_goal_priority"),
            "goal_priority IS NULL OR "
            f"goal_priority IN ({_quoted(GOAL_PRIORITIES)})",
        )

    op.execute(
        sa.text(
            "UPDATE training_goals SET goal_type = 'OTHER', "
            "event_name = main_goal, goal_priority = 'OTHER'"
        )
    )
