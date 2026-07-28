"""Confirmed local and provider Strava disconnect."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from app.integrations.strava.client import StravaClient
from app.integrations.strava.exceptions import StravaProviderError
from app.security.encryption import EncryptionError, TokenCipher
from app.services.strava.exceptions import (
    DisconnectConfirmationRequiredError,
    StravaNotConnectedError,
)
from app.services.strava.protocols import StravaRepositoryProtocol


@dataclass(frozen=True, slots=True)
class DisconnectOutcome:
    """Safe consent-aware disconnect result."""

    provider_revoked: bool
    local_tokens_erased: bool
    imported_history_preserved: bool = True


class StravaDisconnectService:
    """Attempt revocation, then always erase local credentials after confirmation."""

    def __init__(
        self,
        *,
        repository: StravaRepositoryProtocol,
        client: StravaClient,
        cipher: TokenCipher,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._client = client
        self._cipher = cipher
        self._clock = clock or (lambda: datetime.now(UTC))

    async def disconnect(
        self,
        *,
        user_id: UUID,
        confirmed: bool,
    ) -> DisconnectOutcome:
        """Disconnect only after an explicit second-step confirmation."""

        if not confirmed:
            raise DisconnectConfirmationRequiredError()
        connection = await self._repository.get_connection(user_id=user_id)
        if connection is None:
            raise StravaNotConnectedError()
        provider_revoked = False
        encrypted_access = connection.encrypted_access_token
        if encrypted_access:
            try:
                await self._client.revoke(self._cipher.decrypt(encrypted_access))
                provider_revoked = True
            except (EncryptionError, StravaProviderError):
                # Local erasure must still complete when the provider is unavailable.
                provider_revoked = False
        erased = await self._repository.disconnect_connection(
            user_id=user_id,
            disconnected_at=self._now(),
        )
        return DisconnectOutcome(
            provider_revoked=provider_revoked,
            local_tokens_erased=erased,
        )

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("The disconnect clock must return an aware timestamp.")
        return now.astimezone(UTC)
