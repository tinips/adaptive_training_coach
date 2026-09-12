"""Run three synthetic onboarding journeys and live first-week generations.

This is a verification harness, not an automated test.  It creates an isolated
temporary SQLite database, walks each synthetic athlete through the real bot
onboarding callbacks and baseline Web App submission, then calls the configured
live first-week LLM.  It never connects to the configured production database
and deletes the temporary database when the process exits.

Run from ``backend/``:

    python -m scripts.simulate_onboarding_first_week
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.bot.service import CoachBotApplicationService
from app.config import Settings, get_settings
from app.db.base import Base
from app.integrations.llm.factory import create_goal_extraction_model
from app.schemas.common import TelegramIdentity
from app.schemas.weekly_plans import PlanSession
from app.services.accounts import AccountQueryService, AccountService
from app.services.onboarding import OnboardingApplicationError, OnboardingService
from app.services.profiles import ProfileService
from app.services.weekly_planning import FirstWeekPlanner
from tests.catalog_seed import seed_training_catalog

ATHLETES: tuple[dict[str, object], ...] = (
    {
        "name": "Marta",
        "telegram_user_id": 960_001,
        "goal_template": "TRIATHLON_SPRINT",
        "supporting_goal": "STRENGTH_MAINTENANCE",
        "birth_year": "1989",
        "gender": "FEMALE",
        "weight_kg": "61",
        "height_cm": "168",
        "availability": (
            "Monday: 50 minutes running and 35 minutes strength. Tuesday: 60 "
            "minutes cycling. Wednesday: 45 minutes swimming. Thursday: 50 "
            "minutes running and 35 minutes strength. Friday: 60 minutes cycling. "
            "Saturday: 45 minutes swimming."
        ),
        "baseline": {
            "preferences.coaching_style": "NORMAL",
            "running.typical_weekly_sessions": "3",
            "running.typical_weekly_duration_minutes": "165",
            "running.longest_recent_run_minutes": "70",
            "running.recent_race_result": "10 km, 50:00",
            "running.recent_race_effort": "MAXIMAL",
            "cycling.typical_weekly_sessions": "2",
            "cycling.typical_weekly_duration_minutes": "150",
            "cycling.longest_recent_ride_minutes": "75",
            "cycling.riding_environment": "BOTH",
            "cycling.riding_confidence": "CONFIDENT",
            "cycling.recent_ftp_watts": "210",
            "swimming.typical_weekly_sessions": "2",
            "swimming.typical_weekly_duration_minutes": "90",
            "swimming.longest_continuous_swim_meters": "1000",
            "swimming.swimming_environment": "POOL",
            "swimming.pool_length_meters": "25",
            "swimming.recent_400m_seconds": "09:00",
            "triathlon.prior_experience": "SPRINT",
            "triathlon.weakest_discipline": "SWIMMING",
            "triathlon.open_water_confidence": "SOME_EXPERIENCE",
            "preferences.desired_weekly_sessions.RUNNING": "2",
            "preferences.desired_weekly_sessions.CYCLING": "2",
            "preferences.desired_weekly_sessions.SWIMMING": "2",
            "preferences.desired_weekly_sessions.STRENGTH": "2",
        },
    },
    {
        "name": "Nora",
        "telegram_user_id": 960_002,
        "goal_template": "TRIATHLON_SPRINT",
        "supporting_goal": "none",
        "birth_year": "1978",
        "gender": "FEMALE",
        "weight_kg": "74",
        "height_cm": "165",
        "availability": (
            "Tuesday: 30 minutes running. Wednesday: 30 minutes cycling. Thursday: "
            "30 minutes swimming. Friday: 30 minutes running. Saturday: 30 minutes "
            "cycling. Sunday: 30 minutes swimming."
        ),
        "baseline": {
            "preferences.coaching_style": "CONSERVATIVE",
            "running.typical_weekly_sessions": "0",
            "running.typical_weekly_duration_minutes": "0",
            "running.longest_recent_run_minutes": "0",
            "running.recent_race_result": "",
            "running.recent_race_effort": "",
            "cycling.typical_weekly_sessions": "0",
            "cycling.typical_weekly_duration_minutes": "0",
            "cycling.longest_recent_ride_minutes": "0",
            "cycling.riding_environment": "INDOOR",
            "cycling.riding_confidence": "NEW_RIDER",
            "cycling.recent_ftp_watts": "",
            "swimming.typical_weekly_sessions": "0",
            "swimming.typical_weekly_duration_minutes": "0",
            "swimming.longest_continuous_swim_meters": "0",
            "swimming.swimming_environment": "POOL",
            "swimming.pool_length_meters": "25",
            "swimming.recent_400m_seconds": "",
            "triathlon.prior_experience": "NONE",
            "triathlon.weakest_discipline": "SWIMMING",
            "triathlon.open_water_confidence": "NOT_CONFIDENT",
            "preferences.desired_weekly_sessions.RUNNING": "2",
            "preferences.desired_weekly_sessions.CYCLING": "2",
            "preferences.desired_weekly_sessions.SWIMMING": "2",
        },
    },
    {
        "name": "Leo",
        "telegram_user_id": 960_003,
        "goal_template": "TRIATHLON_OLYMPIC",
        "supporting_goal": "none",
        "birth_year": "1995",
        "gender": "MALE",
        "weight_kg": "80",
        "height_cm": "182",
        "availability": (
            "Monday: 50 minutes running. Tuesday: 60 minutes cycling. Wednesday: "
            "45 minutes swimming. Thursday: 50 minutes running. Friday: 60 minutes "
            "cycling. Saturday: 45 minutes swimming."
        ),
        "baseline": {
            "preferences.coaching_style": "DEMANDING",
            "running.typical_weekly_sessions": "2",
            "running.typical_weekly_duration_minutes": "90",
            "running.longest_recent_run_minutes": "45",
            "running.recent_race_result": "5 km, 35:00",
            "running.recent_race_effort": "EASY",
            "cycling.typical_weekly_sessions": "2",
            "cycling.typical_weekly_duration_minutes": "120",
            "cycling.longest_recent_ride_minutes": "60",
            "cycling.riding_environment": "BOTH",
            "cycling.riding_confidence": "SIMPLE_ROUTES",
            "cycling.recent_ftp_watts": "180",
            "swimming.typical_weekly_sessions": "2",
            "swimming.typical_weekly_duration_minutes": "80",
            "swimming.longest_continuous_swim_meters": "800",
            "swimming.swimming_environment": "POOL",
            "swimming.pool_length_meters": "25",
            "swimming.recent_400m_seconds": "10:00",
            "triathlon.prior_experience": "OLYMPIC",
            "triathlon.weakest_discipline": "NO_CLEAR_WEAKNESS",
            "triathlon.open_water_confidence": "CONFIDENT",
            "preferences.desired_weekly_sessions.RUNNING": "2",
            "preferences.desired_weekly_sessions.CYCLING": "2",
            "preferences.desired_weekly_sessions.SWIMMING": "2",
        },
    },
)


def _identity(spec: dict[str, object]) -> TelegramIdentity:
    name = str(spec["name"])
    return TelegramIdentity(
        telegram_user_id=int(spec["telegram_user_id"]),
        telegram_username=f"simulation_{name.casefold()}",
        first_name=name,
        language_code="en",
    )


def _first_callback(response: object, label: str) -> str:
    keyboard = getattr(response, "keyboard", None)
    rows = getattr(keyboard, "inline_keyboard", ()) if keyboard is not None else ()
    for row in rows:
        for button in row:
            if label in (getattr(button, "text", None) or "") and button.callback_data:
                return button.callback_data
    raise RuntimeError(f"missing onboarding button: {label}")


async def _onboard(
    bot: CoachBotApplicationService, spec: dict[str, object]
) -> TelegramIdentity:
    """Exercise the same profile, goal, availability, equipment, and baseline path."""

    identity = _identity(spec)
    await bot.start(identity)
    await bot.handle_callback(identity, "nav:v1:consent")
    await bot.handle_callback(identity, "ob:v1:profile")
    await bot.handle_text(identity, str(spec["birth_year"]))
    await bot.handle_callback(identity, f"ob:v1:profile:gender:{spec['gender']}")
    await bot.handle_text(identity, str(spec["weight_kg"]))
    await bot.handle_text(identity, str(spec["height_cm"]))
    await bot.handle_text(identity, "Europe/Madrid")
    await bot.handle_callback(identity, "ob:v1:goal:sport:TRIATHLON")
    await bot.handle_callback(identity, f"ob:v1:goal:template:{spec['goal_template']}")
    await bot.handle_callback(identity, "ob:v1:goal:metric:skip")
    await bot.handle_callback(identity, "ob:v1:goal:nodate")
    await bot.handle_callback(identity, f"ob:v1:support:{spec['supporting_goal']}")
    availability_response = await bot.handle_text(identity, str(spec["availability"]))
    try:
        equipment = await bot.handle_callback(identity, "ob:v1:availability:confirm")
    except OnboardingApplicationError as error:
        raise RuntimeError(
            f"{spec['name']}: availability was not ready to confirm: "
            f"{availability_response.text}"
        ) from error
    await bot.handle_callback(identity, _first_callback(equipment, "Running shoes"))
    await bot.handle_callback(identity, "ob:v1:equipment:done")
    await bot.handle_callback(identity, "ob:v1:health:none")
    await bot.submit_baseline_web_app(
        identity, json.dumps(spec["baseline"], separators=(",", ":"))
    )
    return identity


def _session_payload(session: PlanSession) -> dict[str, object]:
    intensity = session.intensity
    targets = session.targets
    return {
        "discipline": session.discipline.value,
        "purpose": session.purpose,
        "objective": session.objective,
        "intensity": {
            "metric": intensity.metric,
            "target_range": list(intensity.target_range),
            "rpe_range": list(intensity.rpe_range),
            "guidance": intensity.guidance,
        },
        "targets": targets.model_dump(mode="json", exclude_none=True),
        "execution": session.execution,
    }


async def _run(settings: Settings) -> list[dict[str, object]]:
    with tempfile.TemporaryDirectory(prefix="adaptive-coach-simulation-") as directory:
        database_path = Path(directory) / "simulation.sqlite"
        simulation_settings = settings.model_copy(
            update={
                "environment": "test",
                "database_url": f"sqlite+aiosqlite:///{database_path.as_posix()}",
                "telegram_bot_username": None,
            }
        )
        engine = create_async_engine(simulation_settings.database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with factory.begin() as session:
                await seed_training_catalog(session)

            onboarding = OnboardingService(
                session_factory=factory,
                settings=simulation_settings,
                model=create_goal_extraction_model(simulation_settings),
            )
            planner = FirstWeekPlanner(
                session_factory=factory,
                settings=simulation_settings,
                model=create_goal_extraction_model(
                    simulation_settings,
                    model_name=(
                        simulation_settings.first_week_llm_model
                        or simulation_settings.llm_model
                    ),
                ),
            )
            bot = CoachBotApplicationService(
                onboarding=onboarding,
                profiles=ProfileService(factory),
                account_queries=AccountQueryService(factory),
                accounts=AccountService(factory),
                planning=planner,
            )

            results: list[dict[str, object]] = []
            for spec in ATHLETES:
                identity = await _onboard(bot, spec)
                result = await planner.generate_next_week(identity)
                if result.kind != "created" or result.plan is None:
                    raise RuntimeError(
                        f"{spec['name']}: expected a created plan, got {result.kind}"
                    )
                results.append(
                    {
                        "athlete": spec["name"],
                        "baseline": spec["baseline"],
                        "generation_source": result.generation_source,
                        "week_start": result.plan.week_start.isoformat(),
                        "sessions": [
                            _session_payload(session)
                            for session in result.plan.sessions
                        ],
                        "guardrails": list(result.plan.guardrails),
                        "logging_instructions": list(result.plan.logging_instructions),
                    }
                )
            return results
        finally:
            await engine.dispose()


def main() -> int:
    settings = get_settings()
    if settings.llm_mode != "live":
        print(
            "ABORT: LLM_MODE must be live; refusing mock plan output.", file=sys.stderr
        )
        return 1
    if settings.llm_api_key is None or not settings.llm_api_key.get_secret_value():
        print("ABORT: LLM_API_KEY is not configured.", file=sys.stderr)
        return 1
    report: dict[str, Any] = {
        "provider_mode": settings.llm_mode,
        "model": settings.first_week_llm_model or settings.llm_model,
        "onboarding_model": settings.llm_model,
        "database": "temporary isolated SQLite database",
        "athletes": asyncio.run(_run(settings)),
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
