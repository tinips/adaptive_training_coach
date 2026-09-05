"""Read-only ad hoc sample: "view my zones" output for the fit triathlete.

Reuses Athlete A's exact fixture data from baseline_adaptation_test.py
(ATHLETES["A"], `_seed_athlete`) -- does not invent a new athlete. Seeds the
athlete only if not already present (idempotent, read-friendly), then calls
AccountQueryService.zones() and prints messages.zones_view() on the result.

No LLM call is made: AccountQueryService.zones() never touches a model.

Usage (from repo root, DB must be up and migrated):
  docker run --rm --network adaptive_training_coach_default \\
    --env-file .env \\
    -e DATABASE_URL=postgresql+asyncpg://coach:coach@db:5432/adaptive_coach \\
    -v "$(pwd)/backend/scripts:/app/scripts" \\
    adaptive-training-coach-backend:local \\
    python /app/scripts/view_my_zones_sample.py
"""

from __future__ import annotations

import asyncio
import sys

# Invoked directly as `python /app/scripts/view_my_zones_sample.py` (mirroring
# baseline_adaptation_test.py's own invocation pattern), so sys.path[0] is
# /app/scripts -- import its sibling module as a top-level module, not via
# a `scripts.` package prefix (no __init__.py makes /app/scripts a package
# under /app, which is not itself on sys.path in that invocation).
from baseline_adaptation_test import ATHLETES, _seed_athlete

from app.bot import messages
from app.config import get_settings
from app.db.session import create_engine, create_session_factory
from app.repositories.users import UserRepository
from app.schemas.common import TelegramIdentity
from app.services.accounts.service import AccountQueryService


async def run() -> int:
    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)

    spec = ATHLETES["A"]
    identity = TelegramIdentity(
        telegram_user_id=spec["telegram_user_id"],
        telegram_username=spec["telegram_username"],
        first_name="TestA",
        language_code="en",
    )

    async with session_factory() as session:
        existing = await UserRepository(session).get_by_telegram_id(
            identity.telegram_user_id
        )

    if existing is None:
        print(
            f"Athlete A not found (telegram_user_id={identity.telegram_user_id}); "
            "seeding."
        )
        async with session_factory() as session, session.begin():
            await _seed_athlete(session, "A", spec)
    else:
        print(
            f"Athlete A already seeded (user_id={existing.id}); reusing existing row."
        )

    account_queries = AccountQueryService(session_factory)
    zones = await account_queries.zones(identity)
    if zones is None:
        print("ABORT: AccountQueryService.zones() returned None.", file=sys.stderr)
        await engine.dispose()
        return 1

    print("\n--- messages.zones_view(zones) output ---\n")
    print(messages.zones_view(zones))
    print("\n--- end output ---")

    await engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
