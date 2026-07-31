"""Use exact workout identity and remove persisted HR confidence metadata.

Revision ID: 0006_exact_workout_identity
Revises: 0005_remove_hr_observations
Create Date: 2026-07-30
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_exact_workout_identity"
down_revision: str | None = "0005_remove_hr_observations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

HEART_RATE_SOURCES = (
    "MEASURED_SENSOR",
    "PROVIDER_SUMMARY",
    "DERIVED",
    "USER_REPORTED",
    "UNAVAILABLE",
)
HEART_RATE_QUALITIES = (
    "EXACT_SAMPLE",
    "SHORT_INTERVAL",
    "COARSE_INTERVAL",
    "MANUAL",
    "UNKNOWN",
)


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    """Remove source-link fields that existed only for HR confidence scoring."""

    with op.batch_alter_table("activity_source_links") as batch_op:
        batch_op.drop_constraint(
            op.f("ck_activity_source_links_source_link_heart_rate_quality"),
            type_="check",
        )
        batch_op.drop_constraint(
            op.f("ck_activity_source_links_source_link_heart_rate_source"),
            type_="check",
        )
        batch_op.drop_column("heart_rate_sample_count")
        batch_op.drop_column("heart_rate_reliable")
        batch_op.drop_column("heart_rate_quality")
        batch_op.drop_column("heart_rate_source")


def downgrade() -> None:
    """Restore the prior confidence columns with neutral defaults."""

    with op.batch_alter_table("activity_source_links") as batch_op:
        batch_op.add_column(
            sa.Column(
                "heart_rate_source",
                sa.String(length=24),
                server_default="UNAVAILABLE",
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "heart_rate_quality",
                sa.String(length=24),
                server_default="UNKNOWN",
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "heart_rate_reliable",
                sa.Boolean(),
                server_default=sa.text("false"),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "heart_rate_sample_count",
                sa.Integer(),
                server_default="0",
                nullable=False,
            )
        )
        batch_op.create_check_constraint(
            op.f("ck_activity_source_links_source_link_heart_rate_source"),
            f"heart_rate_source IN ({_quoted(HEART_RATE_SOURCES)})",
        )
        batch_op.create_check_constraint(
            op.f("ck_activity_source_links_source_link_heart_rate_quality"),
            f"heart_rate_quality IN ({_quoted(HEART_RATE_QUALITIES)})",
        )
    _restore_migration_snapshot_values()


def _restore_migration_snapshot_values() -> None:
    """Restore 0004 values when its provenance snapshot is still available."""

    bind = op.get_bind()
    links = sa.Table(
        "activity_source_links",
        sa.MetaData(),
        autoload_with=bind,
    )
    rows = bind.execute(
        sa.select(
            links.c.id,
            links.c.source_metadata_jsonb,
        )
    ).mappings()
    for row in rows:
        metadata = row["source_metadata_jsonb"]
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except json.JSONDecodeError:
                continue
        if not isinstance(metadata, Mapping):
            continue
        provenance = metadata.get("migration_provenance", metadata)
        if not isinstance(provenance, Mapping):
            continue
        snapshot = provenance.get("canonical_snapshot")
        if not isinstance(snapshot, Mapping):
            continue
        source_links = snapshot.get("source_links")
        if not isinstance(source_links, list):
            continue
        row_id = str(row["id"]).replace("-", "")
        prior = next(
            (
                item
                for item in source_links
                if isinstance(item, Mapping)
                and str(item.get("id", "")).replace("-", "") == row_id
            ),
            None,
        )
        if not isinstance(prior, Mapping):
            continue
        values = {
            key: prior[key]
            for key in (
                "heart_rate_source",
                "heart_rate_quality",
                "heart_rate_reliable",
                "heart_rate_sample_count",
            )
            if key in prior
        }
        if values:
            bind.execute(
                sa.update(links).where(links.c.id == row["id"]).values(**values)
            )
