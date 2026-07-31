"""Persistence primitives for OAuth, Strava imports, and webhook inboxes."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import and_, exists, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.db.base import utc_now
from app.db.models import (
    ActivitySourceLink,
    BaselinePreference,
    OAuthState,
    StravaConnection,
    StravaSyncJob,
    StravaWebhookEvent,
    Workout,
)
from app.domain.enums import (
    ActivitySource,
    BaselinePreferenceStatus,
    BaselineSource,
    ConnectionStatus,
    Discipline,
    OAuthProvider,
    SyncStatus,
    SyncType,
    WebhookAspectType,
    WebhookObjectType,
    WebhookProcessingStatus,
)
from app.repositories.activities import TrainingActivityRepository
from app.repositories.errors import ExternalIdentityConflictError
from app.schemas.strava import NormalizedStravaActivity
from app.schemas.workouts import WorkoutMetricsProjection, workout_metrics
from app.services.activities.contracts import ActivityUpsertOutcome


class StravaRepository:
    """Atomic Strava persistence while leaving commit/rollback to services."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_oauth_state(
        self,
        *,
        user_id: uuid.UUID,
        provider: OAuthProvider,
        state_hash: str,
        expires_at: datetime,
    ) -> OAuthState:
        state = OAuthState(
            user_id=user_id,
            provider=provider,
            state_hash=state_hash,
            expires_at=expires_at,
        )
        self._session.add(state)
        await self._session.flush()
        return state

    async def consume_oauth_state_by_hash(
        self,
        *,
        provider: OAuthProvider,
        state_hash: str,
        now: datetime,
        expected_user_id: uuid.UUID | None = None,
    ) -> OAuthState | None:
        """Atomically consume the high-entropy callback bearer credential."""

        statement = (
            update(OAuthState)
            .where(
                OAuthState.provider == provider,
                OAuthState.state_hash == state_hash,
                OAuthState.consumed_at.is_(None),
                OAuthState.expires_at > now,
            )
            .values(consumed_at=now)
            .returning(OAuthState)
        )
        if expected_user_id is not None:
            statement = statement.where(
                OAuthState.user_id == expected_user_id,
            )
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def get_connection(
        self,
        *,
        user_id: uuid.UUID,
        connection_id: uuid.UUID | None = None,
    ) -> StravaConnection | None:
        """Load a connection only inside an authenticated owner scope."""

        statement = (
            select(StravaConnection)
            .where(StravaConnection.user_id == user_id)
            .execution_options(populate_existing=True)
        )
        if connection_id is not None:
            statement = statement.where(StravaConnection.id == connection_id)
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def get_connection_by_athlete_id(
        self,
        *,
        strava_athlete_id: int,
    ) -> StravaConnection | None:
        """Resolve a signed provider event before any user-owned mutation."""

        statement = select(StravaConnection).where(
            StravaConnection.strava_athlete_id == strava_athlete_id,
        )
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def upsert_connection(
        self,
        *,
        user_id: uuid.UUID,
        strava_athlete_id: int,
        accepted_scopes: Sequence[str],
        encrypted_access_token: str,
        encrypted_refresh_token: str,
        access_token_expires_at: datetime,
        connection_status: ConnectionStatus = ConnectionStatus.CONNECTED,
        disconnected_at: datetime | None = None,
    ) -> StravaConnection:
        """Insert or refresh the one user-owned Strava connection."""

        connection = await self.get_connection(user_id=user_id)
        if connection is None:
            connection = StravaConnection(
                user_id=user_id,
                strava_athlete_id=strava_athlete_id,
                accepted_scopes=list(accepted_scopes),
                encrypted_access_token=encrypted_access_token,
                encrypted_refresh_token=encrypted_refresh_token,
                access_token_expires_at=access_token_expires_at,
                connection_status=connection_status,
                disconnected_at=disconnected_at,
            )
            try:
                async with self._session.begin_nested():
                    self._session.add(connection)
                    await self._session.flush()
            except IntegrityError as error:
                owned = await self.get_connection(user_id=user_id)
                if owned is None or owned.strava_athlete_id != strava_athlete_id:
                    raise ExternalIdentityConflictError(
                        "Strava athlete identity is already connected",
                    ) from error
                connection = owned
        elif connection.strava_athlete_id != strava_athlete_id:
            raise ExternalIdentityConflictError(
                "user already has a different Strava athlete identity",
            )

        connection.accepted_scopes = list(accepted_scopes)
        connection.encrypted_access_token = encrypted_access_token
        connection.encrypted_refresh_token = encrypted_refresh_token
        connection.access_token_expires_at = access_token_expires_at
        connection.connection_status = connection_status
        connection.disconnected_at = disconnected_at
        await self._session.flush()
        return connection

    async def rotate_tokens(
        self,
        *,
        user_id: uuid.UUID,
        connection_id: uuid.UUID,
        expected_encrypted_refresh_token: str,
        encrypted_access_token: str,
        encrypted_refresh_token: str,
        access_token_expires_at: datetime,
    ) -> StravaConnection | None:
        """Compare-and-swap refreshed tokens to prevent stale overwrites."""

        statement = (
            update(StravaConnection)
            .where(
                StravaConnection.id == connection_id,
                StravaConnection.user_id == user_id,
                StravaConnection.encrypted_refresh_token
                == expected_encrypted_refresh_token,
                StravaConnection.connection_status != ConnectionStatus.DISCONNECTED,
            )
            .values(
                encrypted_access_token=encrypted_access_token,
                encrypted_refresh_token=encrypted_refresh_token,
                access_token_expires_at=access_token_expires_at,
                connection_status=ConnectionStatus.CONNECTED,
            )
            .returning(StravaConnection)
        )
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def lock_connection_for_token_refresh(
        self,
        *,
        user_id: uuid.UUID,
        connection_id: uuid.UUID,
    ) -> StravaConnection | None:
        """Serialize provider refresh calls on the owned connection row."""

        statement = (
            select(StravaConnection)
            .where(
                StravaConnection.id == connection_id,
                StravaConnection.user_id == user_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def mark_refresh_failed(
        self,
        *,
        user_id: uuid.UUID,
        connection_id: uuid.UUID,
        expected_encrypted_refresh_token: str,
        occurred_at: datetime,
    ) -> bool:
        result = await self._session.execute(
            update(StravaConnection)
            .where(
                StravaConnection.id == connection_id,
                StravaConnection.user_id == user_id,
                StravaConnection.encrypted_refresh_token
                == expected_encrypted_refresh_token,
                StravaConnection.connection_status == ConnectionStatus.CONNECTED,
            )
            .values(
                connection_status=ConnectionStatus.REFRESH_FAILED,
                updated_at=occurred_at,
            )
            .returning(StravaConnection.id),
        )
        return result.scalar_one_or_none() is not None

    async def mark_sync_succeeded(
        self,
        *,
        user_id: uuid.UUID,
        connection_id: uuid.UUID,
        synced_at: datetime,
    ) -> None:
        await self._session.execute(
            update(StravaConnection)
            .where(
                StravaConnection.id == connection_id,
                StravaConnection.user_id == user_id,
                StravaConnection.connection_status != ConnectionStatus.DISCONNECTED,
            )
            .values(
                last_successful_sync_at=synced_at,
                connection_status=ConnectionStatus.CONNECTED,
            )
        )

    async def disconnect_connection(
        self,
        *,
        user_id: uuid.UUID,
        disconnected_at: datetime,
        connection_id: uuid.UUID | None = None,
    ) -> bool:
        statement = (
            update(StravaConnection)
            .where(StravaConnection.user_id == user_id)
            .values(
                connection_status=ConnectionStatus.DISCONNECTED,
                disconnected_at=disconnected_at,
                encrypted_access_token=None,
                encrypted_refresh_token=None,
            )
            .returning(StravaConnection.id)
        )
        if connection_id is not None:
            statement = statement.where(StravaConnection.id == connection_id)
        result = await self._session.execute(statement)
        return result.scalar_one_or_none() is not None

    async def create_sync_job(
        self,
        *,
        user_id: uuid.UUID,
        sync_type: SyncType,
        requested_at: datetime | None = None,
    ) -> StravaSyncJob | None:
        """Use the partial unique index as the concurrency arbiter."""

        job = StravaSyncJob(
            user_id=user_id,
            sync_type=sync_type,
            status=SyncStatus.REQUESTED,
            requested_at=requested_at or utc_now(),
        )
        try:
            async with self._session.begin_nested():
                self._session.add(job)
                await self._session.flush()
        except IntegrityError:
            return None
        return job

    async def claim_sync_job(
        self,
        *,
        user_id: uuid.UUID,
        job_id: uuid.UUID,
        started_at: datetime | None = None,
    ) -> StravaSyncJob | None:
        statement = (
            update(StravaSyncJob)
            .where(
                StravaSyncJob.id == job_id,
                StravaSyncJob.user_id == user_id,
                StravaSyncJob.status == SyncStatus.REQUESTED,
            )
            .values(
                status=SyncStatus.RUNNING,
                started_at=started_at or utc_now(),
            )
            .returning(StravaSyncJob)
        )
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def update_sync_job(
        self,
        *,
        user_id: uuid.UUID,
        job_id: uuid.UUID,
        status: SyncStatus,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
        imported_count: int | None = None,
        updated_count: int | None = None,
        skipped_count: int | None = None,
        failed_count: int | None = None,
        error_code: str | None = None,
        error_message_safe: str | None = None,
    ) -> None:
        """Finalize an owned sync with safe counters and error metadata."""

        values: dict[str, object] = {
            "status": status,
            "error_code": error_code,
            "error_message_safe": error_message_safe,
        }
        optional_values: dict[str, object | None] = {
            "started_at": started_at,
            "completed_at": completed_at,
            "imported_count": imported_count,
            "updated_count": updated_count,
            "skipped_count": skipped_count,
            "failed_count": failed_count,
        }
        values.update(
            {key: value for key, value in optional_values.items() if value is not None},
        )
        if (
            status not in (SyncStatus.REQUESTED, SyncStatus.RUNNING)
            and completed_at is None
        ):
            values["completed_at"] = utc_now()
        await self._session.execute(
            update(StravaSyncJob)
            .where(
                StravaSyncJob.id == job_id,
                StravaSyncJob.user_id == user_id,
            )
            .values(**values)
        )

    async def get_active_sync_job(
        self,
        *,
        user_id: uuid.UUID,
    ) -> StravaSyncJob | None:
        statement = (
            select(StravaSyncJob)
            .where(
                StravaSyncJob.user_id == user_id,
                StravaSyncJob.status.in_(
                    (SyncStatus.REQUESTED, SyncStatus.RUNNING),
                ),
            )
            .order_by(StravaSyncJob.requested_at.desc())
        )
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def get_latest_sync_job(
        self,
        *,
        user_id: uuid.UUID,
    ) -> StravaSyncJob | None:
        statement = (
            select(StravaSyncJob)
            .where(StravaSyncJob.user_id == user_id)
            .order_by(StravaSyncJob.requested_at.desc())
            .limit(1)
        )
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def list_stale_sync_user_ids(
        self,
        *,
        stale_before: datetime,
    ) -> tuple[uuid.UUID, ...]:
        """List owners whose active sync lease expired for startup recovery."""

        statement = (
            select(StravaSyncJob.user_id)
            .where(self._stale_sync_condition(stale_before=stale_before))
            .distinct()
        )
        result = await self._session.scalars(statement)
        return tuple(result.all())

    async def fail_stale_sync_jobs(
        self,
        *,
        user_id: uuid.UUID,
        stale_before: datetime,
        completed_at: datetime,
    ) -> int:
        """Release only one owner's expired active sync jobs."""

        statement = (
            update(StravaSyncJob)
            .where(
                StravaSyncJob.user_id == user_id,
                self._stale_sync_condition(stale_before=stale_before),
            )
            .values(
                status=SyncStatus.FAILED,
                completed_at=completed_at,
                error_code="strava_sync_worker_expired",
                error_message_safe="Strava synchronization did not complete.",
            )
            .returning(StravaSyncJob.id)
        )
        result = await self._session.scalars(statement)
        return len(result.all())

    async def list_pending_initial_sync_user_ids(
        self,
        *,
        limit: int,
    ) -> tuple[uuid.UUID, ...]:
        """Find connected owners whose durable OAuth handoff has no initial job."""

        if limit < 1:
            raise ValueError("limit must be positive")
        initial_job_exists = (
            select(StravaSyncJob.id)
            .where(
                StravaSyncJob.user_id == StravaConnection.user_id,
                StravaSyncJob.sync_type == SyncType.INITIAL,
            )
            .exists()
        )
        statement = (
            select(StravaConnection.user_id)
            .join(
                BaselinePreference,
                BaselinePreference.user_id == StravaConnection.user_id,
            )
            .where(
                StravaConnection.connection_status == ConnectionStatus.CONNECTED,
                BaselinePreference.selected_source == BaselineSource.STRAVA,
                BaselinePreference.status == BaselinePreferenceStatus.PENDING,
                ~initial_job_exists,
            )
            .order_by(StravaConnection.created_at, StravaConnection.user_id)
            .limit(limit)
        )
        result = await self._session.scalars(statement)
        return tuple(result.all())

    async def upsert_activity(
        self,
        *,
        user_id: uuid.UUID,
        source: ActivitySource,
        external_id: str,
        values: dict[str, object],
    ) -> ActivityUpsertOutcome:
        """Route a Strava record through central workout normalization."""

        provider_values = dict(values)
        provider_values.pop("deleted_at", None)
        normalized = NormalizedStravaActivity(
            source=source,
            external_id=str(external_id),
            **provider_values,
        )
        _workout, outcome = await TrainingActivityRepository(
            self._session
        ).import_strava_activity(
            user_id=user_id,
            normalized=normalized,
        )
        return outcome

    async def list_activities(
        self,
        *,
        user_id: uuid.UUID,
        started_at_or_after: datetime | None = None,
        started_at_or_before: datetime | None = None,
        disciplines: Sequence[Discipline] | None = None,
        include_deleted: bool = False,
    ) -> tuple[WorkoutMetricsProjection, ...]:
        active_source_exists = exists(
            select(ActivitySourceLink.id).where(
                ActivitySourceLink.workout_id == Workout.id,
                ActivitySourceLink.user_id == user_id,
                ActivitySourceLink.deleted_at.is_(None),
            )
        )
        statement = select(Workout).where(Workout.athlete_id == user_id)
        if started_at_or_after is not None:
            statement = statement.where(
                Workout.started_at >= started_at_or_after,
            )
        if started_at_or_before is not None:
            statement = statement.where(
                Workout.started_at <= started_at_or_before,
            )
        if disciplines is not None:
            statement = statement.where(Workout.discipline.in_(disciplines))
        if not include_deleted:
            statement = statement.where(
                or_(
                    Workout.source == ActivitySource.MANUAL,
                    active_source_exists,
                )
            )
        statement = statement.order_by(Workout.started_at, Workout.id)
        result = await self._session.scalars(statement)
        return tuple(workout_metrics(item) for item in result.unique().all())

    async def mark_activity_deleted(
        self,
        *,
        user_id: uuid.UUID,
        source: ActivitySource,
        external_id: str | int,
        deleted_at: datetime | None = None,
    ) -> bool:
        return await TrainingActivityRepository(self._session).mark_source_deleted(
            user_id=user_id,
            source=source,
            external_id=str(external_id),
            deleted_at=deleted_at or utc_now(),
        )

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
    ) -> StravaWebhookEvent | None:
        """Persist the inbox item once; return ``None`` for a duplicate key."""

        connection = await self.get_connection_by_athlete_id(
            strava_athlete_id=owner_id,
        )
        event = StravaWebhookEvent(
            user_id=connection.user_id if connection is not None else None,
            external_event_key=external_event_key,
            owner_id=owner_id,
            object_type=object_type,
            object_id=object_id,
            aspect_type=aspect_type,
            event_time=event_time,
            payload=dict(payload),
        )
        try:
            async with self._session.begin_nested():
                self._session.add(event)
                await self._session.flush()
        except IntegrityError:
            return None
        return event

    async def get_webhook_event_by_key(
        self,
        *,
        external_event_key: str,
    ) -> StravaWebhookEvent | None:
        """Resolve an internal provider inbox item by its canonical event key."""

        statement = select(StravaWebhookEvent).where(
            StravaWebhookEvent.external_event_key == external_event_key,
        )
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def get_webhook_event(
        self,
        *,
        event_id: uuid.UUID,
        external_event_key: str,
    ) -> StravaWebhookEvent | None:
        """Load an inbox record only through its opaque ID and canonical key."""

        statement = select(StravaWebhookEvent).where(
            StravaWebhookEvent.id == event_id,
            StravaWebhookEvent.external_event_key == external_event_key,
        )
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def list_recoverable_webhook_events(
        self,
        *,
        stale_before: datetime,
        limit: int,
    ) -> tuple[StravaWebhookEvent, ...]:
        """Return a bounded startup batch; atomic claim remains the arbiter."""

        if limit < 1:
            raise ValueError("limit must be positive")
        statement = (
            select(StravaWebhookEvent)
            .where(
                or_(
                    StravaWebhookEvent.processing_status.in_(
                        (
                            WebhookProcessingStatus.PENDING,
                            WebhookProcessingStatus.FAILED,
                        )
                    ),
                    and_(
                        StravaWebhookEvent.processing_status
                        == WebhookProcessingStatus.PROCESSING,
                        or_(
                            StravaWebhookEvent.processed_at <= stale_before,
                            and_(
                                StravaWebhookEvent.processed_at.is_(None),
                                StravaWebhookEvent.created_at <= stale_before,
                            ),
                        ),
                    ),
                ),
            )
            .order_by(StravaWebhookEvent.created_at, StravaWebhookEvent.id)
            .limit(limit)
        )
        result = await self._session.scalars(statement)
        return tuple(result.all())

    async def claim_webhook_event(
        self,
        *,
        event_id: uuid.UUID,
        external_event_key: str,
        claimed_at: datetime,
        stale_before: datetime,
    ) -> bool:
        """Claim pending/failed work or reclaim an expired processing lease."""

        statement = (
            update(StravaWebhookEvent)
            .where(
                StravaWebhookEvent.id == event_id,
                StravaWebhookEvent.external_event_key == external_event_key,
                or_(
                    StravaWebhookEvent.processing_status.in_(
                        (
                            WebhookProcessingStatus.PENDING,
                            WebhookProcessingStatus.FAILED,
                        )
                    ),
                    and_(
                        StravaWebhookEvent.processing_status
                        == WebhookProcessingStatus.PROCESSING,
                        or_(
                            StravaWebhookEvent.processed_at <= stale_before,
                            and_(
                                StravaWebhookEvent.processed_at.is_(None),
                                StravaWebhookEvent.created_at <= stale_before,
                            ),
                        ),
                    ),
                ),
            )
            .values(
                processing_status=WebhookProcessingStatus.PROCESSING,
                processed_at=claimed_at,
            )
            .returning(StravaWebhookEvent.id)
            .execution_options(synchronize_session=False)
        )
        result = await self._session.execute(statement)
        return result.scalar_one_or_none() is not None

    async def update_webhook_event(
        self,
        *,
        event_id: uuid.UUID,
        external_event_key: str,
        status: WebhookProcessingStatus,
        processed_at: datetime,
    ) -> bool:
        statement = (
            update(StravaWebhookEvent)
            .where(
                StravaWebhookEvent.id == event_id,
                StravaWebhookEvent.external_event_key == external_event_key,
            )
            .values(
                processing_status=status,
                processed_at=processed_at,
            )
            .returning(StravaWebhookEvent.id)
        )
        result = await self._session.execute(statement)
        return result.scalar_one_or_none() is not None

    @staticmethod
    def _stale_sync_condition(
        *,
        stale_before: datetime,
    ) -> ColumnElement[bool]:
        return or_(
            and_(
                StravaSyncJob.status == SyncStatus.REQUESTED,
                StravaSyncJob.requested_at <= stale_before,
            ),
            and_(
                StravaSyncJob.status == SyncStatus.RUNNING,
                or_(
                    StravaSyncJob.started_at <= stale_before,
                    and_(
                        StravaSyncJob.started_at.is_(None),
                        StravaSyncJob.requested_at <= stale_before,
                    ),
                ),
            ),
        )
