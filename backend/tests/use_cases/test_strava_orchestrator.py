"""End-to-end application-service composition with real repositories."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import Settings
from app.db.base import Base
from app.db.models import (
    Activity,
    AthleteBaseline,
    BaselinePreference,
    StravaConnection,
    StravaSyncJob,
    User,
)
from app.domain.enums import (
    BaselinePreferenceStatus,
    BaselineSource,
    BaselineStatus,
    ConnectionStatus,
    SyncStatus,
    SyncType,
    UserStatus,
    WebhookAspectType,
    WebhookObjectType,
    WebhookProcessingStatus,
)
from app.integrations.strava.client import StravaClient
from app.repositories.baselines import BaselineRepository
from app.repositories.profiles import ProfileRepository
from app.repositories.strava import StravaRepository
from app.repositories.users import UserRepository
from app.schemas.strava import StravaWebhookEvent
from app.security.encryption import TokenCipher
from app.services.strava.exceptions import (
    OAuthAuthorizationDeniedError,
    OAuthStateRejectedError,
    StravaNotConnectedError,
)
from app.services.strava.orchestrator import (
    ConnectTicketRejectedError,
    StravaCoordinator,
)

NOW = datetime(2026, 7, 28, 12, tzinfo=UTC)


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def enable_foreign_keys(dbapi_connection: object, _: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(
        engine,
        expire_on_commit=False,
        autoflush=False,
    )
    yield factory
    await engine.dispose()


def activity_payload() -> dict[str, object]:
    return {
        "id": 8080,
        "name": "Imported run",
        "sport_type": "Run",
        "start_date": "2026-07-27T07:00:00Z",
        "timezone": "Europe/Madrid",
        "elapsed_time": 3600,
        "moving_time": 3500,
        "distance": 10_000.0,
        "total_elevation_gain": 80.0,
        "average_heartrate": None,
        "max_heartrate": None,
        "average_speed": 2.86,
        "average_watts": None,
        "trainer": False,
        "commute": False,
        "manual": False,
    }


@pytest.mark.asyncio
async def test_opaque_ticket_oauth_sync_baseline_and_disconnect(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    requests: list[httpx.Request] = []
    job_visible_during_provider_call: list[bool] = []
    rotated_token_visible_during_provider_call: list[bool] = []
    importing_visible_during_provider_call: list[bool] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/oauth/token":
            form = parse_qs(request.content.decode())
            if form.get("grant_type") == ["refresh_token"]:
                return httpx.Response(
                    200,
                    json={
                        "access_token": "rotated-access",
                        "refresh_token": "rotated-refresh",
                        "expires_at": int((NOW + timedelta(hours=6)).timestamp()),
                    },
                )
            return httpx.Response(
                200,
                json={
                    "access_token": "provider-access",
                    "refresh_token": "provider-refresh",
                    "expires_at": int((NOW + timedelta(minutes=1)).timestamp()),
                    "athlete": {"id": 4242},
                    "scope": "activity:read",
                },
            )
        if request.url.path == "/api/v3/athlete/activities":
            async with session_factory() as visibility_session:
                visible_repository = StravaRepository(visibility_session)
                active_job = await visible_repository.get_active_sync_job(
                    user_id=user_id
                )
                visible_connection = await visible_repository.get_connection(
                    user_id=user_id
                )
                visible_user = await visibility_session.get(User, user_id)
                visible_preference = await visibility_session.scalar(
                    select(BaselinePreference).where(
                        BaselinePreference.user_id == user_id
                    )
                )
                job_visible_during_provider_call.append(active_job is not None)
                rotated_token_visible_during_provider_call.append(
                    visible_connection is not None
                    and visible_connection.encrypted_access_token is not None
                    and cipher.decrypt(visible_connection.encrypted_access_token)
                    == "rotated-access"
                )
                importing_visible_during_provider_call.append(
                    visible_user is not None
                    and visible_user.status == UserStatus.BASELINE_IMPORTING
                    and visible_preference is not None
                    and visible_preference.status == BaselinePreferenceStatus.IMPORTING
                )
            page = int(request.url.params["page"])
            return httpx.Response(
                200,
                json=[activity_payload()] if page == 1 else [],
                headers={
                    "X-RateLimit-Limit": "200,2000",
                    "X-RateLimit-Usage": "1,10",
                    "X-ReadRateLimit-Limit": "100,1000",
                    "X-ReadRateLimit-Usage": "1,10",
                },
            )
        if request.url.path == "/oauth/revoke":
            return httpx.Response(200, json={})
        raise AssertionError(f"Unexpected provider path {request.url.path}")

    http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://www.strava.com",
    )
    client = StravaClient(
        client_id="client-id",
        client_secret="client-secret",
        redirect_uri="https://coach.example/integrations/strava/callback",
        http_client=http,
    )
    cipher = TokenCipher(TokenCipher.generate_key())
    settings = Settings(
        environment="test",
        database_url="sqlite+aiosqlite:///:memory:",
        public_base_url="https://coach.example",
        strava_client_id="client-id",
        strava_client_secret="client-secret",
        strava_redirect_uri="https://coach.example/integrations/strava/callback",
        strava_webhook_verify_token="verify-token",
        strava_initial_sync_days=56,
        strava_sync_page_size=100,
    )
    async with session_factory.begin() as session:
        user, _ = await UserRepository(session).get_or_create(
            telegram_user_id=112233,
            telegram_username="athlete",
            first_name="Athlete",
        )
        user_id = user.id
        await ProfileRepository(session).upsert_baseline_preference(
            user_id=user_id,
            selected_source=BaselineSource.STRAVA,
            status=BaselinePreferenceStatus.PENDING,
        )

    coordinator = StravaCoordinator(
        session_factory=session_factory,
        settings=settings,
        client=client,
        cipher=cipher,
        clock=lambda: NOW,
    )
    app_connect_url = await coordinator.issue_connect_url(user_id=user_id)
    parsed_connect = urlparse(app_connect_url)
    raw_ticket = parse_qs(parsed_connect.query)["ticket"][0]

    assert parsed_connect.path == "/integrations/strava/connect"
    assert str(user_id) not in app_connect_url

    initiation = await coordinator.begin_oauth(raw_ticket=raw_ticket)
    with pytest.raises(ConnectTicketRejectedError):
        await coordinator.begin_oauth(raw_ticket=raw_ticket)
    provider_query = parse_qs(urlparse(initiation.authorization_url).query)
    provider_state = provider_query["state"][0]
    assert provider_state != raw_ticket

    completion = await coordinator.complete_oauth(
        raw_state=provider_state,
        code="authorization-code",
        accepted_scope="read,activity:read_all",
    )
    assert completion.user_id == user_id

    async with session_factory() as session:
        pending_user = await session.get(User, user_id)
        pending_preference = await session.scalar(
            select(BaselinePreference).where(BaselinePreference.user_id == user_id)
        )
        assert pending_user is not None
        assert pending_user.status == UserStatus.BASELINE_PENDING
        assert pending_preference is not None
        assert pending_preference.status == BaselinePreferenceStatus.PENDING

    outcome = await coordinator.initial_sync(user_id=user_id)
    assert outcome.stats.imported_count == 1

    async with session_factory() as session:
        ready_user = await session.get(User, user_id)
        preference = await session.scalar(
            select(BaselinePreference).where(BaselinePreference.user_id == user_id)
        )
        connection = await session.scalar(
            select(StravaConnection).where(StravaConnection.user_id == user_id)
        )
        imported = await session.scalar(
            select(Activity).where(Activity.user_id == user_id)
        )
        baseline = await session.scalar(
            select(AthleteBaseline).where(AthleteBaseline.user_id == user_id)
        )
        assert ready_user is not None
        assert ready_user.status == UserStatus.BASELINE_READY
        assert preference is not None
        assert preference.status == BaselinePreferenceStatus.READY
        assert connection is not None
        assert connection.encrypted_access_token != "provider-access"
        assert connection.encrypted_access_token is not None
        assert cipher.decrypt(connection.encrypted_access_token) == "rotated-access"
        assert imported is not None
        assert imported.external_id == "8080"
        assert baseline is not None

    disconnected = await coordinator.disconnect(
        user_id=user_id,
        confirmed=True,
    )
    assert disconnected.provider_revoked
    assert disconnected.imported_history_preserved

    async with session_factory() as session:
        connection = await session.scalar(
            select(StravaConnection).where(StravaConnection.user_id == user_id)
        )
        user = await session.get(User, user_id)
        assert connection is not None
        assert connection.connection_status == ConnectionStatus.DISCONNECTED
        assert connection.encrypted_access_token is None
        assert connection.encrypted_refresh_token is None
        assert user is not None
        assert user.status == UserStatus.BASELINE_READY

    with pytest.raises(StravaNotConnectedError):
        await coordinator.manual_sync(user_id=user_id)
    async with session_factory() as session:
        unchanged_user = await session.get(User, user_id)
        unchanged_preference = await session.scalar(
            select(BaselinePreference).where(BaselinePreference.user_id == user_id)
        )
    assert unchanged_user is not None
    assert unchanged_user.status == UserStatus.BASELINE_READY
    assert unchanged_preference is not None
    assert unchanged_preference.status == BaselinePreferenceStatus.READY

    assert [request.url.path for request in requests] == [
        "/oauth/token",
        "/oauth/token",
        "/api/v3/athlete/activities",
        "/api/v3/athlete/activities",
        "/oauth/revoke",
    ]
    assert all(job_visible_during_provider_call)
    assert all(rotated_token_visible_during_provider_call)
    assert all(importing_visible_during_provider_call)

    denied_connect_url = await coordinator.issue_connect_url(user_id=user_id)
    denied_ticket = parse_qs(urlparse(denied_connect_url).query)["ticket"][0]
    denied_initiation = await coordinator.begin_oauth(raw_ticket=denied_ticket)
    denied_state = parse_qs(urlparse(denied_initiation.authorization_url).query)[
        "state"
    ][0]
    with pytest.raises(OAuthAuthorizationDeniedError):
        await coordinator.complete_oauth(
            raw_state=denied_state,
            code=None,
            accepted_scope=None,
            error="access_denied",
        )
    with pytest.raises(OAuthStateRejectedError):
        await coordinator.complete_oauth(
            raw_state=denied_state,
            code=None,
            accepted_scope=None,
            error="access_denied",
        )

    await http.aclose()


@pytest.mark.asyncio
async def test_startup_recovery_fails_stale_syncs_and_reconciles_lifecycle(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory.begin() as session:
        users = UserRepository(session)
        profiles = ProfileRepository(session)
        strava = StravaRepository(session)
        stale_without_baseline, _ = await users.get_or_create(
            telegram_user_id=2201,
            telegram_username=None,
            first_name="First",
        )
        stale_with_baseline, _ = await users.get_or_create(
            telegram_user_id=2202,
            telegram_username=None,
            first_name="Second",
        )
        fresh, _ = await users.get_or_create(
            telegram_user_id=2203,
            telegram_username=None,
            first_name="Third",
        )
        for user in (stale_without_baseline, stale_with_baseline, fresh):
            await users.update_status(
                user_id=user.id,
                status=UserStatus.BASELINE_IMPORTING,
            )
            await profiles.upsert_baseline_preference(
                user_id=user.id,
                selected_source=BaselineSource.STRAVA,
                status=BaselinePreferenceStatus.IMPORTING,
            )

        old_requested = await strava.create_sync_job(
            user_id=stale_without_baseline.id,
            sync_type=SyncType.INITIAL,
            requested_at=NOW - timedelta(hours=2),
        )
        old_running = await strava.create_sync_job(
            user_id=stale_with_baseline.id,
            sync_type=SyncType.MANUAL,
            requested_at=NOW - timedelta(hours=2),
        )
        fresh_running = await strava.create_sync_job(
            user_id=fresh.id,
            sync_type=SyncType.MANUAL,
            requested_at=NOW - timedelta(minutes=10),
        )
        assert old_requested is not None
        assert old_running is not None
        assert fresh_running is not None
        assert (
            await strava.claim_sync_job(
                user_id=stale_with_baseline.id,
                job_id=old_running.id,
                started_at=NOW - timedelta(hours=2),
            )
            is not None
        )
        assert (
            await strava.claim_sync_job(
                user_id=fresh.id,
                job_id=fresh_running.id,
                started_at=NOW - timedelta(minutes=10),
            )
            is not None
        )
        await BaselineRepository(session).create(
            user_id=stale_with_baseline.id,
            generated_at=NOW - timedelta(days=1),
            analysis_start=NOW - timedelta(days=57),
            analysis_end=NOW - timedelta(days=1),
            source=BaselineSource.STRAVA,
            status=BaselineStatus.INSUFFICIENT_DATA,
            overall_confidence=0.0,
            disciplines=[],
        )

    coordinator = StravaCoordinator(
        session_factory=session_factory,
        settings=Settings(
            environment="test",
            database_url="sqlite+aiosqlite:///:memory:",
            public_base_url="https://coach.example",
        ),
        clock=lambda: NOW,
    )

    recovered = await coordinator.recover_stale_work(
        stale_after=timedelta(minutes=30),
    )
    repeated = await coordinator.recover_stale_work(
        stale_after=timedelta(minutes=30),
    )

    assert recovered.stale_sync_jobs_failed == 2
    assert recovered.lifecycles_reconciled == 2
    assert repeated.stale_sync_jobs_failed == 0
    assert repeated.lifecycles_reconciled == 0
    async with session_factory() as session:
        stale_without_user = await session.get(User, stale_without_baseline.id)
        stale_with_user = await session.get(User, stale_with_baseline.id)
        fresh_user = await session.get(User, fresh.id)
        preferences = {
            item.user_id: item
            for item in (
                await session.scalars(
                    select(BaselinePreference).where(
                        BaselinePreference.user_id.in_(
                            (
                                stale_without_baseline.id,
                                stale_with_baseline.id,
                                fresh.id,
                            )
                        )
                    )
                )
            ).all()
        }
        jobs = {
            item.user_id: item
            for item in (
                await session.scalars(
                    select(StravaSyncJob).where(
                        StravaSyncJob.user_id.in_(
                            (
                                stale_without_baseline.id,
                                stale_with_baseline.id,
                                fresh.id,
                            )
                        )
                    )
                )
            ).all()
        }

    assert stale_without_user is not None
    assert stale_without_user.status == UserStatus.BASELINE_FAILED
    assert preferences[stale_without_baseline.id].status == (
        BaselinePreferenceStatus.FAILED
    )
    assert jobs[stale_without_baseline.id].status == SyncStatus.FAILED
    assert jobs[stale_without_baseline.id].error_code == ("strava_sync_worker_expired")
    assert stale_with_user is not None
    assert stale_with_user.status == UserStatus.BASELINE_READY
    assert preferences[stale_with_baseline.id].status == (
        BaselinePreferenceStatus.READY
    )
    assert jobs[stale_with_baseline.id].status == SyncStatus.FAILED
    assert fresh_user is not None
    assert fresh_user.status == UserStatus.BASELINE_IMPORTING
    assert preferences[fresh.id].status == BaselinePreferenceStatus.IMPORTING
    assert jobs[fresh.id].status == SyncStatus.RUNNING


@pytest.mark.asyncio
async def test_startup_recovery_replays_bounded_canonical_webhook_inbox(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    cipher = TokenCipher(TokenCipher.generate_key())
    callbacks = tuple(
        StravaWebhookEvent(
            object_type=WebhookObjectType.ATHLETE,
            object_id=7000 + index,
            aspect_type=WebhookAspectType.UPDATE,
            owner_id=7788,
            event_time=int((NOW + timedelta(seconds=index)).timestamp()),
            updates={"authorized": False},
            subscription_id=7,
        )
        for index in range(3)
    )
    async with session_factory.begin() as session:
        user, _ = await UserRepository(session).get_or_create(
            telegram_user_id=3301,
            telegram_username=None,
            first_name="Webhook",
        )
        await ProfileRepository(session).upsert_baseline_preference(
            user_id=user.id,
            selected_source=BaselineSource.STRAVA,
            status=BaselinePreferenceStatus.IMPORTING,
        )
        repository = StravaRepository(session)
        await repository.upsert_connection(
            user_id=user.id,
            strava_athlete_id=7788,
            accepted_scopes=["activity:read"],
            encrypted_access_token=cipher.encrypt("access"),
            encrypted_refresh_token=cipher.encrypt("refresh"),
            access_token_expires_at=NOW + timedelta(hours=1),
            connection_status=ConnectionStatus.CONNECTED,
            disconnected_at=None,
        )
        records = []
        for callback in callbacks:
            record = await repository.create_webhook_event(
                external_event_key=callback.external_event_key(),
                owner_id=callback.owner_id,
                object_type=callback.object_type,
                object_id=callback.object_id,
                aspect_type=callback.aspect_type,
                event_time=callback.occurred_at,
                payload=callback.model_dump(mode="json"),
            )
            assert record is not None
            records.append(record)
        await repository.update_webhook_event(
            event_id=records[1].id,
            external_event_key=records[1].external_event_key,
            status=WebhookProcessingStatus.FAILED,
            processed_at=NOW - timedelta(minutes=1),
        )
        await repository.update_webhook_event(
            event_id=records[2].id,
            external_event_key=records[2].external_event_key,
            status=WebhookProcessingStatus.PROCESSING,
            processed_at=NOW - timedelta(minutes=10),
        )

    async def no_provider_calls(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"Unexpected provider call: {request.url.path}")

    http = httpx.AsyncClient(
        transport=httpx.MockTransport(no_provider_calls),
        base_url="https://www.strava.com",
    )
    client = StravaClient(
        client_id="client-id",
        client_secret="client-secret",
        redirect_uri="https://coach.example/integrations/strava/callback",
        http_client=http,
    )
    coordinator = StravaCoordinator(
        session_factory=session_factory,
        settings=Settings(
            environment="test",
            database_url="sqlite+aiosqlite:///:memory:",
            public_base_url="https://coach.example",
            strava_webhook_verify_token="verify-token",
            strava_webhook_subscription_id="7",
        ),
        client=client,
        cipher=cipher,
        clock=lambda: NOW,
    )

    first_batch = await coordinator.recover_stale_work(
        webhook_batch_size=2,
        wait_for_webhooks=True,
    )
    second_batch = await coordinator.recover_stale_work(
        webhook_batch_size=2,
        wait_for_webhooks=True,
    )

    assert first_batch.webhook_events_scheduled == 2
    assert first_batch.webhook_events_recovered == 2
    assert first_batch.webhook_events_failed == 0
    assert second_batch.webhook_events_scheduled == 1
    assert second_batch.webhook_events_recovered == 1
    async with session_factory() as session:
        repository = StravaRepository(session)
        recovered = [
            await repository.get_webhook_event(
                event_id=record.id,
                external_event_key=record.external_event_key,
            )
            for record in records
        ]
    assert all(
        record is not None
        and record.processing_status == WebhookProcessingStatus.PROCESSED
        for record in recovered
    )
    await http.aclose()


@pytest.mark.asyncio
async def test_failed_manual_sync_preserves_existing_ready_baseline(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    cipher = TokenCipher(TokenCipher.generate_key())
    async with session_factory.begin() as session:
        user, _ = await UserRepository(session).get_or_create(
            telegram_user_id=4401,
            telegram_username=None,
            first_name="Ready",
        )
        await UserRepository(session).update_status(
            user_id=user.id,
            status=UserStatus.BASELINE_READY,
        )
        await ProfileRepository(session).upsert_baseline_preference(
            user_id=user.id,
            selected_source=BaselineSource.STRAVA,
            status=BaselinePreferenceStatus.READY,
        )
        await StravaRepository(session).upsert_connection(
            user_id=user.id,
            strava_athlete_id=8899,
            accepted_scopes=["activity:read"],
            encrypted_access_token=cipher.encrypt("access"),
            encrypted_refresh_token=cipher.encrypt("refresh"),
            access_token_expires_at=NOW + timedelta(hours=1),
            connection_status=ConnectionStatus.CONNECTED,
            disconnected_at=None,
        )
        await BaselineRepository(session).create(
            user_id=user.id,
            generated_at=NOW - timedelta(days=1),
            analysis_start=NOW - timedelta(days=57),
            analysis_end=NOW - timedelta(days=1),
            source=BaselineSource.STRAVA,
            status=BaselineStatus.READY,
            overall_confidence=0.5,
            disciplines=[],
        )

    async def unavailable(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    http = httpx.AsyncClient(
        transport=httpx.MockTransport(unavailable),
        base_url="https://www.strava.com",
    )
    client = StravaClient(
        client_id="client-id",
        client_secret="client-secret",
        redirect_uri="https://coach.example/integrations/strava/callback",
        http_client=http,
    )
    coordinator = StravaCoordinator(
        session_factory=session_factory,
        settings=Settings(
            environment="test",
            database_url="sqlite+aiosqlite:///:memory:",
            public_base_url="https://coach.example",
            strava_sync_cooldown_seconds=0,
        ),
        client=client,
        cipher=cipher,
        clock=lambda: NOW,
    )

    outcome = await coordinator.manual_sync(user_id=user.id)

    assert outcome.status == SyncStatus.FAILED
    async with session_factory() as session:
        persisted_user = await session.get(User, user.id)
        preference = await session.scalar(
            select(BaselinePreference).where(BaselinePreference.user_id == user.id)
        )
        job = await StravaRepository(session).get_latest_sync_job(user_id=user.id)
    assert persisted_user is not None
    assert persisted_user.status == UserStatus.BASELINE_READY
    assert preference is not None
    assert preference.status == BaselinePreferenceStatus.READY
    assert job is not None
    assert job.status == SyncStatus.FAILED
    await http.aclose()


@pytest.mark.asyncio
async def test_startup_recovery_resumes_pending_oauth_initial_sync(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    cipher = TokenCipher(TokenCipher.generate_key())
    async with session_factory.begin() as session:
        user, _ = await UserRepository(session).get_or_create(
            telegram_user_id=5501,
            telegram_username=None,
            first_name="Pending",
        )
        await UserRepository(session).update_status(
            user_id=user.id,
            status=UserStatus.BASELINE_PENDING,
        )
        await ProfileRepository(session).upsert_baseline_preference(
            user_id=user.id,
            selected_source=BaselineSource.STRAVA,
            status=BaselinePreferenceStatus.PENDING,
        )
        await StravaRepository(session).upsert_connection(
            user_id=user.id,
            strava_athlete_id=9900,
            accepted_scopes=["activity:read"],
            encrypted_access_token=cipher.encrypt("access"),
            encrypted_refresh_token=cipher.encrypt("refresh"),
            access_token_expires_at=NOW + timedelta(hours=1),
            connection_status=ConnectionStatus.CONNECTED,
            disconnected_at=None,
        )

    async def empty_page(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v3/athlete/activities"
        return httpx.Response(
            200,
            json=[],
            headers={
                "X-RateLimit-Limit": "200,2000",
                "X-RateLimit-Usage": "1,10",
                "X-ReadRateLimit-Limit": "100,1000",
                "X-ReadRateLimit-Usage": "1,10",
            },
        )

    http = httpx.AsyncClient(
        transport=httpx.MockTransport(empty_page),
        base_url="https://www.strava.com",
    )
    client = StravaClient(
        client_id="client-id",
        client_secret="client-secret",
        redirect_uri="https://coach.example/integrations/strava/callback",
        http_client=http,
    )
    coordinator = StravaCoordinator(
        session_factory=session_factory,
        settings=Settings(
            environment="test",
            database_url="sqlite+aiosqlite:///:memory:",
            public_base_url="https://coach.example",
        ),
        client=client,
        cipher=cipher,
        clock=lambda: NOW,
    )

    recovery = await coordinator.recover_stale_work(
        wait_for_initial_syncs=True,
    )

    assert recovery.initial_syncs_scheduled == 1
    assert recovery.initial_syncs_recovered == 1
    assert recovery.initial_syncs_failed == 0
    async with session_factory() as session:
        recovered_user = await session.get(User, user.id)
        preference = await session.scalar(
            select(BaselinePreference).where(BaselinePreference.user_id == user.id)
        )
        job = await StravaRepository(session).get_latest_sync_job(user_id=user.id)
        baseline = await BaselineRepository(session).get_latest(user_id=user.id)
    assert recovered_user is not None
    assert recovered_user.status == UserStatus.BASELINE_READY
    assert preference is not None
    assert preference.status == BaselinePreferenceStatus.READY
    assert job is not None
    assert job.status == SyncStatus.SUCCEEDED
    assert baseline is not None
    await http.aclose()
