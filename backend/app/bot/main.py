"""Production Telegram long-polling process and dependency composition."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, Defaults

from app.bot.handlers import BOT_SERVICE_KEY
from app.bot.notifier import TelegramInitialSyncNotifier
from app.bot.router import register_handlers
from app.bot.service import CoachBotApplicationService
from app.bot.service_protocol import CoachBotService
from app.config import Settings, get_settings
from app.db.session import create_engine, create_session_factory
from app.logging import configure_logging
from app.services.accounts import AccountQueryService, AccountService
from app.services.onboarding import OnboardingService
from app.services.profiles import ProfileService
from app.services.strava.orchestrator import StravaCoordinator
from app.services.training_import import TrainingFileImportService
from app.services.workout_feedback import WorkoutFeedbackService
from app.workflows.onboarding_goal.graph import create_goal_extractor

TelegramApplication = Application[Any, Any, Any, Any, Any, Any]
logger = logging.getLogger(__name__)


@dataclass(slots=True)
class BotRuntime:
    """Process-owned resources shared by every Telegram update."""

    settings: Settings
    engine: AsyncEngine
    strava: StravaCoordinator
    apple_health: TrainingFileImportService
    service: CoachBotApplicationService

    async def recover(self) -> None:
        """Reconcile durable background work before accepting updates."""

        await self.strava.recover_stale_work()
        await self.apple_health.recover_stale_work()

    async def aclose(self) -> None:
        """Close provider and database connection pools exactly once."""

        try:
            await self.strava.aclose()
        finally:
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
    goal_extractor = create_goal_extractor(runtime_settings)
    telegram_token = runtime_settings.telegram_bot_token
    initial_sync_notifier = (
        TelegramInitialSyncNotifier(
            session_factory=session_factory,
            bot_token=telegram_token,
        )
        if telegram_token is not None and telegram_token.get_secret_value()
        else None
    )
    strava = StravaCoordinator(
        session_factory=session_factory,
        settings=runtime_settings,
        initial_sync_notifier=initial_sync_notifier,
    )
    apple_health = TrainingFileImportService(
        session_factory=session_factory,
        settings=runtime_settings,
    )
    service = CoachBotApplicationService(
        onboarding=OnboardingService(
            session_factory=session_factory,
            goal_extractor=goal_extractor,
            settings=runtime_settings,
        ),
        profiles=ProfileService(session_factory),
        account_queries=AccountQueryService(session_factory),
        accounts=AccountService(session_factory),
        strava=strava,
        apple_health=apple_health,
        workout_feedback=WorkoutFeedbackService(session_factory),
        strava_enabled=runtime_settings.strava_enabled,
        apple_health_enabled=runtime_settings.apple_health_import_enabled,
        tcx_enabled=runtime_settings.tcx_import_enabled,
        workout_feedback_enabled=runtime_settings.workout_feedback_enabled,
    )
    return BotRuntime(
        settings=runtime_settings,
        engine=runtime_engine,
        strava=strava,
        apple_health=apple_health,
        service=service,
    )


def create_application(
    settings: Settings | None = None,
    *,
    runtime: BotRuntime | None = None,
    service: CoachBotService | None = None,
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
    register_handlers(application)
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
