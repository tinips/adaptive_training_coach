"""Keep automated tests isolated from optional local runtime configuration."""

from __future__ import annotations

import os

# Import-time application objects read Settings before fixtures can run. A
# developer's ignored .env may contain a display name or an unfinished value,
# so pin only this public, non-secret field for the synthetic test process.
os.environ.setdefault("TELEGRAM_BOT_USERNAME", "adaptive_training_coach_bot")
