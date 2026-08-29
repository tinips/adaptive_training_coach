"""Remove the legacy primary_sport column from athlete_profiles.

The PRIMARY_SPORT onboarding step was removed in 0008_remove_legacy_onboarding
and replaced by the deterministic profile intake added in 0009_mandatory_profile
(gender, birth_year). No code path has written athlete_profiles.primary_sport
since, but the column was left behind as NOT NULL, making every profile insert
fail with an IntegrityError.

Revision ID: 0031_remove_legacy_primary_sport
Revises: 0030_mobile_sync_credentials
Create Date: 2026-08-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0031_remove_legacy_primary_sport"
down_revision: str | None = "0030_mobile_sync_credentials"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PRIMARY_SPORT_VALUES = (
    "RUNNING",
    "CYCLING",
    "TRIATHLON",
    "SWIMMING",
    "GENERAL_FITNESS",
    "OTHER",
)


def upgrade() -> None:
    """Drop the unusable legacy primary_sport column and its check constraint."""
    # Use conditional logic to handle cases where constraint/column may not exist
    conn = op.get_bind()
    
    # Check if the column exists before dropping
    inspector = sa.inspect(conn)
    columns = [c["name"] for c in inspector.get_columns("athlete_profiles")]
    
    if "primary_sport" in columns:
        with op.batch_alter_table("athlete_profiles") as batch:
            # Try to drop constraint if it exists
            constraints = [c["name"] for c in inspector.get_check_constraints("athlete_profiles")]
            if "ck_athlete_profiles_primary_sport" in constraints:
                batch.drop_constraint(
                    "ck_athlete_profiles_primary_sport",
                    type_="check",
                )
            batch.drop_column("primary_sport")


def downgrade() -> None:
    """Restore the legacy primary_sport column with a neutral backfill value."""

    with op.batch_alter_table("athlete_profiles") as batch:
        batch.add_column(
            sa.Column(
                "primary_sport",
                sa.String(length=24),
                nullable=False,
                server_default="OTHER",
            )
        )
        batch.alter_column("primary_sport", server_default=None)
        batch.create_check_constraint(
            op.f("ck_athlete_profiles_primary_sport"),
            "primary_sport IN ({values})".format(
                values=", ".join(f"'{value}'" for value in PRIMARY_SPORT_VALUES)
            ),
        )
