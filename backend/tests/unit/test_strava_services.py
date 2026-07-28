"""Use-case tests for OAuth state, token rotation, sync, and webhooks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse
from uuid import UUID, uuid4

import pytest

from app.domain.enums import (
    ConnectionStatus,
    OAuthProvider,
    SyncStatus,
    WebhookAspectType,
    WebhookObjectType,
    WebhookProcessingStatus,
)
from app.integrations.strava.exceptions import (
    StravaAuthenticationError,
    StravaRateLimitedError,
    StravaUnavailableError,
)
from app.schemas.strava import (
    StravaActivityPage,
    StravaActivitySummary,
    StravaRateLimits,
    StravaTokenResponse,
    StravaWebhookEvent,
)
from app.security.encryption import TokenCipher
from app.services.strava.disconnect import StravaDisconnectService
from app.services.strava.exceptions import (
    ConcurrentSyncError,
    DisconnectConfirmationRequiredError,
    OAuthAuthorizationDeniedError,
    OAuthScopeError,
    OAuthStateRejectedError,
    SyncCooldownError,
    WebhookVerificationError,
)
from app.services.strava.oauth import StravaOAuthService
from app.services.strava.sync import StravaSyncService
from app.services.strava.tokens import StravaTokenManager
from app.services.strava.webhook import StravaWebhookService

NOW = datetime(2026, 7, 28, 12, tzinfo=UTC)


@dataclass
class State:
    id: UUID
    user_id: UUID
    state_hash: str
    expires_at: datetime
    consumed_at: datetime | None = None


@dataclass
class Connection:
    id: UUID
    user_id: UUID
    strava_athlete_id: int
    accepted_scopes: list[str]
    encrypted_access_token: str | None
    encrypted_refresh_token: str | None
    access_token_expires_at: datetime
    connection_status: ConnectionStatus = ConnectionStatus.CONNECTED
    last_successful_sync_at: datetime | None = None


class OAuthRepositoryFake:
    def __init__(self) -> None:
        self.states: dict[str, State] = {}
        self.connection_values: dict[str, object] | None = None

    async def create_oauth_state(
        self,
        *,
        user_id: UUID,
        provider: OAuthProvider,
        state_hash: str,
        expires_at: datetime,
    ) -> State:
        assert provider == OAuthProvider.STRAVA
        state = State(uuid4(), user_id, state_hash, expires_at)
        self.states[state_hash] = state
        return state

    async def consume_oauth_state_by_hash(
        self,
        *,
        provider: OAuthProvider,
        state_hash: str,
        now: datetime,
        expected_user_id: UUID | None = None,
    ) -> State | None:
        assert provider == OAuthProvider.STRAVA
        state = self.states.get(state_hash)
        if (
            state is None
            or state.consumed_at is not None
            or state.expires_at <= now
            or (expected_user_id is not None and state.user_id != expected_user_id)
        ):
            return None
        state.consumed_at = now
        return state

    async def upsert_connection(self, **values: object) -> Connection:
        self.connection_values = values
        return Connection(
            id=uuid4(),
            user_id=values["user_id"],  # type: ignore[arg-type]
            strava_athlete_id=values["strava_athlete_id"],  # type: ignore[arg-type]
            accepted_scopes=values["accepted_scopes"],  # type: ignore[arg-type]
            encrypted_access_token=values["encrypted_access_token"],  # type: ignore[arg-type]
            encrypted_refresh_token=values["encrypted_refresh_token"],  # type: ignore[arg-type]
            access_token_expires_at=values["access_token_expires_at"],  # type: ignore[arg-type]
        )


class OAuthClientFake:
    def __init__(
        self,
        *,
        granted_scope: str = "read,activity:read_all",
    ) -> None:
        self.granted_scope = granted_scope
        self.revoked = False
        self.exchanged = False

    def authorization_url(
        self,
        *,
        state: str,
        scopes: list[str],
        approval_prompt: str = "auto",
    ) -> str:
        del approval_prompt
        return f"https://www.strava.com/oauth/authorize?state={state}&scope={','.join(scopes)}"

    async def exchange_code(self, code: str) -> StravaTokenResponse:
        assert code == "authorization-code"
        self.exchanged = True
        return StravaTokenResponse(
            access_token="access-token",
            refresh_token="refresh-token",
            expires_at=int((NOW + timedelta(hours=6)).timestamp()),
            athlete={"id": 4242},
            scope=self.granted_scope,
        )

    async def revoke(self, access_token: str) -> None:
        assert access_token == "access-token"
        self.revoked = True


def raw_state_from_url(url: str) -> str:
    return parse_qs(urlparse(url).query)["state"][0]


@pytest.mark.asyncio
async def test_oauth_state_is_hashed_expiring_owned_and_single_use() -> None:
    user_id = uuid4()
    wrong_user_id = uuid4()
    repository = OAuthRepositoryFake()
    cipher = TokenCipher(TokenCipher.generate_key())
    requested: list[UUID] = []

    async def request_sync(sync_user_id: UUID) -> None:
        requested.append(sync_user_id)

    service = StravaOAuthService(
        repository=repository,  # type: ignore[arg-type]
        client=OAuthClientFake(),  # type: ignore[arg-type]
        cipher=cipher,
        request_initial_sync=request_sync,
        clock=lambda: NOW,
    )
    initiation = await service.begin(user_id=user_id)
    raw_state = raw_state_from_url(initiation.authorization_url)

    assert raw_state not in repository.states
    assert str(user_id) not in initiation.authorization_url
    requested_scopes = parse_qs(urlparse(initiation.authorization_url).query)["scope"][
        0
    ].split(",")
    assert set(requested_scopes) == {"read", "activity:read_all"}
    assert len(requested_scopes) == 2
    assert len(repository.states) == 1
    with pytest.raises(OAuthStateRejectedError):
        await service.complete(
            raw_state=raw_state,
            code="authorization-code",
            accepted_scope="read,activity:read_all",
            expected_user_id=wrong_user_id,
        )

    completed = await service.complete(
        raw_state=raw_state,
        code="authorization-code",
        accepted_scope="read,activity:read_all",
        expected_user_id=user_id,
    )

    assert completed.user_id == user_id
    assert requested == [user_id]
    assert repository.connection_values is not None
    encrypted_access = repository.connection_values["encrypted_access_token"]
    encrypted_refresh = repository.connection_values["encrypted_refresh_token"]
    assert isinstance(encrypted_access, str)
    assert isinstance(encrypted_refresh, str)
    assert encrypted_access != "access-token"
    assert cipher.decrypt(encrypted_access) == "access-token"
    assert cipher.decrypt(encrypted_refresh) == "refresh-token"
    with pytest.raises(OAuthStateRejectedError):
        await service.complete(
            raw_state=raw_state,
            code="authorization-code",
            accepted_scope="read,activity:read_all",
        )


@pytest.mark.asyncio
async def test_expired_denied_and_missing_scope_callbacks_are_rejected() -> None:
    repository = OAuthRepositoryFake()
    service = StravaOAuthService(
        repository=repository,  # type: ignore[arg-type]
        client=OAuthClientFake(),  # type: ignore[arg-type]
        cipher=TokenCipher(TokenCipher.generate_key()),
        clock=lambda: NOW,
    )
    expired = await service.begin(user_id=uuid4())
    expired_state = raw_state_from_url(expired.authorization_url)
    repository.states[next(iter(repository.states))].expires_at = NOW
    with pytest.raises(OAuthStateRejectedError):
        await service.complete(
            raw_state=expired_state,
            code="authorization-code",
            accepted_scope="read,activity:read_all",
        )

    repository.states.clear()
    denied = await service.begin(user_id=uuid4())
    with pytest.raises(OAuthAuthorizationDeniedError):
        await service.complete(
            raw_state=raw_state_from_url(denied.authorization_url),
            code=None,
            accepted_scope=None,
            error="access_denied",
        )

    callback_scope_client = OAuthClientFake()
    service = StravaOAuthService(
        repository=repository,  # type: ignore[arg-type]
        client=callback_scope_client,  # type: ignore[arg-type]
        cipher=TokenCipher(TokenCipher.generate_key()),
        clock=lambda: NOW,
    )
    insufficient = await service.begin(user_id=uuid4())
    with pytest.raises(OAuthScopeError) as caught:
        await service.complete(
            raw_state=raw_state_from_url(insufficient.authorization_url),
            code="authorization-code",
            accepted_scope="read",
        )
    assert caught.value.missing_scopes == frozenset({"activity:read_all"})
    assert not callback_scope_client.exchanged

    insufficient_client = OAuthClientFake(granted_scope="read")
    service = StravaOAuthService(
        repository=repository,  # type: ignore[arg-type]
        client=insufficient_client,  # type: ignore[arg-type]
        cipher=TokenCipher(TokenCipher.generate_key()),
        clock=lambda: NOW,
    )
    insufficient_token = await service.begin(user_id=uuid4())
    with pytest.raises(OAuthScopeError) as caught:
        await service.complete(
            raw_state=raw_state_from_url(insufficient_token.authorization_url),
            code="authorization-code",
            accepted_scope="read,activity:read_all",
        )
    assert caught.value.missing_scopes == frozenset({"activity:read_all"})
    assert insufficient_client.revoked


class TokenRepositoryFake:
    def __init__(self, connection: Connection, cipher: TokenCipher) -> None:
        self.connection = connection
        self.cipher = cipher
        self.rotations = 0
        self.refresh_failures = 0

    async def lock_connection_for_token_refresh(
        self,
        *,
        user_id: UUID,
        connection_id: UUID,
    ) -> Connection | None:
        if user_id != self.connection.user_id or connection_id != self.connection.id:
            return None
        return self.connection

    async def rotate_tokens(self, **values: object) -> Connection | None:
        assert (
            values["expected_encrypted_refresh_token"]
            == self.connection.encrypted_refresh_token
        )
        self.rotations += 1
        self.connection = Connection(
            id=self.connection.id,
            user_id=self.connection.user_id,
            strava_athlete_id=self.connection.strava_athlete_id,
            accepted_scopes=self.connection.accepted_scopes,
            encrypted_access_token=values["encrypted_access_token"],  # type: ignore[arg-type]
            encrypted_refresh_token=values["encrypted_refresh_token"],  # type: ignore[arg-type]
            access_token_expires_at=values["access_token_expires_at"],  # type: ignore[arg-type]
        )
        return self.connection

    async def mark_refresh_failed(self, **_values: object) -> bool:
        self.refresh_failures += 1
        return True

    async def get_connection(self, *, user_id: UUID) -> Connection | None:
        assert user_id == self.connection.user_id
        return self.connection


class RefreshClientFake:
    async def refresh_token(self, refresh_token: str) -> StravaTokenResponse:
        assert refresh_token == "old-refresh"
        return StravaTokenResponse(
            access_token="rotated-access",
            refresh_token="rotated-refresh",
            expires_at=int((NOW + timedelta(hours=6)).timestamp()),
        )


@pytest.mark.asyncio
async def test_expiring_access_token_rotates_both_tokens_atomically() -> None:
    cipher = TokenCipher(TokenCipher.generate_key())
    connection = Connection(
        id=uuid4(),
        user_id=uuid4(),
        strava_athlete_id=42,
        accepted_scopes=["read", "activity:read_all"],
        encrypted_access_token=cipher.encrypt("old-access"),
        encrypted_refresh_token=cipher.encrypt("old-refresh"),
        access_token_expires_at=NOW + timedelta(seconds=30),
    )
    repository = TokenRepositoryFake(connection, cipher)
    manager = StravaTokenManager(
        repository=repository,  # type: ignore[arg-type]
        client=RefreshClientFake(),  # type: ignore[arg-type]
        cipher=cipher,
        clock=lambda: NOW,
    )

    access_token = await manager.access_token(connection=connection)

    assert access_token == "rotated-access"
    assert repository.rotations == 1
    assert repository.connection.encrypted_refresh_token is not None
    assert (
        cipher.decrypt(repository.connection.encrypted_refresh_token)
        == "rotated-refresh"
    )


class DisconnectRepositoryFake:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection
        self.erased = False

    async def get_connection(self, *, user_id: UUID) -> Connection | None:
        return self.connection if user_id == self.connection.user_id else None

    async def disconnect_connection(self, **values: object) -> bool:
        assert values["user_id"] == self.connection.user_id
        self.connection.encrypted_access_token = None
        self.connection.encrypted_refresh_token = None
        self.connection.connection_status = ConnectionStatus.DISCONNECTED
        self.erased = True
        return True


class RevokeClientFake:
    def __init__(self) -> None:
        self.revoked: list[str] = []

    async def revoke(self, access_token: str) -> None:
        self.revoked.append(access_token)


@pytest.mark.asyncio
async def test_confirmed_disconnect_revokes_and_erases_local_tokens() -> None:
    cipher = TokenCipher(TokenCipher.generate_key())
    connection = Connection(
        id=uuid4(),
        user_id=uuid4(),
        strava_athlete_id=42,
        accepted_scopes=[],
        encrypted_access_token=cipher.encrypt("access"),
        encrypted_refresh_token=cipher.encrypt("refresh"),
        access_token_expires_at=NOW,
    )
    repository = DisconnectRepositoryFake(connection)
    client = RevokeClientFake()
    service = StravaDisconnectService(
        repository=repository,  # type: ignore[arg-type]
        client=client,  # type: ignore[arg-type]
        cipher=cipher,
        clock=lambda: NOW,
    )

    with pytest.raises(DisconnectConfirmationRequiredError):
        await service.disconnect(user_id=connection.user_id, confirmed=False)
    outcome = await service.disconnect(
        user_id=connection.user_id,
        confirmed=True,
    )

    assert client.revoked == ["access"]
    assert outcome.provider_revoked
    assert outcome.local_tokens_erased
    assert outcome.imported_history_preserved
    assert connection.encrypted_access_token is None
    assert connection.encrypted_refresh_token is None


class SyncRepositoryFake:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection
        self.concurrent = False
        self.job = SimpleNamespace(id=uuid4(), user_id=connection.user_id)
        self.job_updates: list[dict[str, object]] = []
        self.activities: dict[str, dict[str, object]] = {}
        self.marked_sync = 0

    async def get_connection(self, *, user_id: UUID) -> Connection | None:
        return self.connection if user_id == self.connection.user_id else None

    async def create_sync_job(self, **_values: object) -> object | None:
        return None if self.concurrent else self.job

    async def claim_sync_job(self, **_values: object) -> object:
        return self.job

    async def update_sync_job(self, **values: object) -> None:
        self.job_updates.append(values)

    async def mark_sync_succeeded(self, **_values: object) -> None:
        self.marked_sync += 1

    async def upsert_activity(
        self,
        *,
        user_id: UUID,
        source: object,
        external_id: str,
        values: dict[str, object],
    ) -> str:
        del source
        assert user_id == self.connection.user_id
        previous = self.activities.get(external_id)
        self.activities[external_id] = values
        return "inserted" if previous is None else "unchanged"


class StaticTokenManager:
    def __init__(self) -> None:
        self.calls: list[bool] = []

    async def access_token(
        self,
        *,
        connection: object,
        force_refresh: bool = False,
    ) -> str:
        del connection
        self.calls.append(force_refresh)
        return "access"


class SyncClientFake:
    def __init__(self, pages: list[StravaActivityPage]) -> None:
        self.pages = pages
        self.calls: list[int] = []

    async def get_activities_page(self, **values: object) -> StravaActivityPage:
        page = values["page"]
        assert isinstance(page, int)
        self.calls.append(page)
        return self.pages[page - 1]


class BaselineFake:
    def __init__(self) -> None:
        self.users: list[UUID] = []

    async def recalculate(self, *, user_id: UUID) -> object:
        self.users.append(user_id)
        return object()


def summary(activity_id: int, started_at: datetime) -> StravaActivitySummary:
    return StravaActivitySummary(
        id=activity_id,
        name="Activity",
        sport_type="Run",
        start_date=started_at,
        elapsed_time=1800,
    )


@pytest.mark.asyncio
async def test_initial_sync_paginates_until_empty_and_recalculates() -> None:
    user_id = uuid4()
    connection = Connection(
        id=uuid4(),
        user_id=user_id,
        strava_athlete_id=42,
        accepted_scopes=["read", "activity:read_all"],
        encrypted_access_token="ciphertext",
        encrypted_refresh_token="ciphertext",
        access_token_expires_at=NOW + timedelta(hours=1),
    )
    repository = SyncRepositoryFake(connection)
    limits = StravaRateLimits()
    client = SyncClientFake(
        [
            StravaActivityPage(
                activities=[summary(1, NOW - timedelta(days=1))],
                rate_limits=limits,
            ),
            StravaActivityPage(activities=[], rate_limits=limits),
        ]
    )
    baseline = BaselineFake()
    token_manager = StaticTokenManager()
    service = StravaSyncService(
        repository=repository,  # type: ignore[arg-type]
        client=client,  # type: ignore[arg-type]
        token_manager=token_manager,  # type: ignore[arg-type]
        baseline=baseline,  # type: ignore[arg-type]
        clock=lambda: NOW,
    )

    outcome = await service.initial_sync(user_id=user_id)

    assert outcome.status == SyncStatus.SUCCEEDED
    assert outcome.stats.imported_count == 1
    assert client.calls == [1, 2]
    assert token_manager.calls == [False, False]
    assert baseline.users == [user_id]
    assert repository.marked_sync == 1


class AuthenticationRetryClientFake:
    def __init__(self) -> None:
        self.access_tokens: list[object] = []

    async def get_activities_page(self, **values: object) -> StravaActivityPage:
        self.access_tokens.append(values["access_token"])
        if len(self.access_tokens) == 1:
            raise StravaAuthenticationError()
        return StravaActivityPage(
            activities=[],
            rate_limits=StravaRateLimits(),
        )


class RotatingTokenManagerFake:
    def __init__(self) -> None:
        self.current = "access"
        self.calls: list[bool] = []

    async def access_token(
        self,
        *,
        connection: object,
        force_refresh: bool = False,
    ) -> str:
        del connection
        self.calls.append(force_refresh)
        if force_refresh:
            self.current = "refreshed"
        return self.current


@pytest.mark.asyncio
async def test_sync_retries_one_401_with_forced_refresh() -> None:
    user_id = uuid4()
    connection = Connection(
        id=uuid4(),
        user_id=user_id,
        strava_athlete_id=42,
        accepted_scopes=["activity:read"],
        encrypted_access_token="ciphertext",
        encrypted_refresh_token="ciphertext",
        access_token_expires_at=NOW + timedelta(hours=1),
    )
    repository = SyncRepositoryFake(connection)
    client = AuthenticationRetryClientFake()
    tokens = RotatingTokenManagerFake()
    service = StravaSyncService(
        repository=repository,  # type: ignore[arg-type]
        client=client,  # type: ignore[arg-type]
        token_manager=tokens,  # type: ignore[arg-type]
        baseline=BaselineFake(),  # type: ignore[arg-type]
        clock=lambda: NOW,
    )

    outcome = await service.initial_sync(user_id=user_id)

    assert outcome.status == SyncStatus.SUCCEEDED
    assert tokens.calls == [False, True, False]
    assert client.access_tokens == ["access", "refreshed"]


class PartialFailureClientFake:
    def __init__(self) -> None:
        self.calls = 0

    async def get_activities_page(self, **_values: object) -> StravaActivityPage:
        self.calls += 1
        if self.calls == 1:
            return StravaActivityPage(
                activities=[summary(501, NOW - timedelta(days=1))],
                rate_limits=StravaRateLimits(),
            )
        raise StravaUnavailableError()


@pytest.mark.asyncio
async def test_sync_persists_partial_progress_and_recalculates_baseline() -> None:
    user_id = uuid4()
    connection = Connection(
        id=uuid4(),
        user_id=user_id,
        strava_athlete_id=42,
        accepted_scopes=["activity:read"],
        encrypted_access_token="ciphertext",
        encrypted_refresh_token="ciphertext",
        access_token_expires_at=NOW + timedelta(hours=1),
    )
    repository = SyncRepositoryFake(connection)
    client = PartialFailureClientFake()
    tokens = StaticTokenManager()
    baseline = BaselineFake()
    service = StravaSyncService(
        repository=repository,  # type: ignore[arg-type]
        client=client,  # type: ignore[arg-type]
        token_manager=tokens,  # type: ignore[arg-type]
        baseline=baseline,  # type: ignore[arg-type]
        clock=lambda: NOW,
    )

    outcome = await service.initial_sync(user_id=user_id)

    assert outcome.status == SyncStatus.PARTIAL
    assert outcome.stats.imported_count == 1
    assert outcome.stats.failed_count == 1
    assert tokens.calls == [False, False]
    assert baseline.users == [user_id]
    assert repository.job_updates[-1]["status"] == SyncStatus.PARTIAL
    assert repository.job_updates[-1]["imported_count"] == 1
    assert repository.job_updates[-1]["failed_count"] == 1


@pytest.mark.asyncio
async def test_sync_stops_at_cutoff_and_rejects_concurrency_and_cooldown() -> None:
    user_id = uuid4()
    connection = Connection(
        id=uuid4(),
        user_id=user_id,
        strava_athlete_id=42,
        accepted_scopes=["read", "activity:read_all"],
        encrypted_access_token="ciphertext",
        encrypted_refresh_token="ciphertext",
        access_token_expires_at=NOW + timedelta(hours=1),
    )
    repository = SyncRepositoryFake(connection)
    client = SyncClientFake(
        [
            StravaActivityPage(
                activities=[
                    summary(1, NOW - timedelta(days=2)),
                    summary(2, NOW - timedelta(days=57)),
                ],
                rate_limits=StravaRateLimits(),
            )
        ]
    )
    service = StravaSyncService(
        repository=repository,  # type: ignore[arg-type]
        client=client,  # type: ignore[arg-type]
        token_manager=StaticTokenManager(),  # type: ignore[arg-type]
        baseline=BaselineFake(),  # type: ignore[arg-type]
        clock=lambda: NOW,
    )

    outcome = await service.initial_sync(user_id=user_id)
    assert outcome.stats.stopped_at_cutoff
    assert set(repository.activities) == {"1"}

    repository.concurrent = True
    with pytest.raises(ConcurrentSyncError):
        await service.initial_sync(user_id=user_id)

    repository.concurrent = False
    connection.last_successful_sync_at = NOW - timedelta(minutes=1)
    with pytest.raises(SyncCooldownError):
        await service.manual_sync(user_id=user_id)


class RateLimitedClientFake:
    async def get_activities_page(self, **_values: object) -> StravaActivityPage:
        raise StravaRateLimitedError(
            rate_limits=StravaRateLimits(),
            retry_after_seconds=60,
        )


@pytest.mark.asyncio
async def test_sync_does_not_retry_429() -> None:
    user_id = uuid4()
    connection = Connection(
        id=uuid4(),
        user_id=user_id,
        strava_athlete_id=42,
        accepted_scopes=[],
        encrypted_access_token="ciphertext",
        encrypted_refresh_token="ciphertext",
        access_token_expires_at=NOW + timedelta(hours=1),
    )
    repository = SyncRepositoryFake(connection)
    service = StravaSyncService(
        repository=repository,  # type: ignore[arg-type]
        client=RateLimitedClientFake(),  # type: ignore[arg-type]
        token_manager=StaticTokenManager(),  # type: ignore[arg-type]
        baseline=BaselineFake(),  # type: ignore[arg-type]
        clock=lambda: NOW,
    )

    outcome = await service.initial_sync(user_id=user_id)

    assert outcome.status == SyncStatus.RATE_LIMITED
    assert outcome.stats.rate_limited


class WebhookRepositoryFake:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection
        self.records: dict[str, SimpleNamespace] = {}
        self.statuses: list[object] = []
        self.upserts = 0
        self.deletes = 0
        self.disconnects = 0

    async def create_webhook_event(self, **values: object) -> object | None:
        key = values["external_event_key"]
        assert isinstance(key, str)
        if key in self.records:
            return None
        record = SimpleNamespace(
            id=uuid4(),
            external_event_key=key,
            owner_id=values["owner_id"],
            object_type=values["object_type"],
            object_id=values["object_id"],
            aspect_type=values["aspect_type"],
            event_time=values["event_time"],
            payload=values["payload"],
            processing_status=WebhookProcessingStatus.PENDING,
            created_at=NOW,
            processed_at=None,
        )
        self.records[key] = record
        return record

    async def get_webhook_event_by_key(
        self,
        *,
        external_event_key: str,
    ) -> object | None:
        return self.records.get(external_event_key)

    async def get_webhook_event(
        self,
        *,
        event_id: UUID,
        external_event_key: str,
    ) -> object | None:
        return next(
            (
                item
                for item in self.records.values()
                if item.id == event_id and item.external_event_key == external_event_key
            ),
            None,
        )

    async def claim_webhook_event(
        self,
        *,
        event_id: UUID,
        external_event_key: str,
        claimed_at: datetime,
        stale_before: datetime,
    ) -> bool:
        record = next(
            (item for item in self.records.values() if item.id == event_id),
            None,
        )
        if record is None or record.external_event_key != external_event_key:
            return False
        reclaimable = record.processing_status in {
            WebhookProcessingStatus.PENDING,
            WebhookProcessingStatus.FAILED,
        } or (
            record.processing_status == WebhookProcessingStatus.PROCESSING
            and (record.processed_at or record.created_at) <= stale_before
        )
        if not reclaimable:
            return False
        record.processing_status = WebhookProcessingStatus.PROCESSING
        record.processed_at = claimed_at
        return True

    async def get_connection_by_athlete_id(
        self,
        *,
        strava_athlete_id: int,
    ) -> Connection | None:
        return (
            self.connection
            if strava_athlete_id == self.connection.strava_athlete_id
            else None
        )

    async def update_webhook_event(self, **values: object) -> bool:
        self.statuses.append(values["status"])
        event_id = values["event_id"]
        external_event_key = values["external_event_key"]
        record = next(
            (
                item
                for item in self.records.values()
                if item.id == event_id and item.external_event_key == external_event_key
            ),
            None,
        )
        if record is None:
            return False
        record.processing_status = values["status"]
        record.processed_at = values["processed_at"]
        return True

    async def upsert_activity(self, **_values: object) -> str:
        self.upserts += 1
        return "inserted"

    async def mark_activity_deleted(self, **_values: object) -> bool:
        self.deletes += 1
        return True

    async def disconnect_connection(self, **_values: object) -> bool:
        self.disconnects += 1
        return True


class WebhookClientFake:
    async def get_activity(
        self,
        *,
        access_token: str,
        activity_id: int,
    ) -> tuple[StravaActivitySummary, StravaRateLimits]:
        assert access_token == "access"
        return summary(activity_id, NOW - timedelta(hours=1)), StravaRateLimits()


def webhook(
    *,
    aspect: WebhookAspectType = WebhookAspectType.CREATE,
    object_type: WebhookObjectType = WebhookObjectType.ACTIVITY,
    object_id: int = 100,
    updates: dict[str, object] | None = None,
) -> StravaWebhookEvent:
    return StravaWebhookEvent(
        object_type=object_type,
        object_id=object_id,
        aspect_type=aspect,
        owner_id=42,
        event_time=int(NOW.timestamp()),
        updates=updates or {},
        subscription_id=7,
    )


def webhook_service(
    repository: WebhookRepositoryFake,
    baseline: BaselineFake,
) -> StravaWebhookService:
    return StravaWebhookService(
        repository=repository,  # type: ignore[arg-type]
        client=WebhookClientFake(),  # type: ignore[arg-type]
        token_manager=StaticTokenManager(),  # type: ignore[arg-type]
        baseline=baseline,  # type: ignore[arg-type]
        verify_token="verify-me",
        subscription_id=7,
        clock=lambda: NOW,
    )


def test_webhook_verification_requires_exact_token_and_echoes_challenge() -> None:
    connection = Connection(
        id=uuid4(),
        user_id=uuid4(),
        strava_athlete_id=42,
        accepted_scopes=[],
        encrypted_access_token="ciphertext",
        encrypted_refresh_token="ciphertext",
        access_token_expires_at=NOW,
    )
    service = webhook_service(WebhookRepositoryFake(connection), BaselineFake())

    assert service.verify(
        mode="subscribe",
        verify_token="verify-me",
        challenge="challenge",
    ) == {"hub.challenge": "challenge"}
    with pytest.raises(WebhookVerificationError):
        service.verify(
            mode="subscribe",
            verify_token="verify-me-wrong",
            challenge="challenge",
        )


@pytest.mark.asyncio
async def test_webhook_create_update_delete_deduplicate_and_recalculate() -> None:
    connection = Connection(
        id=uuid4(),
        user_id=uuid4(),
        strava_athlete_id=42,
        accepted_scopes=[],
        encrypted_access_token="ciphertext",
        encrypted_refresh_token="ciphertext",
        access_token_expires_at=NOW + timedelta(hours=1),
    )
    repository = WebhookRepositoryFake(connection)
    baseline = BaselineFake()
    service = webhook_service(repository, baseline)
    create = webhook()

    first = await service.ingest(event=create)
    duplicate = await service.ingest(event=create)
    updated = await service.ingest(event=webhook(aspect=WebhookAspectType.UPDATE))
    deleted = await service.ingest(event=webhook(aspect=WebhookAspectType.DELETE))

    assert first.status == "processed"
    assert duplicate.status == "duplicate"
    assert updated.status == "processed"
    assert deleted.status == "processed"
    assert repository.upserts == 2
    assert repository.deletes == 1
    assert baseline.users == [
        connection.user_id,
        connection.user_id,
        connection.user_id,
    ]


@pytest.mark.asyncio
async def test_webhook_redelivery_recovers_failed_and_expired_processing() -> None:
    connection = Connection(
        id=uuid4(),
        user_id=uuid4(),
        strava_athlete_id=42,
        accepted_scopes=[],
        encrypted_access_token="ciphertext",
        encrypted_refresh_token="ciphertext",
        access_token_expires_at=NOW + timedelta(hours=1),
    )
    repository = WebhookRepositoryFake(connection)
    service = webhook_service(repository, BaselineFake())

    failed_event = webhook(object_id=101)
    failed_acceptance = await service.accept(event=failed_event)
    assert failed_acceptance.event_id is not None
    failed_record = repository.records[failed_event.external_event_key()]
    failed_record.processing_status = WebhookProcessingStatus.FAILED
    failed_record.processed_at = NOW

    failed_retry = await service.accept(event=failed_event)
    assert failed_retry.event_id is not None
    failed_outcome = await service.process(
        event_id=failed_retry.event_id,
        external_event_key=failed_event.external_event_key(),
    )

    expired_event = webhook(object_id=102)
    expired_acceptance = await service.accept(event=expired_event)
    assert expired_acceptance.event_id is not None
    expired_record = repository.records[expired_event.external_event_key()]
    expired_record.processing_status = WebhookProcessingStatus.PROCESSING
    expired_record.processed_at = NOW - timedelta(minutes=6)

    expired_retry = await service.accept(event=expired_event)
    assert expired_retry.event_id is not None
    expired_outcome = await service.process(
        event_id=expired_retry.event_id,
        external_event_key=expired_event.external_event_key(),
    )

    assert failed_retry.status == "accepted"
    assert failed_outcome.status == "processed"
    assert expired_retry.status == "accepted"
    assert expired_outcome.status == "processed"


@pytest.mark.asyncio
async def test_webhook_does_not_reclaim_recent_or_terminal_processing() -> None:
    connection = Connection(
        id=uuid4(),
        user_id=uuid4(),
        strava_athlete_id=42,
        accepted_scopes=[],
        encrypted_access_token="ciphertext",
        encrypted_refresh_token="ciphertext",
        access_token_expires_at=NOW + timedelta(hours=1),
    )
    repository = WebhookRepositoryFake(connection)
    service = webhook_service(repository, BaselineFake())
    event = webhook(object_id=103)
    acceptance = await service.accept(event=event)
    assert acceptance.event_id is not None
    record = repository.records[event.external_event_key()]
    record.processing_status = WebhookProcessingStatus.PROCESSING
    record.processed_at = NOW

    recent_retry = await service.accept(event=event)
    assert recent_retry.status == "duplicate"

    record.processing_status = WebhookProcessingStatus.PROCESSED
    terminal_retry = await service.accept(event=event)
    assert terminal_retry.status == "duplicate"


@pytest.mark.asyncio
async def test_webhook_rejects_mismatched_inbox_id_and_payload() -> None:
    connection = Connection(
        id=uuid4(),
        user_id=uuid4(),
        strava_athlete_id=42,
        accepted_scopes=[],
        encrypted_access_token="ciphertext",
        encrypted_refresh_token="ciphertext",
        access_token_expires_at=NOW + timedelta(hours=1),
    )
    repository = WebhookRepositoryFake(connection)
    service = webhook_service(repository, BaselineFake())
    first_event = webhook(object_id=104)
    second_event = webhook(object_id=105)
    first = await service.accept(event=first_event)
    second = await service.accept(event=second_event)
    assert first.event_id is not None
    assert second.event_id is not None
    repository.records[
        first_event.external_event_key()
    ].payload = second_event.model_dump(mode="json")

    outcome = await service.process(
        event_id=first.event_id,
        external_event_key=first_event.external_event_key(),
    )

    assert outcome.status == "failed"
    assert repository.upserts == 0
    assert (
        repository.records[first_event.external_event_key()].processing_status
        == WebhookProcessingStatus.FAILED
    )
    assert (
        repository.records[second_event.external_event_key()].processing_status
        == WebhookProcessingStatus.PENDING
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("authorized", [False, "false", "False", 0])
async def test_webhook_detects_athlete_deauthorization(
    authorized: object,
) -> None:
    connection = Connection(
        id=uuid4(),
        user_id=uuid4(),
        strava_athlete_id=42,
        accepted_scopes=[],
        encrypted_access_token="ciphertext",
        encrypted_refresh_token="ciphertext",
        access_token_expires_at=NOW,
    )
    repository = WebhookRepositoryFake(connection)
    service = webhook_service(repository, BaselineFake())

    outcome = await service.ingest(
        event=webhook(
            aspect=WebhookAspectType.UPDATE,
            object_type=WebhookObjectType.ATHLETE,
            updates={"authorized": authorized},
        )
    )

    assert outcome.status == "processed"
    assert repository.disconnects == 1
