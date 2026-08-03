"""Remove the legacy onboarding path after conversational goal confirmation.

Revision ID: 0008_remove_legacy_onboarding
Revises: 0007_conversational_goal
Create Date: 2026-08-03
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0008_remove_legacy_onboarding"
down_revision: str | None = "0007_conversational_goal"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LEGACY_ONBOARDING_STEPS = (
    "CONSENT",
    "PRIMARY_SPORT",
    "GOAL_TYPE",
    "EVENT_STATUS",
    "EVENT_NAME",
    "EVENT_DATE",
    "GOAL_PRIORITY",
    "AGE",
    "HEIGHT",
    "WEIGHT",
    "TRAINING_DAYS",
    "WEEKDAY_DURATION",
    "WEEKEND_DURATION",
    "EQUIPMENT",
    "POOL_ACCESS",
    "BIKE_ACCESS",
    "HEALTH_AREAS",
    "HEALTH_TIMING",
    "HEALTH_DESCRIPTION",
    "COACH_TONE",
    "COACH_DETAIL",
    "BASELINE_SOURCE",
    "APPLE_HEALTH_PRIVACY_NOTICE",
    "APPLE_HEALTH_WAITING_FOR_FILE",
    "APPLE_HEALTH_PROCESSING",
    "APPLE_HEALTH_IMPORT_COMPLETE",
    "APPLE_HEALTH_IMPORT_FAILED",
    "FILE_IMPORT_WAITING",
    "FILE_IMPORT_PROCESSING",
    "FILE_IMPORT_COMPLETE",
    "SUMMARY",
)
RETAINED_ONBOARDING_STEPS = (
    "CONSENT",
    "SETUP_INTRODUCTION",
    "GOAL_INTAKE",
    "GOAL_CONFIRMED",
)
BRIDGE_ONBOARDING_STEPS = tuple(
    dict.fromkeys((*LEGACY_ONBOARDING_STEPS, *RETAINED_ONBOARDING_STEPS))
)
LEGACY_ONBOARDING_STATUSES = ("ACTIVE", "COMPLETED", "CANCELLED")
RETAINED_ONBOARDING_STATUSES = ("ACTIVE", "CANCELLED")

_JSON = sa.JSON().with_variant(
    postgresql.JSONB(astext_type=sa.Text()),
    "postgresql",
)
_SESSIONS = sa.table(
    "onboarding_sessions",
    sa.column("id", sa.Uuid()),
    sa.column("user_id", sa.Uuid()),
    sa.column("status", sa.String(16)),
    sa.column("current_step", sa.String(32)),
    sa.column("answers", _JSON),
)
_GOALS = sa.table(
    "training_goals",
    sa.column("user_id", sa.Uuid()),
    sa.column("main_goal", sa.String(500)),
    sa.column("target_outcome", sa.String(500)),
    sa.column("original_description", sa.Text()),
)


def _quoted(values: Sequence[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _replace_enum_check(
    table: str,
    column: str,
    constraint: str,
    values: Sequence[str],
    *,
    length: int,
    nullable: bool = False,
) -> None:
    with op.batch_alter_table(table) as batch:
        batch.drop_constraint(op.f(constraint), type_="check")
        batch.alter_column(
            column,
            existing_type=sa.String(),
            type_=sa.String(length=length),
            existing_nullable=nullable,
        )
        batch.create_check_constraint(
            op.f(constraint),
            f"{column} IN ({_quoted(values)})",
        )


def _dict(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in value.items()}


def _retained_goal_answers(source: object) -> dict[str, Any]:
    answers = _dict(source)
    retained: dict[str, Any] = {}
    if answers.get("consent") is True:
        retained["consent"] = True
    raw_text = answers.get("raw_goal_text")
    if isinstance(raw_text, str) and raw_text:
        retained["raw_goal_text"] = raw_text
    messages = answers.get("goal_messages")
    if isinstance(messages, list):
        safe_messages = [item for item in messages if isinstance(item, str)]
        if safe_messages:
            retained["goal_messages"] = safe_messages
    draft = answers.get("goal_draft")
    if isinstance(draft, Mapping):
        retained["goal_draft"] = dict(draft)
    field = answers.get("_goal_clarification_field")
    if field in {"main_goal", "event_date", "target_outcome"}:
        retained["_goal_clarification_field"] = field
    hint = answers.get("_goal_clarification_hint")
    if isinstance(hint, str) and hint:
        retained["_goal_clarification_hint"] = hint
    phase = answers.get("_goal_intake_phase")
    if phase in {"COLLECTING", "CLARIFYING", "CONFIRMING", "ADDING"}:
        retained["_goal_intake_phase"] = phase
    return retained


def _normalize_sessions() -> None:
    connection = op.get_bind()
    rows = connection.execute(
        sa.select(
            _SESSIONS.c.id,
            _SESSIONS.c.status,
            _SESSIONS.c.answers,
            _GOALS.c.main_goal,
            _GOALS.c.target_outcome,
            _GOALS.c.original_description,
        ).select_from(
            _SESSIONS.outerjoin(_GOALS, _GOALS.c.user_id == _SESSIONS.c.user_id)
        )
    ).all()
    for row in rows:
        original = _dict(row.answers)
        answers = _retained_goal_answers(original)
        has_consent = answers.get("consent") is True
        has_confirmed_goal = (
            isinstance(row.main_goal, str)
            and bool(row.main_goal.strip())
            and isinstance(row.target_outcome, str)
            and bool(row.target_outcome.strip())
            and isinstance(row.original_description, str)
            and bool(row.original_description.strip())
        ) or original.get("_conversational_goal_confirmed") is True

        if not has_consent:
            step = "CONSENT"
            answers = {}
        elif has_confirmed_goal:
            step = "GOAL_CONFIRMED"
            for key in (
                "goal_draft",
                "_goal_intake_phase",
                "_goal_clarification_field",
                "_goal_clarification_hint",
            ):
                answers.pop(key, None)
        elif isinstance(answers.get("goal_draft"), Mapping) or any(
            key in answers for key in ("raw_goal_text", "goal_messages")
        ):
            step = "GOAL_INTAKE"
            if "_goal_intake_phase" not in answers:
                draft = _dict(answers.get("goal_draft"))
                answers["_goal_intake_phase"] = (
                    "CONFIRMING"
                    if draft.get("message_status") == "COMPLETE"
                    else "COLLECTING"
                )
        elif original.get("_setup_introduction_pending") is True:
            step = "SETUP_INTRODUCTION"
        else:
            step = "GOAL_INTAKE"
            answers["_goal_intake_phase"] = "COLLECTING"

        connection.execute(
            _SESSIONS.update()
            .where(_SESSIONS.c.id == row.id)
            .values(
                status="CANCELLED" if row.status == "CANCELLED" else "ACTIVE",
                current_step=step,
                answers=answers,
            )
        )


def upgrade() -> None:
    """Retain only the consent, introduction, and goal checkpoints."""

    _replace_enum_check(
        "onboarding_sessions",
        "current_step",
        "ck_onboarding_sessions_onboarding_step",
        BRIDGE_ONBOARDING_STEPS,
        length=32,
    )
    _replace_enum_check(
        "llm_usage",
        "onboarding_step",
        "ck_llm_usage_llm_onboarding_step",
        BRIDGE_ONBOARDING_STEPS,
        length=32,
    )

    _normalize_sessions()
    op.execute(
        sa.text(
            "UPDATE llm_usage SET onboarding_step = 'GOAL_INTAKE' "
            "WHERE onboarding_step NOT IN "
            "('CONSENT', 'SETUP_INTRODUCTION', 'GOAL_INTAKE', 'GOAL_CONFIRMED')"
        )
    )
    op.execute(
        sa.text("UPDATE onboarding_sessions SET status = 'ACTIVE' WHERE status = 'COMPLETED'")
    )

    _replace_enum_check(
        "llm_usage",
        "onboarding_step",
        "ck_llm_usage_llm_onboarding_step",
        RETAINED_ONBOARDING_STEPS,
        length=32,
    )
    _replace_enum_check(
        "onboarding_sessions",
        "current_step",
        "ck_onboarding_sessions_onboarding_step",
        RETAINED_ONBOARDING_STEPS,
        length=32,
    )
    _replace_enum_check(
        "onboarding_sessions",
        "status",
        "ck_onboarding_sessions_onboarding_status",
        RETAINED_ONBOARDING_STATUSES,
        length=16,
    )

    with op.batch_alter_table("onboarding_sessions") as batch:
        batch.drop_constraint(
            op.f("ck_onboarding_sessions_pending_onboarding_step"),
            type_="check",
        )
        batch.drop_column("pending_free_text_step")
        batch.drop_column("pending_parsed_value")
        batch.drop_column("return_to_summary")
        batch.drop_column("completed_at")

    with op.batch_alter_table("training_goals") as batch:
        batch.alter_column(
            "goal_type",
            existing_type=sa.String(32),
            nullable=True,
        )
        batch.alter_column(
            "goal_priority",
            existing_type=sa.String(24),
            nullable=True,
        )

    with op.batch_alter_table("apple_health_import_jobs") as batch:
        batch.drop_constraint(
            op.f(
                "fk_apple_health_import_jobs_onboarding_session_id_onboarding_sessions"
            ),
            type_="foreignkey",
        )
        batch.drop_constraint(
            op.f("ck_apple_health_import_jobs_training_import_context"),
            type_="check",
        )
        batch.drop_column("onboarding_session_id")
        batch.drop_column("context")

    with op.batch_alter_table("workout_flow_sessions") as batch:
        batch.drop_column("return_to_onboarding")


def downgrade() -> None:
    """Restore legacy columns and map retained checkpoints to PRIMARY_SPORT."""

    _replace_enum_check(
        "onboarding_sessions",
        "current_step",
        "ck_onboarding_sessions_onboarding_step",
        BRIDGE_ONBOARDING_STEPS,
        length=32,
    )
    _replace_enum_check(
        "llm_usage",
        "onboarding_step",
        "ck_llm_usage_llm_onboarding_step",
        BRIDGE_ONBOARDING_STEPS,
        length=32,
    )
    _replace_enum_check(
        "onboarding_sessions",
        "status",
        "ck_onboarding_sessions_onboarding_status",
        LEGACY_ONBOARDING_STATUSES,
        length=16,
    )

    with op.batch_alter_table("onboarding_sessions") as batch:
        batch.add_column(
            sa.Column("pending_free_text_step", sa.String(32), nullable=True)
        )
        batch.add_column(sa.Column("pending_parsed_value", _JSON, nullable=True))
        batch.add_column(
            sa.Column(
                "return_to_summary",
                sa.Boolean(),
                server_default=sa.text("false"),
                nullable=False,
            )
        )
        batch.add_column(
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.create_check_constraint(
            op.f("ck_onboarding_sessions_pending_onboarding_step"),
            "pending_free_text_step IS NULL OR "
            f"pending_free_text_step IN ({_quoted(LEGACY_ONBOARDING_STEPS)})",
        )

    op.execute(
        sa.text(
            "UPDATE onboarding_sessions SET current_step = 'PRIMARY_SPORT' "
            "WHERE current_step IN "
            "('SETUP_INTRODUCTION', 'GOAL_INTAKE', 'GOAL_CONFIRMED')"
        )
    )
    op.execute(
        sa.text(
            "UPDATE llm_usage SET onboarding_step = 'PRIMARY_SPORT' "
            "WHERE onboarding_step IN "
            "('SETUP_INTRODUCTION', 'GOAL_INTAKE', 'GOAL_CONFIRMED')"
        )
    )

    _replace_enum_check(
        "llm_usage",
        "onboarding_step",
        "ck_llm_usage_llm_onboarding_step",
        LEGACY_ONBOARDING_STEPS,
        length=32,
    )
    _replace_enum_check(
        "onboarding_sessions",
        "current_step",
        "ck_onboarding_sessions_onboarding_step",
        LEGACY_ONBOARDING_STEPS,
        length=32,
    )

    op.execute(
        sa.text(
            "UPDATE training_goals SET goal_type = 'OTHER' WHERE goal_type IS NULL"
        )
    )
    op.execute(
        sa.text(
            "UPDATE training_goals SET goal_priority = 'OTHER' "
            "WHERE goal_priority IS NULL"
        )
    )
    with op.batch_alter_table("training_goals") as batch:
        batch.alter_column(
            "goal_type",
            existing_type=sa.String(32),
            nullable=False,
        )
        batch.alter_column(
            "goal_priority",
            existing_type=sa.String(24),
            nullable=False,
        )

    with op.batch_alter_table("apple_health_import_jobs") as batch:
        batch.add_column(sa.Column("onboarding_session_id", sa.Uuid(), nullable=True))
        batch.add_column(
            sa.Column(
                "context",
                sa.String(16),
                server_default="DAILY",
                nullable=False,
            )
        )
        batch.create_check_constraint(
            op.f("ck_apple_health_import_jobs_training_import_context"),
            "context IN ('ONBOARDING', 'DAILY')",
        )
        batch.create_foreign_key(
            op.f(
                "fk_apple_health_import_jobs_onboarding_session_id_onboarding_sessions"
            ),
            "onboarding_sessions",
            ["onboarding_session_id"],
            ["id"],
            ondelete="SET NULL",
        )

    with op.batch_alter_table("workout_flow_sessions") as batch:
        batch.add_column(
            sa.Column(
                "return_to_onboarding",
                sa.Boolean(),
                server_default=sa.text("false"),
                nullable=False,
            )
        )
