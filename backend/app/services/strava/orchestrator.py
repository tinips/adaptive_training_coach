"""Session-owning orchestration for Strava and deterministic baselines."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.domain.enums import (
    BaselinePreferenceStatus,
    BaselineSource,
    OAuthProvider,
    SyncStatus,
    SyncType,
    UserStatus,
    WebhookProcessingStatus,
)
from app.integrations.strava.client import StravaClient
from app.integrations.strava.exceptions import StravaProviderError
from app.repositories.baselines import BaselineRepository
from app.repositories.profiles import ProfileRepository
from app.repositories.strava import StravaRepository
from app.repositories.users import UserRepository
from app.schemas.baseline import BaselineCalculation
from app.schemas.strava import StravaWebhookEvent
from app.security.encryption import EncryptionError, TokenCipher
from app.security.oauth_state import digest_oauth_state, new_oauth_state
from app.services.baseline.service import BaselineService
from app.services.strava.disconnect import DisconnectOutcome, StravaDisconnectService
from app.services.strava.exceptions import (
    ConcurrentSyncError,
    DisconnectConfirmationRequiredError,
    StravaNotConnectedError,
    StravaServiceError,
    SyncCooldownError,
)
from app.services.strava.oauth import (
    OAuthCompletion,
    OAuthInitiation,
    StravaOAuthService,
)
from app.services.strava.sync import StravaSyncService, SyncOutcome
from app.services.strava.tokens import StravaTokenManager
from app.services.strava.webhook import (
    StravaWebhookService,
    WebhookAcceptance,
    WebhookOutcome,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class StravaRecoveryOutcome:
    """Safe counts from startup reconciliation of abandoned sync work."""

    stale_sync_jobs_failed: int
    lifecycles_reconciled: int
    webhook_events_scheduled: int
    webhook_events_recovered: int
    webhook_events_failed: int
    initial_syncs_scheduled: int
    initial_syncs_recovered: int
    initial_syncs_failed: int


class StravaConfigurationError(StravaServiceError):
    """Required integration configuration is unavailable."""

    def __init__(self) -> None:
        super().__init__(
            "strava_configuration_missing",
            "Strava integration is not configured.",
        )


class ConnectTicketRejectedError(StravaServiceError):
    """The application-level one-time connect ticket was rejected."""

    def __init__(self) -> None:
        super().__init__(
            "strava_connect_ticket_rejected",
            "The Strava connection ticket is invalid or expired.",
        )


class StravaCoordinator:
    """Compose provider services with transaction-scoped SQL repositories."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        client: StravaClient | None = None,
        cipher: TokenCipher | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._client = client
        self._cipher = cipher
        self._owns_client = client is None
        self._clock = clock or (lambda: datetime.now(UTC))
        self._recovery_tasks: set[asyncio.Task[tuple[int, int]]] = set()

    async def aclose(self) -> None:
        """Close the lazily-created provider client during application shutdown."""

        for task in tuple(self._recovery_tasks):
            task.cancel()
        if self._recovery_tasks:
            await asyncio.gather(*self._recovery_tasks, return_exceptions=True)
            self._recovery_tasks.clear()
        if self._owns_client and self._client is not None:
            await self._client.aclose()

    async def issue_connect_url(self, *, user_id: UUID) -> str:
        """Issue an opaque persisted ticket for the application's connect route."""

        self._components()
        ticket = new_oauth_state(
            ttl=self._state_ttl,
            now=self._now(),
        )
        async with self._session_factory.begin() as session:
            await UserRepository(session).require_by_id(user_id)
            await StravaRepository(session).create_oauth_state(
                user_id=user_id,
                provider=OAuthProvider.STRAVA_CONNECT,
                state_hash=ticket.digest,
                expires_at=ticket.expires_at,
            )
        connect_url = (
            f"{self._settings.public_base_url.rstrip('/')}/integrations/strava/connect"
        )
        return str(httpx.URL(connect_url, params={"ticket": ticket.raw}))

    async def begin_oauth(self, *, raw_ticket: str) -> OAuthInitiation:
        """Consume the app ticket and create the separate provider OAuth state."""

        try:
            ticket_hash = digest_oauth_state(raw_ticket)
        except ValueError as exc:
            raise ConnectTicketRejectedError() from exc
        client, cipher = self._components()
        async with self._session_factory.begin() as session:
            repository = StravaRepository(session)
            ticket = await repository.consume_oauth_state_by_hash(
                provider=OAuthProvider.STRAVA_CONNECT,
                state_hash=ticket_hash,
                now=self._now(),
            )
            if ticket is None:
                raise ConnectTicketRejectedError()
            service = StravaOAuthService(
                repository=repository,
                client=client,
                cipher=cipher,
                state_ttl=self._state_ttl,
                clock=self._clock,
            )
            return await service.begin(user_id=ticket.user_id)

    async def complete_oauth(
        self,
        *,
        raw_state: str,
        code: str | None,
        accepted_scope: str | None,
        error: str | None = None,
    ) -> OAuthCompletion:
        """Persist encrypted credentials and mark the athlete ready to import."""

        client, cipher = self._components()
        async with self._session_factory.begin() as session:
            repository = StravaRepository(session)
            user_id = await StravaOAuthService(
                repository=repository,
                client=client,
                cipher=cipher,
                state_ttl=self._state_ttl,
                clock=self._clock,
            ).consume_callback_state(raw_state=raw_state)

        async with self._session_factory.begin() as session:
            repository = StravaRepository(session)
            completion = await StravaOAuthService(
                repository=repository,
                client=client,
                cipher=cipher,
                state_ttl=self._state_ttl,
                clock=self._clock,
            ).complete_consumed(
                user_id=user_id,
                code=code,
                accepted_scope=accepted_scope,
                error=error,
            )
            await self._set_lifecycle(
                session=session,
                user_id=completion.user_id,
                user_status=UserStatus.BASELINE_PENDING,
                preference_status=BaselinePreferenceStatus.PENDING,
            )
            return completion

    async def initial_sync(self, *, user_id: UUID) -> SyncOutcome:
        """Run the post-callback historical import."""

        return await self._run_sync(user_id=user_id, initial=True)

    async def manual_sync(self, *, user_id: UUID) -> SyncOutcome:
        """Run a cooldown-protected user-requested import."""

        return await self._run_sync(user_id=user_id, initial=False)

    async def recover_stale_work(
        self,
        *,
        stale_after: timedelta = timedelta(minutes=30),
        webhook_stale_after: timedelta = timedelta(minutes=5),
        webhook_batch_size: int = 100,
        initial_sync_batch_size: int = 20,
        wait_for_webhooks: bool = False,
        wait_for_initial_syncs: bool = False,
    ) -> StravaRecoveryOutcome:
        """Reconcile abandoned sync jobs and replay a bounded webhook batch."""

        if stale_after <= timedelta(0):
            raise ValueError("stale_after must be positive.")
        if webhook_stale_after <= timedelta(0):
            raise ValueError("webhook_stale_after must be positive.")
        if webhook_batch_size < 1:
            raise ValueError("webhook_batch_size must be positive.")
        if initial_sync_batch_size < 1:
            raise ValueError("initial_sync_batch_size must be positive.")
        now = self._now()
        stale_before = now - stale_after
        failed_count = 0
        reconciled_count = 0
        async with self._session_factory.begin() as session:
            strava = StravaRepository(session)
            baselines = BaselineRepository(session)
            user_ids = await strava.list_stale_sync_user_ids(
                stale_before=stale_before,
            )
            for user_id in user_ids:
                released = await strava.fail_stale_sync_jobs(
                    user_id=user_id,
                    stale_before=stale_before,
                    completed_at=now,
                )
                if released == 0:
                    continue
                failed_count += released
                has_baseline = await baselines.get_latest(user_id=user_id) is not None
                await self._set_lifecycle(
                    session=session,
                    user_id=user_id,
                    user_status=(
                        UserStatus.BASELINE_READY
                        if has_baseline
                        else UserStatus.BASELINE_FAILED
                    ),
                    preference_status=(
                        BaselinePreferenceStatus.READY
                        if has_baseline
                        else BaselinePreferenceStatus.FAILED
                    ),
                )
                reconciled_count += 1
        async with self._session_factory() as session:
            repository = StravaRepository(session)
            recoverable_events = await repository.list_recoverable_webhook_events(
                stale_before=now - webhook_stale_after,
                limit=webhook_batch_size,
            )
            pending_initial_syncs = await repository.list_pending_initial_sync_user_ids(
                limit=initial_sync_batch_size,
            )
            recoverable_event_keys = tuple(
                (event.id, event.external_event_key) for event in recoverable_events
            )
        webhook_scheduled = len(recoverable_event_keys)
        webhook_recovered = 0
        webhook_failed = 0
        if recoverable_event_keys and wait_for_webhooks:
            webhook_recovered, webhook_failed = await self._recover_webhook_batch(
                recoverable_event_keys,
            )
        elif recoverable_event_keys:
            task = asyncio.create_task(
                self._recover_webhook_batch(recoverable_event_keys),
                name="strava-webhook-startup-recovery",
            )
            self._recovery_tasks.add(task)
            task.add_done_callback(self._recovery_tasks.discard)
        initial_syncs_scheduled = len(pending_initial_syncs)
        initial_syncs_recovered = 0
        initial_syncs_failed = 0
        if pending_initial_syncs and wait_for_initial_syncs:
            (
                initial_syncs_recovered,
                initial_syncs_failed,
            ) = await self._recover_initial_sync_batch(pending_initial_syncs)
        elif pending_initial_syncs:
            task = asyncio.create_task(
                self._recover_initial_sync_batch(pending_initial_syncs),
                name="strava-initial-sync-startup-recovery",
            )
            self._recovery_tasks.add(task)
            task.add_done_callback(self._recovery_tasks.discard)
        return StravaRecoveryOutcome(
            stale_sync_jobs_failed=failed_count,
            lifecycles_reconciled=reconciled_count,
            webhook_events_scheduled=webhook_scheduled,
            webhook_events_recovered=webhook_recovered,
            webhook_events_failed=webhook_failed,
            initial_syncs_scheduled=initial_syncs_scheduled,
            initial_syncs_recovered=initial_syncs_recovered,
            initial_syncs_failed=initial_syncs_failed,
        )

    async def _recover_webhook_batch(
        self,
        events: tuple[tuple[UUID, str], ...],
    ) -> tuple[int, int]:
        recovered = 0
        failed = 0
        for event_id, external_event_key in events:
            try:
                outcome = await self.process_webhook(
                    event_id=event_id,
                    external_event_key=external_event_key,
                )
            except Exception:
                failed += 1
                continue
            if outcome.status in {"processed", "ignored"}:
                recovered += 1
            elif outcome.status == "failed":
                failed += 1
        return recovered, failed

    async def _recover_initial_sync_batch(
        self,
        user_ids: tuple[UUID, ...],
    ) -> tuple[int, int]:
        recovered = 0
        failed = 0
        for user_id in user_ids:
            try:
                outcome = await self.initial_sync(user_id=user_id)
            except ConcurrentSyncError:
                continue
            except Exception:
                failed += 1
                continue
            if outcome.status == SyncStatus.FAILED:
                failed += 1
            else:
                recovered += 1
        return recovered, failed

    async def recalculate_baseline(self, *, user_id: UUID) -> BaselineCalculation:
        """Append a deterministic baseline version and update lifecycle state."""

        try:
            async with self._session_factory.begin() as session:
                calculation = await self._baseline_service(session).recalculate(
                    user_id=user_id
                )
                await self._set_lifecycle(
                    session=session,
                    user_id=user_id,
                    user_status=UserStatus.BASELINE_READY,
                    preference_status=BaselinePreferenceStatus.READY,
                )
                return calculation
        except Exception:
            await self._mark_failed(user_id=user_id)
            raise

    async def disconnect(
        self,
        *,
        user_id: UUID,
        confirmed: bool,
    ) -> DisconnectOutcome:
        """Revoke and erase credentials, preserving imported baseline history."""

        if not confirmed:
            raise DisconnectConfirmationRequiredError()
        try:
            client, cipher = self._components()
        except StravaConfigurationError:
            async with self._session_factory.begin() as session:
                repository = StravaRepository(session)
                if await repository.get_connection(user_id=user_id) is None:
                    raise StravaNotConnectedError() from None
                erased = await repository.disconnect_connection(
                    user_id=user_id,
                    disconnected_at=self._now(),
                )
            outcome = DisconnectOutcome(
                provider_revoked=False,
                local_tokens_erased=erased,
            )
        else:
            async with self._session_factory.begin() as session:
                outcome = await StravaDisconnectService(
                    repository=StravaRepository(session),
                    client=client,
                    cipher=cipher,
                    clock=self._clock,
                ).disconnect(user_id=user_id, confirmed=True)
        await self._restore_from_baseline(user_id=user_id)
        return outcome

    async def revoke_for_deletion(self, *, user_id: UUID) -> bool:
        """Best-effort provider revocation without committing local mutations."""

        try:
            client, cipher = self._components()
        except StravaConfigurationError:
            return False
        async with self._session_factory() as session:
            connection = await StravaRepository(session).get_connection(user_id=user_id)
            if connection is None or not connection.encrypted_access_token:
                return False
            encrypted_access_token = connection.encrypted_access_token
        try:
            access_token = cipher.decrypt(encrypted_access_token)
            await client.revoke(access_token)
        except (EncryptionError, StravaProviderError):
            return False
        return True

    def verify_webhook(
        self,
        *,
        mode: str | None,
        verify_token: str | None,
        challenge: str | None,
    ) -> dict[str, str]:
        """Verify the provider challenge with no database or network work."""

        return StravaWebhookService.verify_challenge(
            mode=mode,
            supplied_verify_token=verify_token,
            challenge=challenge,
            configured_verify_token=self._webhook_verify_token,
        )

    async def accept_webhook(
        self,
        *,
        event: StravaWebhookEvent,
    ) -> WebhookAcceptance:
        """Durably persist/deduplicate an event before returning HTTP 200."""

        async with self._session_factory.begin() as session:
            return await self._webhook_service(session).accept(event=event)

    async def process_webhook(
        self,
        *,
        event_id: UUID,
        external_event_key: str,
    ) -> WebhookOutcome:
        """Reload and process an accepted event in an independent transaction."""

        try:
            async with self._session_factory() as session:
                service = self._webhook_service(
                    session,
                    after_event_claimed=session.commit,
                )
                try:
                    outcome = await service.process(
                        event_id=event_id,
                        external_event_key=external_event_key,
                    )
                except Exception:
                    await session.rollback()
                    raise
                await session.commit()
        except Exception as exc:
            await self._fail_webhook_event(
                event_id=event_id,
                external_event_key=external_event_key,
            )
            logger.warning(
                "Strava webhook background processing failed type=%s",
                type(exc).__name__,
            )
            raise
        try:
            if outcome.baseline_recalculated and outcome.user_id is not None:
                await self._mark_ready(user_id=outcome.user_id)
            elif outcome.connection_deauthorized and outcome.user_id is not None:
                await self._restore_from_baseline(user_id=outcome.user_id)
        except Exception as exc:
            logger.warning(
                "Strava webhook lifecycle reconciliation failed type=%s",
                type(exc).__name__,
            )
            raise
        return outcome

    async def _run_sync(
        self,
        *,
        user_id: UUID,
        initial: bool,
    ) -> SyncOutcome:
        client, cipher = self._components()
        sync_type = SyncType.INITIAL if initial else SyncType.MANUAL
        async with self._session_factory() as preflight_session:
            await self._sync_service(
                session=preflight_session,
                client=client,
                cipher=cipher,
            ).preflight(
                user_id=user_id,
                sync_type=sync_type,
            )
        try:
            async with self._session_factory() as session:
                service = self._sync_service(
                    session=session,
                    client=client,
                    cipher=cipher,
                    after_job_claimed=lambda: self._checkpoint_sync_job(
                        session=session,
                        user_id=user_id,
                    ),
                )
                try:
                    outcome = (
                        await service.initial_sync(user_id=user_id)
                        if initial
                        else await service.manual_sync(user_id=user_id)
                    )
                except Exception:
                    await session.commit()
                    raise
                await session.commit()
        except ConcurrentSyncError:
            # The active owner keeps the visible importing lifecycle.
            raise
        except SyncCooldownError:
            await self._restore_from_baseline(user_id=user_id)
            raise
        except Exception:
            await self._fail_active_sync(user_id=user_id)
            await self._restore_after_sync_failure(user_id=user_id)
            raise
        await self._apply_sync_outcome(user_id=user_id, outcome=outcome)
        return outcome

    async def _apply_sync_outcome(
        self,
        *,
        user_id: UUID,
        outcome: SyncOutcome,
    ) -> None:
        del outcome
        if await self._has_baseline(user_id=user_id):
            await self._mark_ready(user_id=user_id)
            return
        await self._mark_failed(user_id=user_id)

    async def _mark_importing(self, *, user_id: UUID) -> None:
        async with self._session_factory.begin() as session:
            await self._set_lifecycle(
                session=session,
                user_id=user_id,
                user_status=UserStatus.BASELINE_IMPORTING,
                preference_status=BaselinePreferenceStatus.IMPORTING,
            )

    async def _checkpoint_sync_job(
        self,
        *,
        session: AsyncSession,
        user_id: UUID,
    ) -> None:
        """Persist the RUNNING lease before exposing IMPORTING lifecycle."""

        await session.commit()
        await self._mark_importing(user_id=user_id)

    async def _mark_ready(self, *, user_id: UUID) -> None:
        async with self._session_factory.begin() as session:
            await self._set_lifecycle(
                session=session,
                user_id=user_id,
                user_status=UserStatus.BASELINE_READY,
                preference_status=BaselinePreferenceStatus.READY,
            )

    async def _mark_failed(self, *, user_id: UUID) -> None:
        async with self._session_factory.begin() as session:
            await self._set_lifecycle(
                session=session,
                user_id=user_id,
                user_status=UserStatus.BASELINE_FAILED,
                preference_status=BaselinePreferenceStatus.FAILED,
            )

    async def _fail_active_sync(self, *, user_id: UUID) -> None:
        """Release a durably claimed job after an unexpected worker failure."""

        async with self._session_factory.begin() as session:
            repository = StravaRepository(session)
            job = await repository.get_active_sync_job(user_id=user_id)
            if job is None:
                return
            await repository.update_sync_job(
                user_id=user_id,
                job_id=job.id,
                status=SyncStatus.FAILED,
                completed_at=self._now(),
                error_code="strava_sync_internal_failure",
                error_message_safe="Strava synchronization did not complete.",
            )

    async def _fail_webhook_event(
        self,
        *,
        event_id: UUID,
        external_event_key: str,
    ) -> None:
        """Make a durably claimed inbox item eligible for safe redelivery."""

        async with self._session_factory.begin() as session:
            repository = StravaRepository(session)
            event = await repository.get_webhook_event(
                event_id=event_id,
                external_event_key=external_event_key,
            )
            if event is None:
                return
            await repository.update_webhook_event(
                event_id=event_id,
                external_event_key=event.external_event_key,
                status=WebhookProcessingStatus.FAILED,
                processed_at=self._now(),
            )

    async def _restore_from_baseline(self, *, user_id: UUID) -> None:
        if await self._has_baseline(user_id=user_id):
            await self._mark_ready(user_id=user_id)
            return
        async with self._session_factory.begin() as session:
            await self._set_lifecycle(
                session=session,
                user_id=user_id,
                user_status=UserStatus.BASELINE_PENDING,
                preference_status=BaselinePreferenceStatus.PENDING,
            )

    async def _restore_after_sync_failure(self, *, user_id: UUID) -> None:
        if await self._has_baseline(user_id=user_id):
            await self._mark_ready(user_id=user_id)
            return
        await self._mark_failed(user_id=user_id)

    async def _has_baseline(self, *, user_id: UUID) -> bool:
        async with self._session_factory() as session:
            return (
                await BaselineRepository(session).get_latest(user_id=user_id)
                is not None
            )

    async def _set_lifecycle(
        self,
        *,
        session: AsyncSession,
        user_id: UUID,
        user_status: UserStatus,
        preference_status: BaselinePreferenceStatus,
    ) -> None:
        await UserRepository(session).update_status(
            user_id=user_id,
            status=user_status,
        )
        await ProfileRepository(session).upsert_baseline_preference(
            user_id=user_id,
            selected_source=BaselineSource.STRAVA,
            status=preference_status,
        )

    def _baseline_service(self, session: AsyncSession) -> BaselineService:
        return BaselineService(
            activities=StravaRepository(session),
            baselines=BaselineRepository(session),
            analysis_days=self._settings.strava_initial_sync_days,
            clock=self._clock,
        )

    def _sync_service(
        self,
        *,
        session: AsyncSession,
        client: StravaClient,
        cipher: TokenCipher,
        after_job_claimed: Callable[[], Awaitable[None]] | None = None,
    ) -> StravaSyncService:
        repository = StravaRepository(session)
        return StravaSyncService(
            repository=repository,
            client=client,
            token_manager=StravaTokenManager(
                repository=repository,
                client=client,
                cipher=cipher,
                after_rotation=session.commit,
                clock=self._clock,
            ),
            baseline=self._baseline_service(session),
            initial_sync_days=self._settings.strava_initial_sync_days,
            page_size=self._settings.strava_sync_page_size,
            manual_cooldown=timedelta(
                seconds=self._settings.strava_sync_cooldown_seconds
            ),
            after_job_claimed=after_job_claimed,
            clock=self._clock,
        )

    def _webhook_service(
        self,
        session: AsyncSession,
        *,
        after_event_claimed: Callable[[], Awaitable[None]] | None = None,
    ) -> StravaWebhookService:
        client, cipher = self._components()
        repository = StravaRepository(session)
        return StravaWebhookService(
            repository=repository,
            client=client,
            token_manager=StravaTokenManager(
                repository=repository,
                client=client,
                cipher=cipher,
                after_rotation=session.commit,
                clock=self._clock,
            ),
            baseline=self._baseline_service(session),
            verify_token=self._webhook_verify_token,
            subscription_id=self._webhook_subscription_id,
            after_event_claimed=after_event_claimed,
            clock=self._clock,
        )

    def _components(self) -> tuple[StravaClient, TokenCipher]:
        if self._cipher is None:
            encryption_key = self._settings.app_encryption_key
            if encryption_key is None:
                raise StravaConfigurationError()
            try:
                self._cipher = TokenCipher(encryption_key)
            except EncryptionError as exc:
                raise StravaConfigurationError() from exc
        if self._client is None:
            client_id = self._settings.strava_client_id
            client_secret = self._settings.strava_client_secret
            if not client_id or client_secret is None:
                raise StravaConfigurationError()
            try:
                self._client = StravaClient(
                    client_id=client_id,
                    client_secret=client_secret,
                    redirect_uri=self._settings.strava_redirect_uri,
                )
            except ValueError as exc:
                raise StravaConfigurationError() from exc
        return self._client, self._cipher

    @property
    def _state_ttl(self) -> timedelta:
        return timedelta(seconds=self._settings.oauth_state_ttl_seconds)

    @property
    def _webhook_verify_token(self) -> str:
        token = self._settings.strava_webhook_verify_token
        if token is None or not token.get_secret_value():
            raise StravaConfigurationError()
        return token.get_secret_value()

    @property
    def _webhook_subscription_id(self) -> int:
        value = self._settings.strava_webhook_subscription_id
        if value is None:
            raise StravaConfigurationError()
        try:
            return int(value)
        except ValueError as exc:
            raise StravaConfigurationError() from exc

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("The Strava coordinator clock must be timezone-aware.")
        return now.astimezone(UTC)
