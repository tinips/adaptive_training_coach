"""Restrict athlete sex to male or female and reset affected onboarding data.

Revision ID: 0016_restrict_athlete_gender
Revises: 0015_profile_settings_session
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0016_restrict_athlete_gender"
down_revision: str | None = "0015_profile_settings_session"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_AFFECTED_USERS = (
    "(SELECT user_id FROM athlete_profiles WHERE gender = 'OTHER_UNSPECIFIED')"
)


def upgrade() -> None:
    """Reset affected onboarding records before tightening the check constraint."""

    op.execute(
        sa.text(
            "UPDATE onboarding_sessions SET status = 'ACTIVE', "
            "current_step = 'CONSENT', answers = '{}' "
            f"WHERE user_id IN {_AFFECTED_USERS}"
        )
    )
    op.execute(
        sa.text(
            "UPDATE users SET status = 'ONBOARDING_IN_PROGRESS' "
            f"WHERE id IN {_AFFECTED_USERS}"
        )
    )
    op.execute(
        sa.text(
            f"DELETE FROM profile_settings_sessions WHERE user_id IN {_AFFECTED_USERS}"
        )
    )
    # Deleting goals cascades their athlete-specific equipment selections.
    op.execute(
        sa.text(f"DELETE FROM training_goals WHERE user_id IN {_AFFECTED_USERS}")
    )
    op.execute(
        sa.text("DELETE FROM athlete_profiles WHERE gender = 'OTHER_UNSPECIFIED'")
    )

    op.execute(
        sa.text(
            "ALTER TABLE athlete_profiles DROP CONSTRAINT "
            "ck_athlete_profiles_athlete_gender"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE athlete_profiles ADD CONSTRAINT "
            "ck_athlete_profiles_athlete_gender CHECK "
            "(gender IS NULL OR gender IN ('MALE', 'FEMALE'))"
        )
    )


def downgrade() -> None:
    """Restore only the former database constraint; deleted data is unrecoverable."""

    op.execute(
        sa.text(
            "ALTER TABLE athlete_profiles DROP CONSTRAINT "
            "ck_athlete_profiles_athlete_gender"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE athlete_profiles ADD CONSTRAINT "
            "ck_athlete_profiles_athlete_gender CHECK "
            "(gender IS NULL OR gender IN ('MALE', 'FEMALE', "
            "'OTHER_UNSPECIFIED'))"
        )
    )
