"""Version self-reported baseline payloads with training preferences.

Revision ID: 0047_add_training_preferences
Revises: 0046_structured_availability
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "0047_add_training_preferences"
down_revision: str | None = "0046_structured_availability"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Preferences are additive fields inside existing baseline_jsonb payloads."""


def downgrade() -> None:
    """No relational schema was changed by this payload-only revision."""
