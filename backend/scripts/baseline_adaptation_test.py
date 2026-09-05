"""Live baseline-adaptation harness: fit triathlete vs. near-beginner.

Generates real first-week menus for two contrasting athlete profiles through
the live FIRST_WEEK planner path (current model = whatever LLM_MODEL / live
provider is configured; prompt version is whatever FIRST_WEEK_PLANNER_PROMPT_VERSION
in app/workflows/prompts/weekly_planning.py currently says), using identical
code, identical prompt contract, identical week_start/timezone/availability --
only the athlete baseline (+ coaching style) differs. This is a verification
script, not a test: it makes real, paid LLM calls, and it clears any existing
plan for the same athlete/week before regenerating so re-running it always
exercises a fresh live call rather than returning a cached plan.

Every fixture value taken directly from the test brief is used as given.
Where the brief states something the schema has no field for, or the schema
requires a value the brief didn't state, that is called out explicitly in
the ASSUMPTIONS block below and again in the printed report -- nothing is
silently invented.

Usage (from repo root, DB must be up and migrated):
  docker run --rm --network adaptive_training_coach_default \\
    --env-file .env \\
    -e DATABASE_URL=postgresql+asyncpg://coach:coach@db:5432/adaptive_coach \\
    -v "$(pwd)/backend/scripts:/app/scripts" \\
    adaptive-training-coach-backend:local \\
    python /app/scripts/baseline_adaptation_test.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import AthleteCapability
from app.db.session import create_engine, create_session_factory
from app.domain.enums import (
    AthleteCapabilityStatus,
    AthleteGender,
    CoachingStyle,
    Discipline,
    UserStatus,
)
from app.integrations.llm.factory import create_goal_extraction_model
from app.repositories.athlete_baselines import AthleteBaselineRepository
from app.repositories.profiles import ProfileRepository
from app.repositories.users import UserRepository
from app.schemas.availability import (
    AvailabilityDay,
    AvailabilityWindow,
    ConfirmedWeeklyAvailability,
)
from app.schemas.baseline import (
    AthleteBaselineData,
    CyclingBaseline,
    RecentRaceResult,
    RunningBaseline,
    SwimmingBaseline,
    TrainingPreferences,
)
from app.schemas.common import TelegramIdentity
from app.services.weekly_planning.service import FirstWeekPlanner, _goal_signature
from app.training_catalog_seed import catalog_id

OUTPUT_DIR = Path(__file__).parent / "output"

SHARED_TIMEZONE = "Europe/Madrid"
SHARED_DEMOGRAPHICS = {  # held constant -- not specified in the brief
    "birth_year": 1990,
    "gender": AthleteGender.MALE,
    "weight_kg": 72.0,
    "height_cm": 176.0,
}
GOAL_TEMPLATE_ID = catalog_id("goal", "TRIATHLON_OLYMPIC")  # target: run+bike+swim
# target: strength
SUPPORTING_GOAL_TEMPLATE_ID = catalog_id("goal", "STRENGTH_MAINTENANCE")


def shared_availability() -> ConfirmedWeeklyAvailability:
    """Identical 6-day, all-discipline availability window for both athletes."""

    open_day = AvailabilityDay(
        available=True,
        disciplines=("running", "cycling", "swimming", "strength_training"),
        time_windows=(AvailabilityWindow(duration_minutes=90),),
    )
    rest_day = AvailabilityDay(available=False)
    return ConfirmedWeeklyAvailability(
        days={
            "monday": open_day,
            "tuesday": open_day,
            "wednesday": open_day,
            "thursday": open_day,
            "friday": open_day,
            "saturday": open_day,
            "sunday": rest_day,
        }
    )


ATHLETES = {
    "A": {
        "label": "Athlete A -- well-trained triathlete",
        "telegram_user_id": 900_001,
        "telegram_username": "baseline_test_athlete_a",
        "capabilities": (
            "running_shoes",
            "stationary_bike",
            "pool_access",
            "goggles",
            "gym_access",
        ),
        "baseline": AthleteBaselineData(
            running=RunningBaseline(
                typical_weekly_sessions=4,
                typical_weekly_duration_minutes=200,
                longest_recent_run_minutes=90,
                recent_race_result=RecentRaceResult(
                    distance_km=10.0, duration_seconds=42 * 60
                ),
            ),
            cycling=CyclingBaseline(
                typical_weekly_sessions=3,
                typical_weekly_duration_minutes=240,
                longest_recent_ride_minutes=120,
                riding_environment="INDOOR",
                riding_confidence="CONFIDENT",
                recent_ftp_watts=260,
            ),
            swimming=SwimmingBaseline(
                typical_weekly_sessions=2,
                # ASSUMED: brief gives no weekly swim minutes; ~45min/session.
                typical_weekly_duration_minutes=90,
                longest_continuous_swim_meters=1500,
                swimming_environment="POOL",
                pool_length_meters=25,  # ASSUMED: not stated in brief
                recent_400m_seconds=None,
            ),
            preferences=TrainingPreferences(
                coaching_style=CoachingStyle.DEMANDING,
                desired_weekly_sessions={
                    Discipline.RUNNING: 2,
                    Discipline.CYCLING: 2,
                    Discipline.SWIMMING: 2,
                    Discipline.STRENGTH: 2,
                },
                fits_availability=True,
            ),
        ),
        "notes": (
            "Swim 'CONFIDENT' and strength '1 session/wk, gym access' have no "
            "matching baseline field in this schema (no swim-confidence field, "
            "no strength baseline at all). Gym access is captured only via "
            "the gym_access capability; current strength volume is not "
            "captured anywhere."
        ),
    },
    "B": {
        "label": "Athlete B -- near-beginner",
        "telegram_user_id": 900_002,
        "telegram_username": "baseline_test_athlete_b",
        "capabilities": (
            "running_shoes",
            "stationary_bike",
            "pool_access",
            "goggles",
            # deliberately NO gym_access -- bodyweight-only per brief
        ),
        "baseline": AthleteBaselineData(
            running=RunningBaseline(
                typical_weekly_sessions=0,
                typical_weekly_duration_minutes=0,
                longest_recent_run_minutes=0,
                recent_race_result=None,
            ),
            cycling=CyclingBaseline(
                typical_weekly_sessions=1,
                typical_weekly_duration_minutes=40,
                longest_recent_ride_minutes=40,
                riding_environment="INDOOR",
                # ASSUMED MAPPING: brief says "NERVOUS"; schema has no such
                # literal (NEW_RIDER / SIMPLE_ROUTES / CONFIDENT /
                # NOT_CURRENTLY_RIDING). NEW_RIDER is the closest fit.
                riding_confidence="NEW_RIDER",
                recent_ftp_watts=None,
            ),
            swimming=SwimmingBaseline(
                typical_weekly_sessions=0,
                typical_weekly_duration_minutes=0,
                longest_continuous_swim_meters=25,
                swimming_environment="POOL",  # ASSUMED: not stated in brief
                pool_length_meters=25,  # ASSUMED: not stated in brief
                recent_400m_seconds=None,
            ),
            preferences=TrainingPreferences(
                coaching_style=CoachingStyle.CONSERVATIVE,
                desired_weekly_sessions={
                    Discipline.RUNNING: 2,
                    Discipline.CYCLING: 2,
                    Discipline.SWIMMING: 2,
                    Discipline.STRENGTH: 2,
                },
                fits_availability=True,
            ),
        ),
        "notes": (
            "Swim/water 'NERVOUS' has no matching baseline field (no "
            "swim-confidence field exists). Cycling 'NERVOUS' mapped to "
            "riding_confidence=NEW_RIDER as the closest available enum "
            "value -- schema has no literal NERVOUS. Strength 'no gym, "
            "bodyweight only' captured only as the *absence* of the "
            "gym_access capability; there is no explicit bodyweight-only "
            "flag, current strength volume is not captured at all."
        ),
    },
}


async def _seed_athlete(
    session: AsyncSession, key: str, spec: dict
) -> TelegramIdentity:
    identity = TelegramIdentity(
        telegram_user_id=spec["telegram_user_id"],
        telegram_username=spec["telegram_username"],
        first_name=f"Test{key}",
        language_code="en",
    )
    users = UserRepository(session)
    user, _ = await users.get_or_create(
        telegram_user_id=identity.telegram_user_id,
        telegram_username=identity.telegram_username,
        first_name=identity.first_name,
        timezone=SHARED_TIMEZONE,
    )
    user.status = UserStatus.PROFILE_COMPLETED
    await session.flush()

    profiles = ProfileRepository(session)
    await profiles.upsert_mandatory_athlete_profile(
        user_id=user.id,
        birth_year=SHARED_DEMOGRAPHICS["birth_year"],
        gender=SHARED_DEMOGRAPHICS["gender"],
        weight_kg=SHARED_DEMOGRAPHICS["weight_kg"],
        height_cm=SHARED_DEMOGRAPHICS["height_cm"],
    )
    await profiles.update_athlete_profile_context_fields(
        user_id=user.id,
        payload={
            "weekly_availability_jsonb": shared_availability().model_dump(mode="json"),
        },
    )
    goal = await profiles.upsert_training_goal(
        user_id=user.id,
        main_goal="Olympic-distance triathlon",
        event_date=None,
        secondary_priority=None,
        goal_template_id=GOAL_TEMPLATE_ID,
        supporting_goal_template_id=SUPPORTING_GOAL_TEMPLATE_ID,
    )

    # Idempotent re-seed: this script may be re-run against the same athlete
    # (e.g. to verify a fix), so clear any capability rows from a prior run
    # before re-adding the current spec's set.
    await session.execute(
        delete(AthleteCapability).where(AthleteCapability.athlete_id == user.id)
    )
    for code in spec["capabilities"]:
        session.add(
            AthleteCapability(
                athlete_id=user.id,
                capability_id=catalog_id("capability", code),
                status=AthleteCapabilityStatus.AVAILABLE,
            )
        )
    await session.flush()

    await AthleteBaselineRepository(session).upsert(
        athlete_id=user.id,
        goal_signature=_goal_signature(goal),
        baseline=spec["baseline"],
    )
    return identity


async def run() -> int:
    settings = get_settings()
    if settings.llm_mode != "live":
        print(
            f"ABORT: LLM_MODE={settings.llm_mode!r}, not 'live'. "
            "Refusing to fall back to mock output.",
            file=sys.stderr,
        )
        return 1
    if not settings.llm_api_key or not settings.llm_api_key.get_secret_value():
        print("ABORT: no LLM_API_KEY configured.", file=sys.stderr)
        return 1

    engine = create_engine(settings)
    session_factory = create_session_factory(engine)

    model_name = settings.first_week_llm_model or settings.llm_model
    print(f"Provider: mode=live model={model_name} base_url={settings.llm_base_url}")

    OUTPUT_DIR.mkdir(exist_ok=True)

    # Seed both athletes in one transaction so both read the exact same
    # committed catalog/goal state.
    async with session_factory() as session, session.begin():
        identities = {
            key: await _seed_athlete(session, key, spec)
            for key, spec in ATHLETES.items()
        }

    results: dict[str, dict] = {}
    for key, spec in ATHLETES.items():
        identity = identities[key]
        print(f"\n=== Generating live first-week plan for {spec['label']} ===")

        model = create_goal_extraction_model(settings, model_name=model_name)
        service = FirstWeekPlanner(
            session_factory=session_factory,
            settings=settings,
            model=model,
        )

        # A prior run for this athlete/week is unchanged input (same
        # input_digest), so _prepare would just return the cached plan
        # instead of generating fresh. Supersede it so this run is real.
        await service.delete_next_week(identity)

        # Count repair attempts by wrapping the module-level repair_plan the
        # service calls internally, without altering its behavior.
        import app.services.weekly_planning.service as svc_module

        repair_calls = {"n": 0}
        original_repair_plan = svc_module.repair_plan

        def counting_repair_plan(
            *args, __orig=original_repair_plan, __counter=repair_calls, **kwargs
        ):
            __counter["n"] += 1
            return __orig(*args, **kwargs)

        svc_module.repair_plan = counting_repair_plan
        try:
            prepared = await service._prepare(identity)
            if not hasattr(prepared, "prompt_context"):
                print(
                    f"ABORT for {key}: _prepare returned {prepared!r} "
                    "(not a preparable planning input) -- cannot generate.",
                    file=sys.stderr,
                )
                results[key] = {"error": f"prepare_failed:{prepared!r}"}
                continue

            try:
                result = await service.generate_next_week(identity)
            except Exception as exc:  # live provider call -- report, don't mock
                print(f"LIVE CALL FAILED for {key}: {type(exc).__name__}: {exc}")
                results[key] = {"error": f"{type(exc).__name__}: {exc}"}
                continue
        finally:
            svc_module.repair_plan = original_repair_plan

        record = {
            "label": spec["label"],
            "notes": spec["notes"],
            "generation_source": result.generation_source,
            "repair_call_count": repair_calls["n"],
            "week_start": prepared.week_start.isoformat(),
            "target_disciplines": [d.value for d in prepared.target_disciplines],
            "baseline_tiers": prepared.prompt_context.get("first_week_baseline_tiers"),
            "resolved_intensity_zones": prepared.prompt_context.get(
                "resolved_intensity_zones"
            ),
            "evidence_state": prepared.prompt_context.get("evidence_state"),
            "preferences_sent_to_model": prepared.prompt_context.get("preferences"),
            "plan": (
                result.plan.model_dump(mode="json") if result.plan is not None else None
            ),
        }
        results[key] = record

        out_path = OUTPUT_DIR / f"athlete_{key.lower()}_result.json"
        out_path.write_text(json.dumps(record, indent=2, default=str))
        print(
            f"generation_source={result.generation_source} repairs={repair_calls['n']}"
        )
        print(f"-> wrote {out_path}")

    combined_path = OUTPUT_DIR / "combined_results.json"
    combined_path.write_text(json.dumps(results, indent=2, default=str))
    print(f"\nWrote combined results to {combined_path}")

    await engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
