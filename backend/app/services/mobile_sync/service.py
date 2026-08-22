"""Secure manual iPhone HealthKit sync orchestration for the proof of concept."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.models import MobileSyncCredential
from app.repositories.activities import TrainingActivityRepository
from app.repositories.mobile_sync import MobileSyncCredentialRepository
from app.repositories.users import UserRepository
from app.schemas.common import TelegramIdentity
from app.schemas.mobile_sync import (
    HealthKitWorkoutPayload,
    HealthKitWorkoutSyncResult,
)
from app.services.activities.adapters.healthkit import from_healthkit_workout

_PAIRING_CODE_TTL = timedelta(minutes=10)


class MobileSyncError(RuntimeError):
    """Base class for safe mobile-sync delivery errors."""


class MobileSyncDisabledError(MobileSyncError):
    """Raised when the deliberately opt-in POC is disabled."""


class MobileSyncPairingError(MobileSyncError):
    """Raised for invalid, expired, or already consumed pairing codes."""


class MobileSyncAuthenticationError(MobileSyncError):
    """Raised for missing, revoked, or invalid mobile bearer tokens."""


class MobileSyncIdentityNotFoundError(MobileSyncError):
    """Raised only to the bot when a pairing command has no local athlete."""


@dataclass(frozen=True, slots=True)
class PairingCodeIssue:
    """The one-time code shown by Telegram; never persist or log it in clear."""

    code: str
    expires_at: datetime


class MobileSyncService:
    """Pair an iPhone and ingest minimal HealthKit workouts idempotently."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._clock = clock or (lambda: datetime.now(UTC))

    async def issue_pairing_code(self, identity: TelegramIdentity) -> PairingCodeIssue:
        """Issue a ten-minute single-use code for an existing Telegram athlete."""

        self._require_enabled()
        now = _as_utc(self._clock())
        expires_at = now + _PAIRING_CODE_TTL
        code = _new_pairing_code()
        code_hash = _secret_hash(code)

        async with self._session_factory() as session, session.begin():
            user = await UserRepository(session).get_by_telegram_id(
                identity.telegram_user_id
            )
            if user is None:
                raise MobileSyncIdentityNotFoundError("Mobile pairing is unavailable")
            await MobileSyncCredentialRepository(session).issue_pairing_code(
                user_id=user.id,
                pairing_code_hash=code_hash,
                expires_at=expires_at,
            )
        return PairingCodeIssue(code=code, expires_at=expires_at)

    async def revoke_device(self, identity: TelegramIdentity) -> bool:
        """Revoke the active iPhone token and any pending pairing code."""

        self._require_enabled()
        now = _as_utc(self._clock())
        async with self._session_factory() as session, session.begin():
            user = await UserRepository(session).get_by_telegram_id(
                identity.telegram_user_id
            )
            if user is None:
                return False
            credentials = MobileSyncCredentialRepository(session)
            credential = await credentials.get_for_user(
                user_id=user.id,
                for_update=True,
            )
            if credential is None:
                return False
            was_connected = (
                credential.device_token_hash is not None
                and credential.revoked_at is None
            )
            credential.revoked_at = now
            credential.pairing_code_hash = None
            credential.pairing_code_expires_at = None
            await session.flush()
            return was_connected

    async def pair(
        self,
        *,
        pairing_code: str,
        installation_id: uuid.UUID,
    ) -> str:
        """Exchange one valid code for a newly minted opaque device token."""

        self._require_enabled()
        now = _as_utc(self._clock())
        normalized_code = _normalise_pairing_code(pairing_code)
        code_hash = _secret_hash(normalized_code)
        token = secrets.token_urlsafe(32)
        token_hash = _secret_hash(token)

        async with self._session_factory() as session, session.begin():
            credentials = MobileSyncCredentialRepository(session)
            credential = await credentials.get_for_pairing_code_hash(
                pairing_code_hash=code_hash,
                for_update=True,
            )
            if credential is None or not _is_valid_pairing_code(
                credential=credential,
                supplied_hash=code_hash,
                now=now,
            ):
                raise MobileSyncPairingError("Pairing code is invalid or expired")
            credential.pairing_code_hash = None
            credential.pairing_code_expires_at = None
            credential.device_token_hash = token_hash
            credential.installation_id = str(installation_id)
            credential.revoked_at = None
            credential.last_used_at = now
            await session.flush()
        return token

    async def sync_healthkit_workouts(
        self,
        *,
        access_token: str,
        workouts: Sequence[HealthKitWorkoutPayload],
    ) -> tuple[HealthKitWorkoutSyncResult, ...]:
        """Persist a bounded mobile batch with exact HealthKit UUID identity.

        This intentionally does not invoke the baseline service. A baseline is
        immutable and the weekly planner remains responsible for creating one
        only after its recent-evidence gate passes.
        """

        self._require_enabled()
        token_hash = _secret_hash(access_token)
        now = _as_utc(self._clock())

        async with self._session_factory() as session, session.begin():
            credentials = MobileSyncCredentialRepository(session)
            credential = await credentials.get_for_active_device_token_hash(
                device_token_hash=token_hash,
                for_update=True,
            )
            if credential is None or not _is_valid_device_token(
                credential=credential,
                supplied_hash=token_hash,
            ):
                raise MobileSyncAuthenticationError("Invalid mobile credentials")

            credential.last_used_at = now
            activities = TrainingActivityRepository(session)
            results: list[HealthKitWorkoutSyncResult] = []
            for payload in workouts:
                workout, outcome = await activities.import_activity(
                    user_id=credential.user_id,
                    incoming=from_healthkit_workout(payload),
                )
                results.append(
                    HealthKitWorkoutSyncResult(
                        workout_uuid=payload.workout_uuid,
                        workout_id=workout.id,
                        outcome=outcome,
                    )
                )
            await session.flush()
            return tuple(results)

    def _require_enabled(self) -> None:
        if not self._settings.mobile_sync_enabled:
            raise MobileSyncDisabledError("Mobile sync is disabled")


def _new_pairing_code() -> str:
    """Return a display-friendly 16-character Base32 secret with 80 bits entropy."""

    return base64.b32encode(secrets.token_bytes(10)).decode("ascii").rstrip("=")


def _normalise_pairing_code(value: str) -> str:
    """Tolerate visual separators without weakening the generated-code format."""

    return "".join(character for character in value.upper() if character.isalnum())


def _secret_hash(value: str) -> str:
    """Hash high-entropy credentials before persistence or lookup."""

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _is_valid_pairing_code(
    *,
    credential: MobileSyncCredential | None,
    supplied_hash: str,
    now: datetime,
) -> bool:
    return bool(
        credential is not None
        and credential.pairing_code_hash is not None
        and credential.pairing_code_expires_at is not None
        and _stored_as_utc(credential.pairing_code_expires_at) > now
        and hmac.compare_digest(credential.pairing_code_hash, supplied_hash)
    )


def _is_valid_device_token(
    *,
    credential: MobileSyncCredential | None,
    supplied_hash: str,
) -> bool:
    return bool(
        credential is not None
        and credential.device_token_hash is not None
        and credential.revoked_at is None
        and hmac.compare_digest(credential.device_token_hash, supplied_hash)
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Mobile sync clock must return a timezone-aware timestamp")
    return value.astimezone(UTC)


def _stored_as_utc(value: datetime) -> datetime:
    """Normalize SQLite's timezone-less round-trip as UTC in portable tests."""

    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


__all__ = [
    "MobileSyncAuthenticationError",
    "MobileSyncDisabledError",
    "MobileSyncError",
    "MobileSyncIdentityNotFoundError",
    "MobileSyncPairingError",
    "MobileSyncService",
    "PairingCodeIssue",
]
