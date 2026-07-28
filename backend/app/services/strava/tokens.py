"""Concurrency-safe token refresh and rotation."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from app.domain.enums import ConnectionStatus
from app.integrations.strava.client import StravaClient
from app.integrations.strava.exceptions import StravaProviderError
from app.security.encryption import EncryptionError, TokenCipher
from app.services.strava.exceptions import (
    StravaNotConnectedError,
    StravaTokenRotationError,
)
from app.services.strava.protocols import (
    StravaConnectionRecord,
    StravaRepositoryProtocol,
)


class StravaTokenManager:
    """Decrypt current access or atomically rotate an expiring token pair."""

    def __init__(
        self,
        *,
        repository: StravaRepositoryProtocol,
        client: StravaClient,
        cipher: TokenCipher,
        refresh_margin: timedelta = timedelta(minutes=5),
        after_rotation: Callable[[], Awaitable[None]] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if refresh_margin < timedelta(0):
            raise ValueError("refresh_margin must not be negative.")
        self._repository = repository
        self._client = client
        self._cipher = cipher
        self._refresh_margin = refresh_margin
        self._after_rotation = after_rotation
        self._clock = clock or (lambda: datetime.now(UTC))

    async def access_token(
        self,
        *,
        connection: StravaConnectionRecord,
        force_refresh: bool = False,
    ) -> str:
        """Return a usable access token, refreshing and rotating when needed."""

        if connection.connection_status != ConnectionStatus.CONNECTED:
            raise StravaNotConnectedError()
        now = self._now()
        encrypted_access = connection.encrypted_access_token
        encrypted_refresh = connection.encrypted_refresh_token
        if encrypted_access is None or encrypted_refresh is None:
            raise StravaNotConnectedError()
        expires_at = self._persisted_utc(connection.access_token_expires_at)
        if not force_refresh and expires_at > now + self._refresh_margin:
            try:
                return self._cipher.decrypt(encrypted_access)
            except EncryptionError as exc:
                raise StravaTokenRotationError() from exc
        locked = await self._repository.lock_connection_for_token_refresh(
            user_id=connection.user_id,
            connection_id=connection.id,
        )
        if (
            locked is None
            or locked.connection_status != ConnectionStatus.CONNECTED
            or locked.encrypted_access_token is None
            or locked.encrypted_refresh_token is None
        ):
            raise StravaNotConnectedError()
        locked_access = locked.encrypted_access_token
        locked_refresh = locked.encrypted_refresh_token
        locked_expires_at = self._persisted_utc(locked.access_token_expires_at)
        another_worker_rotated = (
            locked_access != encrypted_access
            or locked_refresh != encrypted_refresh
            or locked_expires_at != expires_at
        )
        if (another_worker_rotated and locked_expires_at > now) or (
            not force_refresh and locked_expires_at > now + self._refresh_margin
        ):
            try:
                usable_access_token = self._cipher.decrypt(locked_access)
            except EncryptionError as exc:
                raise StravaTokenRotationError() from exc
            if self._after_rotation is not None:
                await self._after_rotation()
            return usable_access_token
        try:
            refresh_token = self._cipher.decrypt(locked_refresh)
            refreshed = await self._client.refresh_token(refresh_token)
            rotated = await self._repository.rotate_tokens(
                user_id=locked.user_id,
                connection_id=locked.id,
                expected_encrypted_refresh_token=locked_refresh,
                encrypted_access_token=self._cipher.encrypt(refreshed.access_token),
                encrypted_refresh_token=self._cipher.encrypt(refreshed.refresh_token),
                access_token_expires_at=refreshed.expires_at_datetime,
            )
        except (EncryptionError, StravaProviderError) as exc:
            marked_failed = await self._repository.mark_refresh_failed(
                user_id=locked.user_id,
                connection_id=locked.id,
                expected_encrypted_refresh_token=locked_refresh,
                occurred_at=now,
            )
            if not marked_failed:
                winner = await self._repository.get_connection(
                    user_id=connection.user_id,
                )
                winner_token = self._winner_access_token(
                    connection=winner,
                    replaced_refresh_token=locked_refresh,
                    now=now,
                )
                if winner_token is not None:
                    if self._after_rotation is not None:
                        await self._after_rotation()
                    return winner_token
            raise StravaTokenRotationError() from exc
        if rotated is None:
            # Another worker won the compare-and-swap. Use its fresh committed value.
            rotated = await self._repository.get_connection(user_id=connection.user_id)
            if (
                rotated is None
                or rotated.connection_status != ConnectionStatus.CONNECTED
                or rotated.encrypted_access_token is None
                or self._persisted_utc(rotated.access_token_expires_at) <= now
            ):
                raise StravaTokenRotationError()
        if self._after_rotation is not None:
            await self._after_rotation()
        try:
            return self._cipher.decrypt(rotated.encrypted_access_token or "")
        except EncryptionError as exc:
            raise StravaTokenRotationError() from exc

    def _winner_access_token(
        self,
        *,
        connection: StravaConnectionRecord | None,
        replaced_refresh_token: str,
        now: datetime,
    ) -> str | None:
        if (
            connection is None
            or connection.connection_status != ConnectionStatus.CONNECTED
            or connection.encrypted_access_token is None
            or connection.encrypted_refresh_token is None
            or connection.encrypted_refresh_token == replaced_refresh_token
            or self._persisted_utc(connection.access_token_expires_at) <= now
        ):
            return None
        try:
            return self._cipher.decrypt(connection.encrypted_access_token)
        except EncryptionError:
            return None

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise StravaTokenRotationError()
        return now.astimezone(UTC)

    @staticmethod
    def _persisted_utc(value: datetime) -> datetime:
        # PostgreSQL returns aware values. SQLite drops offsets in portable tests;
        # every persisted application timestamp is defined to be UTC.
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
