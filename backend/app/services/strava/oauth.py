"""Strava OAuth state lifecycle and callback completion."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.domain.enums import ConnectionStatus, OAuthProvider
from app.integrations.strava.client import StravaClient
from app.integrations.strava.exceptions import StravaProviderError
from app.schemas.strava import normalize_scopes
from app.security.encryption import TokenCipher
from app.security.oauth_state import digest_oauth_state, new_oauth_state
from app.services.strava.exceptions import (
    OAuthAuthorizationDeniedError,
    OAuthScopeError,
    OAuthStateRejectedError,
)
from app.services.strava.protocols import StravaRepositoryProtocol

REQUIRED_STRAVA_SCOPES = frozenset({"activity:read"})


@dataclass(frozen=True, slots=True)
class OAuthInitiation:
    """Browser URL and expiry returned to the connect endpoint."""

    authorization_url: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class OAuthCompletion:
    """Safe callback outcome."""

    user_id: UUID
    strava_athlete_id: int
    accepted_scopes: frozenset[str]
    initial_sync_requested: bool


InitialSyncRequester = Callable[[UUID], Awaitable[object]]


class StravaOAuthService:
    """Issue one-time states and persist encrypted callback credentials."""

    def __init__(
        self,
        *,
        repository: StravaRepositoryProtocol,
        client: StravaClient,
        cipher: TokenCipher,
        state_ttl: timedelta = timedelta(minutes=10),
        required_scopes: frozenset[str] = REQUIRED_STRAVA_SCOPES,
        request_initial_sync: InitialSyncRequester | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if state_ttl <= timedelta(0):
            raise ValueError("state_ttl must be positive.")
        if not required_scopes:
            raise ValueError("At least one required Strava scope is needed.")
        self._repository = repository
        self._client = client
        self._cipher = cipher
        self._state_ttl = state_ttl
        self._required_scopes = required_scopes
        self._request_initial_sync = request_initial_sync
        self._clock = clock or (lambda: datetime.now(UTC))

    async def begin(self, *, user_id: UUID) -> OAuthInitiation:
        """Persist a digest and return the only copy of the raw browser state."""

        state = new_oauth_state(ttl=self._state_ttl, now=self._now())
        await self._repository.create_oauth_state(
            user_id=user_id,
            provider=OAuthProvider.STRAVA,
            state_hash=state.digest,
            expires_at=state.expires_at,
        )
        return OAuthInitiation(
            authorization_url=self._client.authorization_url(
                state=state.raw,
                scopes=sorted(self._required_scopes),
            ),
            expires_at=state.expires_at,
        )

    async def complete(
        self,
        *,
        raw_state: str,
        code: str | None,
        accepted_scope: str | Sequence[str] | None,
        error: str | None = None,
        expected_user_id: UUID | None = None,
    ) -> OAuthCompletion:
        """Consume state once, validate scope, exchange, encrypt, and persist."""

        user_id = await self.consume_callback_state(
            raw_state=raw_state,
            expected_user_id=expected_user_id,
        )
        return await self.complete_consumed(
            user_id=user_id,
            code=code,
            accepted_scope=accepted_scope,
            error=error,
        )

    async def consume_callback_state(
        self,
        *,
        raw_state: str,
        expected_user_id: UUID | None = None,
    ) -> UUID:
        """Atomically consume provider state and return its owning user."""

        try:
            state_hash = digest_oauth_state(raw_state)
        except ValueError as exc:
            raise OAuthStateRejectedError() from exc
        state = await self._repository.consume_oauth_state_by_hash(
            provider=OAuthProvider.STRAVA,
            state_hash=state_hash,
            now=self._now(),
            expected_user_id=expected_user_id,
        )
        if state is None:
            raise OAuthStateRejectedError()
        return state.user_id

    async def complete_consumed(
        self,
        *,
        user_id: UUID,
        code: str | None,
        accepted_scope: str | Sequence[str] | None,
        error: str | None = None,
    ) -> OAuthCompletion:
        """Complete a callback whose state consumption was already committed."""

        if error is not None:
            raise OAuthAuthorizationDeniedError()
        if not code:
            raise OAuthAuthorizationDeniedError()
        del accepted_scope  # The token response is the authoritative scope grant.
        token_response = await self._client.exchange_code(code)
        accepted = normalize_scopes(token_response.scope)
        missing = self._required_scopes - accepted
        if missing:
            try:
                await self._client.revoke(token_response.access_token)
            except StravaProviderError:
                pass
            raise OAuthScopeError(frozenset(missing))
        if token_response.athlete is None:
            from app.integrations.strava.exceptions import StravaResponseError

            raise StravaResponseError()
        try:
            await self._repository.upsert_connection(
                user_id=user_id,
                strava_athlete_id=token_response.athlete.id,
                accepted_scopes=sorted(accepted),
                encrypted_access_token=self._cipher.encrypt(
                    token_response.access_token
                ),
                encrypted_refresh_token=self._cipher.encrypt(
                    token_response.refresh_token
                ),
                access_token_expires_at=token_response.expires_at_datetime,
                connection_status=ConnectionStatus.CONNECTED,
                disconnected_at=None,
            )
        except Exception:
            try:
                await self._client.revoke(token_response.access_token)
            except StravaProviderError:
                pass
            raise
        initial_sync_requested = self._request_initial_sync is not None
        if self._request_initial_sync is not None:
            await self._request_initial_sync(user_id)
        return OAuthCompletion(
            user_id=user_id,
            strava_athlete_id=token_response.athlete.id,
            accepted_scopes=accepted,
            initial_sync_requested=initial_sync_requested,
        )

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("The OAuth clock must return an aware timestamp.")
        return now.astimezone(UTC)
