"""Initial and manual Strava activity synchronization."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.domain.enums import ConnectionStatus, SyncStatus, SyncType
from app.integrations.strava.client import StravaClient
from app.integrations.strava.exceptions import (
    StravaAuthenticationError,
    StravaProviderError,
    StravaRateLimitedError,
)
from app.schemas.strava import StravaSyncStats
from app.services.strava.exceptions import (
    ConcurrentSyncError,
    StravaNotConnectedError,
    SyncCooldownError,
)
from app.services.strava.protocols import (
    BaselineRecalculator,
    StravaConnectionRecord,
    StravaRepositoryProtocol,
)
from app.services.strava.tokens import StravaTokenManager


@dataclass(frozen=True, slots=True)
class SyncOutcome:
    """Safe service result suitable for state-aware UI rendering."""

    job_id: UUID
    status: SyncStatus
    stats: StravaSyncStats
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class _ImportOutcome:
    status: SyncStatus
    error_code: str | None = None


class StravaSyncService:
    """Synchronize activity summaries with cutoff, quota, and concurrency guards."""

    def __init__(
        self,
        *,
        repository: StravaRepositoryProtocol,
        client: StravaClient,
        token_manager: StravaTokenManager,
        baseline: BaselineRecalculator,
        initial_sync_days: int = 56,
        page_size: int = 100,
        manual_cooldown: timedelta = timedelta(minutes=5),
        after_job_claimed: Callable[[], Awaitable[None]] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if initial_sync_days < 1:
            raise ValueError("initial_sync_days must be positive.")
        if not 1 <= page_size <= 200:
            raise ValueError("page_size must be in [1, 200].")
        self._repository = repository
        self._client = client
        self._token_manager = token_manager
        self._baseline = baseline
        self._initial_sync_days = initial_sync_days
        self._page_size = page_size
        self._manual_cooldown = manual_cooldown
        self._after_job_claimed = after_job_claimed
        self._clock = clock or (lambda: datetime.now(UTC))

    async def initial_sync(self, *, user_id: UUID) -> SyncOutcome:
        """Run the configured historical import after OAuth completion."""

        return await self.sync(user_id=user_id, sync_type=SyncType.INITIAL)

    async def manual_sync(self, *, user_id: UUID) -> SyncOutcome:
        """Run a user-requested sync while enforcing the local cooldown."""

        return await self.sync(user_id=user_id, sync_type=SyncType.MANUAL)

    async def preflight(
        self,
        *,
        user_id: UUID,
        sync_type: SyncType,
    ) -> StravaConnectionRecord:
        """Validate connection and cooldown before changing visible lifecycle."""

        return await self._validate_request(
            user_id=user_id,
            sync_type=sync_type,
            requested_at=self._now(),
        )

    async def sync(
        self,
        *,
        user_id: UUID,
        sync_type: SyncType,
    ) -> SyncOutcome:
        """Claim, import, finalize counters, and refresh the baseline."""

        requested_at = self._now()
        connection = await self._validate_request(
            user_id=user_id,
            sync_type=sync_type,
            requested_at=requested_at,
        )
        job = await self._repository.create_sync_job(
            user_id=user_id,
            sync_type=sync_type,
            requested_at=requested_at,
        )
        if job is None:
            raise ConcurrentSyncError()
        claimed_job = await self._repository.claim_sync_job(
            user_id=user_id,
            job_id=job.id,
            started_at=requested_at,
        )
        if claimed_job is None:
            raise ConcurrentSyncError()
        if self._after_job_claimed is not None:
            await self._after_job_claimed()
        cutoff = requested_at - timedelta(days=self._initial_sync_days)
        stats = StravaSyncStats()
        try:
            outcome = await self._import_pages(
                user_id=user_id,
                cutoff=cutoff,
                before=requested_at,
                stats=stats,
            )
            if outcome.status == SyncStatus.SUCCEEDED:
                await self._repository.mark_sync_succeeded(
                    user_id=user_id,
                    connection_id=connection.id,
                    synced_at=self._now(),
                )
            if (
                outcome.status == SyncStatus.SUCCEEDED
                or stats.imported_count
                or stats.updated_count
            ):
                await self._baseline.recalculate(user_id=user_id)
            await self._finalize_job(
                user_id=user_id,
                job_id=job.id,
                status=outcome.status,
                stats=stats,
                error_code=outcome.error_code,
            )
            return SyncOutcome(
                job_id=job.id,
                status=outcome.status,
                stats=stats,
                error_code=outcome.error_code,
            )
        except Exception:
            await self._finalize_job(
                user_id=user_id,
                job_id=job.id,
                status=SyncStatus.FAILED,
                stats=stats,
                error_code="strava_sync_internal_failure",
            )
            raise

    async def _validate_request(
        self,
        *,
        user_id: UUID,
        sync_type: SyncType,
        requested_at: datetime,
    ) -> StravaConnectionRecord:
        connection = await self._repository.get_connection(user_id=user_id)
        if (
            connection is None
            or connection.connection_status != ConnectionStatus.CONNECTED
        ):
            raise StravaNotConnectedError()
        if (
            sync_type == SyncType.MANUAL
            and connection.last_successful_sync_at is not None
        ):
            retry_at = (
                self._persisted_utc(connection.last_successful_sync_at)
                + self._manual_cooldown
            )
            if retry_at > requested_at:
                raise SyncCooldownError(retry_at)
        return connection

    async def _import_pages(
        self,
        *,
        user_id: UUID,
        cutoff: datetime,
        before: datetime,
        stats: StravaSyncStats,
    ) -> _ImportOutcome:
        page_number = 1
        retried_authentication = False
        while True:
            connection = await self._repository.get_connection(user_id=user_id)
            if (
                connection is None
                or connection.connection_status != ConnectionStatus.CONNECTED
            ):
                raise StravaNotConnectedError()
            access_token = await self._token_manager.access_token(
                connection=connection,
            )
            try:
                page = await self._client.get_activities_page(
                    access_token=access_token,
                    after=int(cutoff.timestamp()),
                    before=int(before.timestamp()),
                    page=page_number,
                    per_page=self._page_size,
                )
            except StravaAuthenticationError as exc:
                if retried_authentication:
                    stats.failed_count += 1
                    return self._failure_outcome(
                        stats=stats,
                        error_code="strava_authentication_failed",
                    )
                latest_connection = await self._repository.get_connection(
                    user_id=user_id
                )
                if latest_connection is None:
                    raise StravaNotConnectedError() from exc
                await self._token_manager.access_token(
                    connection=latest_connection,
                    force_refresh=True,
                )
                retried_authentication = True
                continue
            except StravaRateLimitedError as exc:
                stats.rate_limited = True
                stats.rate_limits = exc.rate_limits
                return _ImportOutcome(
                    status=SyncStatus.RATE_LIMITED,
                    error_code=exc.error_code,
                )
            except StravaProviderError as exc:
                stats.failed_count += 1
                return self._failure_outcome(
                    stats=stats,
                    error_code=exc.error_code,
                )
            if not page.activities:
                return _ImportOutcome(status=SyncStatus.SUCCEEDED)
            stats.pages_fetched += 1
            stats.rate_limits = page.rate_limits
            cutoff_reached = False
            for summary in page.activities:
                normalized = summary.normalized()
                if normalized.started_at < cutoff:
                    stats.stopped_at_cutoff = True
                    cutoff_reached = True
                    break
                values = normalized.model_dump(
                    mode="python",
                    exclude={"source", "external_id"},
                )
                result = await self._repository.upsert_activity(
                    user_id=user_id,
                    source=normalized.source,
                    external_id=normalized.external_id,
                    values=values,
                )
                if result == "inserted":
                    stats.imported_count += 1
                elif result == "updated":
                    stats.updated_count += 1
                else:
                    stats.skipped_count += 1
            if cutoff_reached:
                return _ImportOutcome(status=SyncStatus.SUCCEEDED)
            if page.rate_limits.is_near_limit():
                stats.rate_limited = True
                return _ImportOutcome(
                    status=SyncStatus.RATE_LIMITED,
                    error_code="strava_rate_limit_near",
                )
            page_number += 1

    @staticmethod
    def _failure_outcome(
        *,
        stats: StravaSyncStats,
        error_code: str,
    ) -> _ImportOutcome:
        made_progress = (
            stats.pages_fetched > 0
            or stats.imported_count > 0
            or stats.updated_count > 0
        )
        return _ImportOutcome(
            status=SyncStatus.PARTIAL if made_progress else SyncStatus.FAILED,
            error_code=error_code,
        )

    async def _finalize_job(
        self,
        *,
        user_id: UUID,
        job_id: UUID,
        status: SyncStatus,
        stats: StravaSyncStats,
        error_code: str | None,
    ) -> None:
        await self._repository.update_sync_job(
            user_id=user_id,
            job_id=job_id,
            status=status,
            completed_at=self._now(),
            imported_count=stats.imported_count,
            updated_count=stats.updated_count,
            skipped_count=stats.skipped_count,
            failed_count=stats.failed_count,
            error_code=error_code,
            error_message_safe=(
                "Strava synchronization did not complete."
                if error_code is not None
                else None
            ),
        )

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("The sync clock must return an aware timestamp.")
        return now.astimezone(UTC)

    @staticmethod
    def _persisted_utc(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
