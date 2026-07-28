"""Structural persistence and application boundaries for Strava services."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from app.domain.enums import (
    ActivitySource,
    ConnectionStatus,
    OAuthProvider,
    SyncStatus,
    SyncType,
    WebhookAspectType,
    WebhookObjectType,
    WebhookProcessingStatus,
)
from app.schemas.baseline import BaselineCalculation


class OAuthStateRecord(Protocol):
    id: UUID
    user_id: UUID
    expires_at: datetime
    consumed_at: datetime | None


class StravaConnectionRecord(Protocol):
    id: UUID
    user_id: UUID
    strava_athlete_id: int
    accepted_scopes: list[str]
    encrypted_access_token: str | None
    encrypted_refresh_token: str | None
    access_token_expires_at: datetime
    connection_status: ConnectionStatus
    last_successful_sync_at: datetime | None


class SyncJobRecord(Protocol):
    id: UUID
    user_id: UUID


class WebhookEventRecord(Protocol):
    id: UUID
    external_event_key: str
    owner_id: int
    object_type: WebhookObjectType
    object_id: int
    aspect_type: WebhookAspectType
    event_time: datetime
    payload: dict[str, object]
    processing_status: WebhookProcessingStatus
    created_at: datetime
    processed_at: datetime | None


class StravaRepositoryProtocol(Protocol):
    """All persistence operations remain explicitly user-scoped where personal."""

    async def create_oauth_state(
        self,
        *,
        user_id: UUID,
        provider: OAuthProvider,
        state_hash: str,
        expires_at: datetime,
    ) -> OAuthStateRecord: ...

    async def consume_oauth_state_by_hash(
        self,
        *,
        provider: OAuthProvider,
        state_hash: str,
        now: datetime,
        expected_user_id: UUID | None = None,
    ) -> OAuthStateRecord | None: ...

    async def get_connection(
        self,
        *,
        user_id: UUID,
    ) -> StravaConnectionRecord | None: ...

    async def get_connection_by_athlete_id(
        self,
        *,
        strava_athlete_id: int,
    ) -> StravaConnectionRecord | None: ...

    async def upsert_connection(
        self,
        *,
        user_id: UUID,
        strava_athlete_id: int,
        accepted_scopes: list[str],
        encrypted_access_token: str,
        encrypted_refresh_token: str,
        access_token_expires_at: datetime,
        connection_status: ConnectionStatus,
        disconnected_at: datetime | None,
    ) -> StravaConnectionRecord: ...

    async def rotate_tokens(
        self,
        *,
        user_id: UUID,
        connection_id: UUID,
        expected_encrypted_refresh_token: str,
        encrypted_access_token: str,
        encrypted_refresh_token: str,
        access_token_expires_at: datetime,
    ) -> StravaConnectionRecord | None: ...

    async def lock_connection_for_token_refresh(
        self,
        *,
        user_id: UUID,
        connection_id: UUID,
    ) -> StravaConnectionRecord | None: ...

    async def mark_refresh_failed(
        self,
        *,
        user_id: UUID,
        connection_id: UUID,
        expected_encrypted_refresh_token: str,
        occurred_at: datetime,
    ) -> bool: ...

    async def mark_sync_succeeded(
        self,
        *,
        user_id: UUID,
        connection_id: UUID,
        synced_at: datetime,
    ) -> None: ...

    async def disconnect_connection(
        self,
        *,
        user_id: UUID,
        disconnected_at: datetime,
    ) -> bool: ...

    async def create_sync_job(
        self,
        *,
        user_id: UUID,
        sync_type: SyncType,
        requested_at: datetime,
    ) -> SyncJobRecord | None: ...

    async def claim_sync_job(
        self,
        *,
        user_id: UUID,
        job_id: UUID,
        started_at: datetime,
    ) -> SyncJobRecord | None: ...

    async def update_sync_job(
        self,
        *,
        user_id: UUID,
        job_id: UUID,
        status: SyncStatus,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
        imported_count: int | None = None,
        updated_count: int | None = None,
        skipped_count: int | None = None,
        failed_count: int | None = None,
        error_code: str | None = None,
        error_message_safe: str | None = None,
    ) -> None: ...

    async def upsert_activity(
        self,
        *,
        user_id: UUID,
        source: ActivitySource,
        external_id: str,
        values: dict[str, object],
    ) -> Literal["inserted", "updated", "unchanged"]: ...

    async def mark_activity_deleted(
        self,
        *,
        user_id: UUID,
        source: ActivitySource,
        external_id: str,
        deleted_at: datetime,
    ) -> bool: ...

    async def create_webhook_event(
        self,
        *,
        external_event_key: str,
        owner_id: int,
        object_type: WebhookObjectType,
        object_id: int,
        aspect_type: WebhookAspectType,
        event_time: datetime,
        payload: dict[str, object],
    ) -> WebhookEventRecord | None: ...

    async def get_webhook_event_by_key(
        self,
        *,
        external_event_key: str,
    ) -> WebhookEventRecord | None: ...

    async def get_webhook_event(
        self,
        *,
        event_id: UUID,
        external_event_key: str,
    ) -> WebhookEventRecord | None: ...

    async def claim_webhook_event(
        self,
        *,
        event_id: UUID,
        external_event_key: str,
        claimed_at: datetime,
        stale_before: datetime,
    ) -> bool: ...

    async def update_webhook_event(
        self,
        *,
        event_id: UUID,
        external_event_key: str,
        status: WebhookProcessingStatus,
        processed_at: datetime,
    ) -> bool: ...


class BaselineRecalculator(Protocol):
    async def recalculate(self, *, user_id: UUID) -> BaselineCalculation: ...


class InitialSyncNotifier(Protocol):
    """Notify the owning product user after a completed initial import."""

    async def notify_initial_sync_succeeded(self, *, user_id: UUID) -> None: ...
