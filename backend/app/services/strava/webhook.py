"""Webhook verification, idempotent ingestion, and domain processing."""

from __future__ import annotations

import hmac
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import SecretStr, ValidationError

from app.domain.enums import (
    ActivitySource,
    WebhookAspectType,
    WebhookObjectType,
    WebhookProcessingStatus,
)
from app.integrations.strava.client import StravaClient
from app.integrations.strava.exceptions import (
    StravaAuthenticationError,
    StravaProviderError,
)
from app.schemas.strava import StravaWebhookEvent
from app.services.strava.exceptions import (
    StravaServiceError,
    WebhookVerificationError,
)
from app.services.strava.protocols import (
    BaselineRecalculator,
    StravaConnectionRecord,
    StravaRepositoryProtocol,
    WebhookEventRecord,
)
from app.services.strava.tokens import StravaTokenManager


@dataclass(frozen=True, slots=True)
class WebhookOutcome:
    """Safe webhook ingestion/processing result."""

    status: Literal["processed", "ignored", "duplicate", "failed"]
    event_id: UUID | None
    baseline_recalculated: bool = False
    user_id: UUID | None = None
    connection_deauthorized: bool = False


@dataclass(frozen=True, slots=True)
class WebhookAcceptance:
    """Fast inbox result that can be processed after the HTTP response."""

    status: Literal["accepted", "ignored", "duplicate"]
    event_id: UUID | None
    external_event_key: str | None = None


class StravaWebhookService:
    """Persist first, deduplicate, resolve owner, then process meaningful changes."""

    def __init__(
        self,
        *,
        repository: StravaRepositoryProtocol,
        client: StravaClient,
        token_manager: StravaTokenManager,
        baseline: BaselineRecalculator,
        verify_token: SecretStr | str,
        subscription_id: int | str | None = None,
        processing_lease: timedelta = timedelta(minutes=5),
        after_event_claimed: Callable[[], Awaitable[None]] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._client = client
        self._token_manager = token_manager
        self._baseline = baseline
        self._verify_token = (
            verify_token.get_secret_value()
            if isinstance(verify_token, SecretStr)
            else verify_token
        )
        if not self._verify_token:
            raise ValueError("A webhook verification token is required.")
        try:
            self._subscription_id = (
                int(subscription_id) if subscription_id is not None else None
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("subscription_id must be an integer.") from exc
        if processing_lease <= timedelta(0):
            raise ValueError("processing_lease must be positive.")
        self._processing_lease = processing_lease
        self._after_event_claimed = after_event_claimed
        self._clock = clock or (lambda: datetime.now(UTC))

    def verify(
        self,
        *,
        mode: str | None,
        verify_token: str | None,
        challenge: str | None,
    ) -> dict[str, str]:
        """Perform Strava's exact subscribe challenge verification."""

        return self.verify_challenge(
            mode=mode,
            supplied_verify_token=verify_token,
            challenge=challenge,
            configured_verify_token=self._verify_token,
        )

    @staticmethod
    def verify_challenge(
        *,
        mode: str | None,
        supplied_verify_token: str | None,
        challenge: str | None,
        configured_verify_token: str,
    ) -> dict[str, str]:
        """Verify a subscribe challenge without constructing persistence services."""

        if (
            mode != "subscribe"
            or supplied_verify_token is None
            or challenge is None
            or not hmac.compare_digest(
                supplied_verify_token,
                configured_verify_token,
            )
        ):
            raise WebhookVerificationError()
        return {"hub.challenge": challenge}

    async def ingest(self, *, event: StravaWebhookEvent) -> WebhookOutcome:
        """Convenience path that accepts then processes one event inline."""

        acceptance = await self.accept(event=event)
        if acceptance.status == "ignored":
            return WebhookOutcome(status="ignored", event_id=acceptance.event_id)
        if acceptance.status == "duplicate":
            return WebhookOutcome(status="duplicate", event_id=acceptance.event_id)
        if acceptance.event_id is None:
            return WebhookOutcome(status="ignored", event_id=None)
        if acceptance.external_event_key is None:
            return WebhookOutcome(status="failed", event_id=acceptance.event_id)
        return await self.process(
            event_id=acceptance.event_id,
            external_event_key=acceptance.external_event_key,
        )

    async def accept(self, *, event: StravaWebhookEvent) -> WebhookAcceptance:
        """Persist and deduplicate quickly, without making provider calls."""

        if (
            self._subscription_id is not None
            and event.subscription_id != self._subscription_id
        ):
            return WebhookAcceptance(status="ignored", event_id=None)
        external_event_key = event.external_event_key()
        record = await self._repository.create_webhook_event(
            external_event_key=external_event_key,
            owner_id=event.owner_id,
            object_type=event.object_type,
            object_id=event.object_id,
            aspect_type=event.aspect_type,
            event_time=event.occurred_at,
            payload=event.model_dump(mode="json"),
        )
        if record is None:
            record = await self._repository.get_webhook_event_by_key(
                external_event_key=external_event_key,
            )
            if record is None:
                return WebhookAcceptance(status="duplicate", event_id=None)
            if not self._is_recoverable(record=record):
                return WebhookAcceptance(
                    status="duplicate",
                    event_id=record.id,
                    external_event_key=external_event_key,
                )
        return WebhookAcceptance(
            status="accepted",
            event_id=record.id,
            external_event_key=external_event_key,
        )

    async def process(
        self,
        *,
        event_id: UUID,
        external_event_key: str,
    ) -> WebhookOutcome:
        """Claim and process only the inbox record's canonical stored payload."""

        record = await self._repository.get_webhook_event(
            event_id=event_id,
            external_event_key=external_event_key,
        )
        if record is None:
            return WebhookOutcome(status="duplicate", event_id=event_id)
        claimed_at = self._now()
        payload = dict(record.payload)
        owner_id = record.owner_id
        object_type = record.object_type
        object_id = record.object_id
        aspect_type = record.aspect_type
        event_time = record.event_time
        claimed = await self._repository.claim_webhook_event(
            event_id=event_id,
            external_event_key=external_event_key,
            claimed_at=claimed_at,
            stale_before=claimed_at - self._processing_lease,
        )
        if not claimed:
            return WebhookOutcome(status="duplicate", event_id=event_id)
        if self._after_event_claimed is not None:
            await self._after_event_claimed()
        event = self._canonical_event(
            payload=payload,
            external_event_key=external_event_key,
            owner_id=owner_id,
            object_type=object_type,
            object_id=object_id,
            aspect_type=aspect_type,
            event_time=event_time,
        )
        if event is None:
            return await self._finish(
                event_id=event_id,
                external_event_key=external_event_key,
                processing_status=WebhookProcessingStatus.FAILED,
                outcome_status="failed",
            )
        return await self._process(
            event_id=event_id,
            external_event_key=external_event_key,
            event=event,
        )

    async def _process(
        self,
        *,
        event_id: UUID,
        external_event_key: str,
        event: StravaWebhookEvent,
    ) -> WebhookOutcome:
        connection = await self._repository.get_connection_by_athlete_id(
            strava_athlete_id=event.owner_id
        )
        if connection is None or connection.strava_athlete_id != event.owner_id:
            return await self._finish(
                event_id=event_id,
                external_event_key=external_event_key,
                processing_status=WebhookProcessingStatus.IGNORED,
                outcome_status="ignored",
            )
        try:
            if event.object_type == WebhookObjectType.ATHLETE:
                if not self._is_deauthorization(event):
                    return await self._finish(
                        event_id=event_id,
                        external_event_key=external_event_key,
                        processing_status=WebhookProcessingStatus.IGNORED,
                        outcome_status="ignored",
                        user_id=connection.user_id,
                    )
                await self._repository.disconnect_connection(
                    user_id=connection.user_id,
                    disconnected_at=self._now(),
                )
                return await self._finish(
                    event_id=event_id,
                    external_event_key=external_event_key,
                    processing_status=WebhookProcessingStatus.PROCESSED,
                    outcome_status="processed",
                    user_id=connection.user_id,
                    connection_deauthorized=True,
                )
            changed = await self._process_activity(
                event=event,
                user_id=connection.user_id,
                connection=connection,
            )
            if changed:
                await self._baseline.recalculate(user_id=connection.user_id)
            return await self._finish(
                event_id=event_id,
                external_event_key=external_event_key,
                processing_status=WebhookProcessingStatus.PROCESSED,
                outcome_status="processed",
                baseline_recalculated=changed,
                user_id=connection.user_id,
            )
        except (StravaProviderError, StravaServiceError):
            return await self._finish(
                event_id=event_id,
                external_event_key=external_event_key,
                processing_status=WebhookProcessingStatus.FAILED,
                outcome_status="failed",
                user_id=connection.user_id,
            )

    async def _process_activity(
        self,
        *,
        event: StravaWebhookEvent,
        user_id: UUID,
        connection: StravaConnectionRecord,
    ) -> bool:
        if event.aspect_type == WebhookAspectType.DELETE:
            return await self._repository.mark_activity_deleted(
                user_id=user_id,
                source=ActivitySource.STRAVA,
                external_id=str(event.object_id),
                deleted_at=self._now(),
            )
        if event.aspect_type not in {
            WebhookAspectType.CREATE,
            WebhookAspectType.UPDATE,
        }:
            return False
        access_token = await self._token_manager.access_token(connection=connection)
        try:
            summary, _rate_limits = await self._client.get_activity(
                access_token=access_token,
                activity_id=event.object_id,
            )
        except StravaAuthenticationError:
            latest_connection = await self._repository.get_connection(user_id=user_id)
            if latest_connection is None:
                return False
            access_token = await self._token_manager.access_token(
                connection=latest_connection,
                force_refresh=True,
            )
            summary, _rate_limits = await self._client.get_activity(
                access_token=access_token,
                activity_id=event.object_id,
            )
        normalized = summary.normalized()
        if normalized.external_id != str(event.object_id):
            return False
        result = await self._repository.upsert_activity(
            user_id=user_id,
            source=normalized.source,
            external_id=normalized.external_id,
            values=normalized.model_dump(
                mode="python",
                exclude={"source", "external_id"},
            ),
        )
        return result != "unchanged"

    async def _finish(
        self,
        *,
        event_id: UUID,
        external_event_key: str,
        processing_status: WebhookProcessingStatus,
        outcome_status: Literal["processed", "ignored", "failed"],
        baseline_recalculated: bool = False,
        user_id: UUID | None = None,
        connection_deauthorized: bool = False,
    ) -> WebhookOutcome:
        updated = await self._repository.update_webhook_event(
            event_id=event_id,
            external_event_key=external_event_key,
            status=processing_status,
            processed_at=self._now(),
        )
        if not updated:
            return WebhookOutcome(
                status="failed",
                event_id=event_id,
                user_id=user_id,
            )
        return WebhookOutcome(
            status=outcome_status,
            event_id=event_id,
            baseline_recalculated=baseline_recalculated,
            user_id=user_id,
            connection_deauthorized=connection_deauthorized,
        )

    def _canonical_event(
        self,
        *,
        payload: dict[str, object],
        external_event_key: str,
        owner_id: int,
        object_type: WebhookObjectType,
        object_id: int,
        aspect_type: WebhookAspectType,
        event_time: datetime,
    ) -> StravaWebhookEvent | None:
        try:
            event = StravaWebhookEvent.model_validate(payload)
        except ValidationError:
            return None
        if (
            event.external_event_key() != external_event_key
            or event.owner_id != owner_id
            or event.object_type != object_type
            or event.object_id != object_id
            or event.aspect_type != aspect_type
            or event.occurred_at != self._as_utc(event_time)
            or (
                self._subscription_id is not None
                and event.subscription_id != self._subscription_id
            )
        ):
            return None
        return event

    @staticmethod
    def _is_deauthorization(event: StravaWebhookEvent) -> bool:
        authorized = event.updates.get("authorized")
        return authorized in {False, "false", "False"}

    def _is_recoverable(self, *, record: WebhookEventRecord) -> bool:
        status = record.processing_status
        if status in {
            WebhookProcessingStatus.PENDING,
            WebhookProcessingStatus.FAILED,
        }:
            return True
        if status != WebhookProcessingStatus.PROCESSING:
            return False
        lease_started = record.processed_at or record.created_at
        return self._as_utc(lease_started) <= self._now() - self._processing_lease

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("The webhook clock must return an aware timestamp.")
        return now.astimezone(UTC)
