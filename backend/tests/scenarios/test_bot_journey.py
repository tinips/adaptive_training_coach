"""End-to-end application facade journeys without Telegram network calls."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)

from app.bot import keyboards, messages
from app.bot.rendering import TelegramResponse
from app.bot.service import CoachBotApplicationService
from app.config import Settings
from app.db.base import Base
from app.db.models import AthleteBaseline
from app.domain.enums import (
    BaselineSource,
    ConnectionStatus,
    SyncStatus,
    UserStatus,
)
from app.repositories.strava import StravaRepository
from app.repositories.users import UserRepository
from app.schemas.common import TelegramIdentity
from app.schemas.strava import StravaSyncStats
from app.services.accounts import AccountQueryService, AccountService
from app.services.onboarding import OnboardingService
from app.services.profiles import ProfileService
from app.services.strava.disconnect import DisconnectOutcome
from app.services.strava.sync import SyncOutcome
from app.workflows.onboarding_text.graph import create_onboarding_text_parser


@dataclass
class FakeStravaPort:
    calls: list[tuple[str, UUID]] = field(default_factory=list)
    sync_status: SyncStatus = SyncStatus.SUCCEEDED
    provider_revoked: bool = True

    async def issue_connect_url(self, *, user_id: UUID) -> str:
        self.calls.append(("connect", user_id))
        return "https://coach.example/integrations/strava/connect?ticket=opaque"

    async def manual_sync(self, *, user_id: UUID) -> SyncOutcome:
        self.calls.append(("sync", user_id))
        return SyncOutcome(
            job_id=uuid4(),
            status=self.sync_status,
            stats=StravaSyncStats(),
        )

    async def recalculate_baseline(self, *, user_id: UUID) -> object:
        self.calls.append(("recalculate", user_id))
        return object()

    async def disconnect(
        self,
        *,
        user_id: UUID,
        confirmed: bool,
    ) -> DisconnectOutcome:
        assert confirmed
        self.calls.append(("disconnect", user_id))
        return DisconnectOutcome(
            provider_revoked=self.provider_revoked,
            local_tokens_erased=True,
        )

    async def revoke_for_deletion(self, *, user_id: UUID) -> bool:
        self.calls.append(("revoke_for_deletion", user_id))
        return True


@pytest_asyncio.fixture
async def bot_service() -> AsyncIterator[
    tuple[
        CoachBotApplicationService,
        AccountQueryService,
        FakeStravaPort,
        AsyncEngine,
    ]
]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    settings = Settings(
        environment="test",
        database_url="sqlite+aiosqlite:///:memory:",
        llm_mode="mock",
    )
    queries = AccountQueryService(factory)
    strava = FakeStravaPort()
    service = CoachBotApplicationService(
        onboarding=OnboardingService(
            session_factory=factory,
            text_parser=create_onboarding_text_parser(settings),
            settings=settings,
        ),
        profiles=ProfileService(factory),
        account_queries=queries,
        accounts=AccountService(factory),
        strava=strava,
    )
    yield service, queries, strava, engine
    await engine.dispose()


def athlete(telegram_id: int = 5101) -> TelegramIdentity:
    return TelegramIdentity(
        telegram_user_id=telegram_id,
        telegram_username="scenario_runner",
        first_name="Sam",
        language_code="en",
    )


def button_labels(response: TelegramResponse) -> list[str]:
    keyboard = response.keyboard
    assert keyboard is not None
    return [button.text for row in keyboard.inline_keyboard for button in row]


async def complete_running_profile(
    service: CoachBotApplicationService,
    identity: TelegramIdentity,
) -> None:
    await service.start(identity)
    callbacks = [
        "ob:v1:set:CONSENT:CONTINUE",
        "ob:v1:set:PRIMARY_SPORT:RUNNING",
        "ob:v1:set:GOAL_TYPE:TEN_K",
        "ob:v1:set:EVENT_STATUS:NO",
        "ob:v1:set:GOAL_PRIORITY:FINISH_SAFELY",
    ]
    for callback in callbacks:
        await service.handle_callback(identity, callback)
        if callback == "ob:v1:set:CONSENT:CONTINUE":
            await service.handle_callback(identity, callback)
    await service.handle_text(identity, "34")
    await service.handle_callback(identity, "ob:v1:skip:HEIGHT")
    await service.handle_callback(identity, "ob:v1:skip:HEIGHT")
    await service.handle_callback(identity, "ob:v1:skip:WEIGHT")
    await service.handle_callback(
        identity,
        "ob:v1:multi:add:TRAINING_DAYS:MONDAY",
    )
    await service.handle_callback(
        identity,
        "ob:v1:multi:add:TRAINING_DAYS:MONDAY",
    )
    await service.handle_callback(
        identity,
        "ob:v1:multi:add:TRAINING_DAYS:SATURDAY",
    )
    await service.handle_callback(identity, "ob:v1:continue:TRAINING_DAYS")
    await service.handle_callback(
        identity,
        "ob:v1:set:WEEKDAY_DURATION:60",
    )
    await service.handle_callback(
        identity,
        "ob:v1:set:WEEKDAY_DURATION:60",
    )
    await service.handle_callback(
        identity,
        "ob:v1:set:WEEKEND_DURATION:120",
    )
    await service.handle_callback(
        identity,
        "ob:v1:multi:add:EQUIPMENT:RUNNING_SHOES",
    )
    await service.handle_callback(identity, "ob:v1:continue:EQUIPMENT")
    await service.handle_callback(
        identity,
        "ob:v1:multi:add:HEALTH_AREAS:NONE",
    )
    await service.handle_callback(identity, "ob:v1:continue:HEALTH_AREAS")
    await service.handle_callback(
        identity,
        "ob:v1:set:COACH_TONE:CONCISE_PRACTICAL",
    )
    await service.handle_callback(
        identity,
        "ob:v1:set:COACH_DETAIL:MEDIUM",
    )
    await service.handle_callback(
        identity,
        "ob:v1:set:BASELINE_SOURCE:SKIP_FOR_NOW",
    )


@pytest.mark.asyncio
async def test_complete_deterministic_bot_journey_persists_profile(
    bot_service: tuple[
        CoachBotApplicationService,
        AccountQueryService,
        FakeStravaPort,
        AsyncEngine,
    ],
) -> None:
    service, _, strava, _ = bot_service
    identity = athlete()

    start = await service.start(identity)
    assert messages.WELCOME_NEW in start.text
    assert "not medical advice" in start.text
    await complete_running_profile(service, identity)
    summary = await service.handle_callback(
        identity,
        "ob:v1:summary:confirm",
    )
    profile = await service.profile(identity)

    assert messages.ONBOARDING_COMPLETE in summary.text
    assert "Your saved athlete profile" in profile.text
    assert "Running" in profile.text
    assert "Monday, Saturday" in profile.text
    assert strava.calls == []


@pytest.mark.asyncio
async def test_explicit_other_renders_confirmation_before_advancing(
    bot_service: tuple[
        CoachBotApplicationService,
        AccountQueryService,
        FakeStravaPort,
        AsyncEngine,
    ],
) -> None:
    service, _, _, _ = bot_service
    identity = athlete(5102)
    await service.start(identity)
    await service.handle_callback(identity, "ob:v1:set:CONSENT:CONTINUE")
    request = await service.handle_callback(
        identity,
        "ob:v1:other:PRIMARY_SPORT",
    )
    interpreted = await service.handle_text(identity, "córrer")

    assert messages.FREE_TEXT_REQUEST in request.text
    assert "I interpreted your answer as" in interpreted.text
    assert interpreted.keyboard is not None

    confirmed = await service.handle_callback(
        identity,
        "ob:v1:parsed:confirm",
    )
    assert "main training goal" in confirmed.text


@pytest.mark.asyncio
async def test_cancel_and_delete_both_require_second_confirmation(
    bot_service: tuple[
        CoachBotApplicationService,
        AccountQueryService,
        FakeStravaPort,
        AsyncEngine,
    ],
) -> None:
    service, queries, strava, _ = bot_service
    identity = athlete(5103)
    await service.start(identity)

    cancel_prompt = await service.cancel(identity)
    still_present = await queries.resolve_user_id(identity)
    assert cancel_prompt.text == messages.CANCEL_CONFIRM
    assert still_present is not None

    delete_prompt = await service.delete_me(identity)
    still_present = await queries.resolve_user_id(identity)
    assert delete_prompt.text == messages.DELETE_CONFIRM
    assert still_present is not None

    deleted = await service.handle_callback(
        identity,
        "acct:v1:delete:confirm",
    )
    assert deleted.text == messages.DELETED
    assert await queries.resolve_user_id(identity) is None
    assert ("revoke_for_deletion", still_present) in strava.calls


@pytest.mark.asyncio
async def test_strava_connect_button_uses_opaque_application_ticket(
    bot_service: tuple[
        CoachBotApplicationService,
        AccountQueryService,
        FakeStravaPort,
        AsyncEngine,
    ],
) -> None:
    service, _, _, _ = bot_service
    identity = athlete(5104)
    await complete_running_profile(service, identity)
    await service.handle_callback(identity, "ob:v1:summary:confirm")

    response = await service.strava(identity)

    assert response.keyboard is not None
    button = response.keyboard.inline_keyboard[0][0]
    assert button.url is not None
    assert "ticket=opaque" in button.url
    assert str(identity.telegram_user_id) not in button.url


@pytest.mark.asyncio
async def test_strava_is_not_available_before_profile_confirmation(
    bot_service: tuple[
        CoachBotApplicationService,
        AccountQueryService,
        FakeStravaPort,
        AsyncEngine,
    ],
) -> None:
    service, _, strava, _ = bot_service
    identity = athlete(5105)
    await service.start(identity)

    response = await service.strava(identity)

    assert response.text == messages.PROFILE_INCOMPLETE
    assert strava.calls == []


@pytest.mark.asyncio
async def test_post_profile_manual_and_calibration_choices_are_persisted(
    bot_service: tuple[
        CoachBotApplicationService,
        AccountQueryService,
        FakeStravaPort,
        AsyncEngine,
    ],
) -> None:
    service, queries, _, engine = bot_service
    identity = athlete(5106)
    await complete_running_profile(service, identity)
    await service.handle_callback(identity, "ob:v1:summary:confirm")

    manual = await service.handle_callback(identity, "menu:v1:manual")
    await service.handle_callback(identity, "menu:v1:manual")
    profile = await queries.profile(identity)
    lifecycle = await queries.lifecycle(identity)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        baseline_count = await session.scalar(
            select(func.count()).select_from(AthleteBaseline)
        )

    assert manual.text == messages.BASELINE_MANUAL_PENDING
    assert profile is not None
    assert profile["baseline_source"] is BaselineSource.MANUAL
    assert lifecycle is not None
    assert lifecycle["status"] is UserStatus.BASELINE_PENDING
    assert baseline_count == 0

    calibration = await service.handle_callback(
        identity,
        "menu:v1:calibration",
    )
    updated = await queries.profile(identity)

    assert calibration.text == messages.BASELINE_CALIBRATION_PENDING
    assert updated is not None
    assert updated["baseline_source"] is BaselineSource.CALIBRATION
    assert await queries.baseline(identity) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "connection_status",
    [
        None,
        ConnectionStatus.DISCONNECTED,
        ConnectionStatus.REFRESH_FAILED,
        ConnectionStatus.INSUFFICIENT_SCOPE,
    ],
)
async def test_ready_home_never_offers_sync_without_healthy_connection(
    bot_service: tuple[
        CoachBotApplicationService,
        AccountQueryService,
        FakeStravaPort,
        AsyncEngine,
    ],
    connection_status: ConnectionStatus | None,
) -> None:
    service, queries, _, engine = bot_service
    identity = athlete(5200)
    await complete_running_profile(service, identity)
    await service.handle_callback(identity, "ob:v1:summary:confirm")
    lifecycle = await queries.lifecycle(identity)
    assert lifecycle is not None
    user_id = lifecycle["user_id"]
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with factory.begin() as session:
        user = await UserRepository(session).require_by_id(user_id)
        user.status = UserStatus.BASELINE_READY
        if connection_status is not None:
            await StravaRepository(session).upsert_connection(
                user_id=user_id,
                strava_athlete_id=7000
                + list(ConnectionStatus).index(connection_status),
                accepted_scopes=["activity:read"],
                encrypted_access_token="encrypted-access",
                encrypted_refresh_token="encrypted-refresh",
                access_token_expires_at=datetime(2027, 1, 1, tzinfo=UTC),
                connection_status=connection_status,
            )

    response = await service.start(identity)
    labels = button_labels(response)

    assert labels == [
        keyboards.LABELS["reconnect_strava"],
        keyboards.LABELS["view_baseline"],
        keyboards.LABELS["view_profile"],
        keyboards.LABELS["help"],
    ]
    assert keyboards.LABELS["sync_now"] not in labels
    assert keyboards.LABELS["recalculate"] not in labels


@pytest.mark.asyncio
async def test_unhealthy_strava_settings_allow_reconnect_and_disconnect(
    bot_service: tuple[
        CoachBotApplicationService,
        AccountQueryService,
        FakeStravaPort,
        AsyncEngine,
    ],
) -> None:
    service, queries, _, engine = bot_service
    identity = athlete(5250)
    await complete_running_profile(service, identity)
    await service.handle_callback(identity, "ob:v1:summary:confirm")
    lifecycle = await queries.lifecycle(identity)
    assert lifecycle is not None
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with factory.begin() as session:
        await StravaRepository(session).upsert_connection(
            user_id=lifecycle["user_id"],
            strava_athlete_id=7250,
            accepted_scopes=["activity:read"],
            encrypted_access_token="encrypted-access",
            encrypted_refresh_token="encrypted-refresh",
            access_token_expires_at=datetime(2027, 1, 1, tzinfo=UTC),
            connection_status=ConnectionStatus.REFRESH_FAILED,
        )

    response = await service.strava(identity)
    labels = button_labels(response)

    assert messages.STRAVA_CONNECTION_UNHEALTHY in response.text
    assert keyboards.LABELS["reconnect_strava"] in labels
    assert keyboards.LABELS["disconnect_strava"] in labels
    assert keyboards.LABELS["sync_now"] not in labels


@pytest.mark.asyncio
async def test_disconnect_copy_is_honest_when_provider_revocation_is_unconfirmed(
    bot_service: tuple[
        CoachBotApplicationService,
        AccountQueryService,
        FakeStravaPort,
        AsyncEngine,
    ],
) -> None:
    service, _, strava, _ = bot_service
    identity = athlete(5260)
    await complete_running_profile(service, identity)
    await service.handle_callback(identity, "ob:v1:summary:confirm")
    strava.provider_revoked = False

    response = await service.handle_callback(
        identity,
        "st:v1:disconnect:confirm",
    )

    assert response.text == messages.STRAVA_DISCONNECTED_LOCAL_ONLY


@pytest.mark.asyncio
@pytest.mark.parametrize("status", list(SyncStatus))
async def test_manual_sync_renders_each_outcome_honestly(
    bot_service: tuple[
        CoachBotApplicationService,
        AccountQueryService,
        FakeStravaPort,
        AsyncEngine,
    ],
    status: SyncStatus,
) -> None:
    service, _, strava, _ = bot_service
    identity = athlete(5300)
    await complete_running_profile(service, identity)
    await service.handle_callback(identity, "ob:v1:summary:confirm")
    strava.sync_status = status

    response = await service.handle_callback(identity, "st:v1:sync")

    assert response.text == messages.strava_sync_outcome(status)


@pytest.mark.asyncio
async def test_stale_manual_menu_action_cannot_downgrade_ready_lifecycle(
    bot_service: tuple[
        CoachBotApplicationService,
        AccountQueryService,
        FakeStravaPort,
        AsyncEngine,
    ],
) -> None:
    service, queries, _, engine = bot_service
    identity = athlete(5400)
    await complete_running_profile(service, identity)
    await service.handle_callback(identity, "ob:v1:summary:confirm")
    lifecycle = await queries.lifecycle(identity)
    assert lifecycle is not None
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory.begin() as session:
        user = await UserRepository(session).require_by_id(lifecycle["user_id"])
        user.status = UserStatus.BASELINE_READY

    response = await service.handle_callback(identity, "menu:v1:manual")
    unchanged_profile = await queries.profile(identity)
    unchanged_lifecycle = await queries.lifecycle(identity)

    assert response.text == messages.BASELINE_SELECTION_UNAVAILABLE
    assert unchanged_profile is not None
    assert unchanged_profile["baseline_source"] is BaselineSource.SKIP_FOR_NOW
    assert unchanged_lifecycle is not None
    assert unchanged_lifecycle["status"] is UserStatus.BASELINE_READY
