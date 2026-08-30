"""Production Telegram long-polling process and dependency composition."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, Defaults

from app.bot.handlers import (
    ALLOWED_USER_IDS_KEY,
    BOT_SERVICE_KEY,
    DEV_USER_IDS_KEY,
    WORKOUT_SCREENSHOT_SERVICE_KEY,
)
from app.bot.router import register_handlers
from app.bot.service import CoachBotApplicationService
from app.bot.service_protocol import CoachBotService
from app.config import Settings, get_settings
from app.db.session import create_engine, create_session_factory
from app.integrations.llm.factory import create_goal_extraction_model
from app.integrations.llm.vision import DeepSeekWorkoutScreenshotExtractor
from app.logging import configure_logging
from app.services.accounts import AccountQueryService, AccountService
from app.services.mobile_sync import MobileSyncService
from app.services.onboarding import OnboardingService
from app.services.profiles import ProfileService
from app.services.training_import import TrainingFileImportService
from app.services.weekly_planning import WeeklyPlanningService
from app.services.workout_screenshot import WorkoutScreenshotService

TelegramApplication = Application[Any, Any, Any, Any, Any, Any]
logger = logging.getLogger(__name__)


@dataclass(slots=True)
class BotRuntime:
    """Process-owned resources shared by every Telegram update."""

    settings: Settings
    engine: AsyncEngine
    apple_health: TrainingFileImportService
    service: CoachBotApplicationService
    workout_screenshot: WorkoutScreenshotService

    async def recover(self) -> None:
        """Reconcile durable background work before accepting updates."""

        await self.apple_health.recover_stale_work()

    async def aclose(self) -> None:
        """Close provider and database connection pools exactly once."""

        await self.engine.dispose()


def build_runtime(
    settings: Settings | None = None,
    *,
    engine: AsyncEngine | None = None,
) -> BotRuntime:
    """Compile the graph once and compose the production application facade."""

    runtime_settings = settings or get_settings()
    runtime_engine = engine or create_engine(runtime_settings)
    session_factory = create_session_factory(runtime_engine)
    apple_health = TrainingFileImportService(
        session_factory=session_factory,
        settings=runtime_settings,
    )
    mobile_sync = MobileSyncService(
        session_factory=session_factory,
        settings=runtime_settings,
    )
    service = CoachBotApplicationService(
        onboarding=OnboardingService(
            session_factory=session_factory,
            settings=runtime_settings,
        ),
        profiles=ProfileService(session_factory),
        account_queries=AccountQueryService(session_factory),
        accounts=AccountService(session_factory),
        apple_health=apple_health,
        apple_health_enabled=runtime_settings.apple_health_import_enabled,
        tcx_enabled=runtime_settings.tcx_import_enabled,
        mobile_sync=mobile_sync,
        mobile_sync_enabled=runtime_settings.mobile_sync_enabled,
        planning=WeeklyPlanningService(
            session_factory=session_factory,
            settings=runtime_settings,
            model=create_goal_extraction_model(runtime_settings),
        ),
    )
    workout_screenshot = WorkoutScreenshotService(
        session_factory=session_factory,
        settings=runtime_settings,
        extractor=DeepSeekWorkoutScreenshotExtractor(
            api_key=runtime_settings.llm_api_key,
            base_url=runtime_settings.llm_base_url or None,
            model_name=runtime_settings.llm_vision_model,
        ),
    )
    return BotRuntime(
        settings=runtime_settings,
        engine=runtime_engine,
        apple_health=apple_health,
        service=service,
        workout_screenshot=workout_screenshot,
    )


def create_application(
    settings: Settings | None = None,
    *,
    runtime: BotRuntime | None = None,
    service: CoachBotService | None = None,
    workout_screenshot: WorkoutScreenshotService | None = None,
) -> TelegramApplication:
    """Build a network-idle Telegram application suitable for tests or polling."""

    runtime_settings = settings or (runtime.settings if runtime else get_settings())
    token = runtime_settings.telegram_bot_token
    if token is None or not token.get_secret_value():
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required to start the bot")

    owned_runtime = runtime
    if service is None:
        owned_runtime = runtime or build_runtime(runtime_settings)
        service = owned_runtime.service
    if workout_screenshot is None:
        if owned_runtime is not None:
            workout_screenshot = owned_runtime.workout_screenshot
        else:
            # Construction never calls out to DeepSeek - safe to build lazily
            # here for a test/injection path that supplied its own `service`.
            workout_screenshot = WorkoutScreenshotService(
                session_factory=create_session_factory(
                    create_engine(runtime_settings)
                ),
                settings=runtime_settings,
                extractor=DeepSeekWorkoutScreenshotExtractor(
                    api_key=runtime_settings.llm_api_key,
                    base_url=runtime_settings.llm_base_url or None,
                    model_name=runtime_settings.llm_vision_model,
                ),
            )

    builder = (
        Application.builder()
        .token(token.get_secret_value())
        .defaults(Defaults(parse_mode=ParseMode.HTML))
    )
    if owned_runtime is not None:

        async def post_init(_: TelegramApplication) -> None:
            await owned_runtime.recover()

        async def post_shutdown(_: TelegramApplication) -> None:
            await owned_runtime.aclose()

        builder = builder.post_init(post_init).post_shutdown(post_shutdown)

    application = builder.build()
    application.bot_data[BOT_SERVICE_KEY] = service
    application.bot_data[WORKOUT_SCREENSHOT_SERVICE_KEY] = workout_screenshot
    application.bot_data[ALLOWED_USER_IDS_KEY] = frozenset(
        runtime_settings.telegram_allowed_user_ids
    )
    application.bot_data[DEV_USER_IDS_KEY] = frozenset(
        runtime_settings.dev_telegram_user_ids
    )
    register_handlers(application, runtime_settings)
    return application


def main() -> None:
    """Run the local production bot with Telegram long polling."""

    settings = get_settings()
    configure_logging(settings.log_level)
    if settings.llm_mode == "live":
        logger.info(
            "goal_llm_mode=live model=%s",
            settings.llm_model,
        )
    else:
        logger.info("goal_llm_mode=mock")
    application = create_application(settings)
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
