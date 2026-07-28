"""Focused persistence invariants using the portable SQLite model variants."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.dialects.postgresql import JSONB, dialect
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.db.base import Base
from app.db.models import (
    AthleteBaseline,
    BaselinePreference,
    BodyArea,
    CoachPreference,
    DisciplineBaseline,
    EquipmentAccess,
    EquipmentAccessType,
    EquipmentType,
    GoalType,
    HealthConstraint,
    HealthConstraintType,
    OnboardingSession,
)
from app.domain.enums import (
    ActivitySource,
    BaselinePreferenceStatus,
    BaselineSource,
    BaselineStatus,
    CoachTone,
    ConnectionStatus,
    DayOfWeek,
    DetailLevel,
    Discipline,
    GoalPriority,
    LevelLabel,
    OAuthProvider,
    OnboardingStep,
    PrimarySport,
    SyncStatus,
    SyncType,
    WebhookAspectType,
    WebhookObjectType,
    WebhookProcessingStatus,
)
from app.integrations.strava.exceptions import StravaAuthenticationError
from app.repositories import (
    AvailabilityRuleInput,
    BaselineRepository,
    EquipmentAccessInput,
    HealthConstraintInput,
    OnboardingRepository,
    ProfileRepository,
    StravaRepository,
    UserRepository,
)
from app.repositories.errors import OwnedRecordNotFoundError
from app.schemas.strava import StravaTokenResponse
from app.security.encryption import TokenCipher
from app.services.strava.tokens import StravaTokenManager


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def enable_foreign_keys(dbapi_connection: object, _: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(
        engine,
        expire_on_commit=False,
        autoflush=False,
    )
    yield factory
    await engine.dispose()


async def create_user(
    session: AsyncSession,
    *,
    telegram_user_id: int,
) -> uuid.UUID:
    user, created = await UserRepository(session).get_or_create(
        telegram_user_id=telegram_user_id,
        telegram_username=f"athlete_{telegram_user_id}",
        first_name="Athlete",
    )
    assert created
    return user.id


def test_model_metadata_has_every_entity_and_postgresql_jsonb() -> None:
    assert set(Base.metadata.tables) == {
        "activities",
        "athlete_baselines",
        "athlete_profiles",
        "availability_rules",
        "baseline_preferences",
        "coach_preferences",
        "discipline_baselines",
        "equipment_access",
        "health_constraints",
        "llm_usage",
        "oauth_states",
        "onboarding_sessions",
        "strava_connections",
        "strava_sync_jobs",
        "strava_webhook_events",
        "training_goals",
        "users",
    }
    answers_type = OnboardingSession.__table__.c.answers.type.dialect_impl(
        dialect(),
    )
    assert isinstance(answers_type, JSONB)
    active_index = next(
        index
        for index in Base.metadata.tables["strava_sync_jobs"].indexes
        if index.name == "uq_strava_sync_jobs_active_user"
    )
    assert active_index.unique
    assert active_index.dialect_options["postgresql"]["where"] is not None


async def test_onboarding_resumes_and_never_crosses_user_scope(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        first_user_id = await create_user(session, telegram_user_id=101)
        second_user_id = await create_user(session, telegram_user_id=202)
        repository = OnboardingRepository(session)
        onboarding, created = await repository.get_or_create(
            user_id=first_user_id,
        )
        assert created
        await repository.save_progress(
            user_id=first_user_id,
            current_step=OnboardingStep.PRIMARY_SPORT,
            answers={"consent": True},
        )
        await repository.begin_free_text(
            user_id=first_user_id,
            onboarding_step=OnboardingStep.PRIMARY_SPORT,
        )
        onboarding_id = onboarding.id
        await session.commit()

    async with session_factory() as session:
        repository = OnboardingRepository(session)
        resumed = await repository.get_for_user(user_id=first_user_id)
        assert resumed is not None
        assert resumed.current_step is OnboardingStep.PRIMARY_SPORT
        assert resumed.answers == {"consent": True}
        assert resumed.pending_free_text_step is OnboardingStep.PRIMARY_SPORT
        assert resumed.pending_parsed_value is None
        assert (
            await repository.get_for_user(
                user_id=second_user_id,
                session_id=onboarding_id,
            )
            is None
        )
        locked = await repository.lock_for_user(user_id=first_user_id)
        assert locked.id == onboarding_id
        await repository.set_pending_parse(
            user_id=first_user_id,
            onboarding_step=OnboardingStep.PRIMARY_SPORT,
            parsed_value={"normalized_value": "OTHER"},
        )
        assert locked.answers == {"consent": True}


async def test_profile_finalization_is_idempotent_owned_and_cascading(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        first_user_id = await create_user(session, telegram_user_id=303)
        second_user_id = await create_user(session, telegram_user_id=404)
        repository = ProfileRepository(session)
        arguments = {
            "user_id": first_user_id,
            "age": 36,
            "height_cm": 178.0,
            "weight_kg": 72.5,
            "primary_sport": PrimarySport.TRIATHLON,
            "goal_type": GoalType.HALF_IRONMAN_70_3,
            "event_name": "Coastal 70.3",
            "event_date": date(2027, 5, 9),
            "goal_priority": GoalPriority.FINISH_SAFELY,
            "availability": [
                AvailabilityRuleInput(DayOfWeek.MONDAY, 60, False),
                AvailabilityRuleInput(DayOfWeek.SUNDAY, None, True),
            ],
            "equipment": [
                EquipmentAccessInput(
                    EquipmentType.ROAD_BIKE,
                    EquipmentAccessType.REGULAR,
                    (DayOfWeek.SUNDAY,),
                ),
            ],
            "constraints": [
                HealthConstraintInput(
                    BodyArea.KNEE,
                    HealthConstraintType.HISTORICAL,
                    "User-reported historical limitation",
                ),
            ],
            "coach_tone": CoachTone.CONCISE_PRACTICAL,
            "detail_level": DetailLevel.SHORT,
            "baseline_source": BaselineSource.STRAVA,
            "baseline_status": BaselinePreferenceStatus.SELECTED,
        }
        first = await repository.finalize_profile(**arguments)
        second = await repository.finalize_profile(**arguments)
        assert first.athlete_profile is not None
        assert second.athlete_profile is not None
        assert first.athlete_profile.id == second.athlete_profile.id
        assert len(second.availability_rules) == 2
        assert len(second.equipment_access) == 1
        assert len(second.health_constraints) == 1
        limitation = second.health_constraints[0]
        assert limitation.is_historical
        assert not limitation.is_current
        other_user = await repository.get_bundle(user_id=second_user_id)
        assert other_user.athlete_profile is None
        assert other_user.availability_rules == ()

        assert await UserRepository(session).delete(user_id=first_user_id)
        await session.commit()
        for model in (
            EquipmentAccess,
            HealthConstraint,
            CoachPreference,
            BaselinePreference,
        ):
            count = await session.scalar(
                select(func.count()).select_from(model),
            )
            assert count == 0


async def test_strava_atomic_guards_dedup_and_token_erasure(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 7, 28, 8, tzinfo=UTC)
    async with session_factory() as session:
        user_id = await create_user(session, telegram_user_id=505)
        other_user_id = await create_user(session, telegram_user_id=606)
        repository = StravaRepository(session)

        await repository.create_oauth_state(
            user_id=user_id,
            provider=OAuthProvider.STRAVA,
            state_hash="a" * 64,
            expires_at=now + timedelta(minutes=10),
        )
        assert (
            await repository.consume_oauth_state_by_hash(
                provider=OAuthProvider.STRAVA,
                state_hash="a" * 64,
                now=now,
                expected_user_id=other_user_id,
            )
            is None
        )
        consumed = await repository.consume_oauth_state_by_hash(
            provider=OAuthProvider.STRAVA,
            state_hash="a" * 64,
            now=now,
            expected_user_id=user_id,
        )
        assert consumed is not None
        assert (
            await repository.consume_oauth_state_by_hash(
                provider=OAuthProvider.STRAVA,
                state_hash="a" * 64,
                now=now,
            )
            is None
        )

        connection = await repository.upsert_connection(
            user_id=user_id,
            strava_athlete_id=9001,
            accepted_scopes=["read", "activity:read_all"],
            encrypted_access_token="encrypted-access",
            encrypted_refresh_token="encrypted-refresh",
            access_token_expires_at=now + timedelta(hours=6),
            connection_status=ConnectionStatus.CONNECTED,
            disconnected_at=None,
        )
        first_job = await repository.create_sync_job(
            user_id=user_id,
            sync_type=SyncType.INITIAL,
            requested_at=now,
        )
        assert first_job is not None
        assert (
            await repository.create_sync_job(
                user_id=user_id,
                sync_type=SyncType.MANUAL,
                requested_at=now,
            )
            is None
        )
        await repository.update_sync_job(
            user_id=user_id,
            job_id=first_job.id,
            status=SyncStatus.SUCCEEDED,
            completed_at=now,
            imported_count=1,
            updated_count=0,
            skipped_count=0,
            failed_count=0,
        )
        assert (
            await repository.create_sync_job(
                user_id=user_id,
                sync_type=SyncType.MANUAL,
                requested_at=now + timedelta(minutes=1),
            )
            is not None
        )

        activity_values: dict[str, object] = {
            "sport": Discipline.RUN,
            "source_sport_type": "Run",
            "name": "Morning run",
            "started_at": now,
            "timezone": "Europe/Madrid",
            "duration_seconds": 1800,
            "moving_time_seconds": 1700,
            "distance_meters": 5000.0,
            "elevation_gain_meters": 40.0,
            "average_heart_rate": None,
            "max_heart_rate": None,
            "average_speed": 2.94,
            "average_watts": None,
            "trainer": False,
            "commute": False,
            "manual": False,
            "raw_summary": {"id": 77},
        }
        assert (
            await repository.upsert_activity(
                user_id=user_id,
                source=ActivitySource.STRAVA,
                external_id="77",
                values=activity_values,
            )
            == "inserted"
        )
        assert (
            await repository.upsert_activity(
                user_id=user_id,
                source=ActivitySource.STRAVA,
                external_id="77",
                values=activity_values,
            )
            == "unchanged"
        )
        changed_values = {**activity_values, "name": "Renamed run"}
        assert (
            await repository.upsert_activity(
                user_id=user_id,
                source=ActivitySource.STRAVA,
                external_id="77",
                values=changed_values,
            )
            == "updated"
        )
        assert not await repository.mark_activity_deleted(
            user_id=other_user_id,
            source=ActivitySource.STRAVA,
            external_id="77",
            deleted_at=now,
        )

        webhook = await repository.create_webhook_event(
            external_event_key="event-key",
            owner_id=9001,
            object_type=WebhookObjectType.ACTIVITY,
            object_id=77,
            aspect_type=WebhookAspectType.UPDATE,
            event_time=now,
            payload={"object_id": 77},
        )
        assert webhook is not None
        assert webhook.user_id == user_id
        assert (
            await repository.create_webhook_event(
                external_event_key="event-key",
                owner_id=9001,
                object_type=WebhookObjectType.ACTIVITY,
                object_id=77,
                aspect_type=WebhookAspectType.UPDATE,
                event_time=now,
                payload={"object_id": 77},
            )
            is None
        )
        duplicate = await repository.get_webhook_event_by_key(
            external_event_key="event-key",
        )
        assert duplicate is not None
        assert duplicate.id == webhook.id
        assert await repository.claim_webhook_event(
            event_id=webhook.id,
            external_event_key="event-key",
            claimed_at=now,
            stale_before=now - timedelta(minutes=5),
        )
        assert not await repository.claim_webhook_event(
            event_id=webhook.id,
            external_event_key="event-key",
            claimed_at=now + timedelta(minutes=1),
            stale_before=now - timedelta(minutes=4),
        )
        await repository.update_webhook_event(
            event_id=webhook.id,
            external_event_key="event-key",
            status=WebhookProcessingStatus.FAILED,
            processed_at=now + timedelta(minutes=1),
        )
        assert await repository.claim_webhook_event(
            event_id=webhook.id,
            external_event_key="event-key",
            claimed_at=now + timedelta(minutes=2),
            stale_before=now - timedelta(minutes=3),
        )
        await repository.update_webhook_event(
            event_id=webhook.id,
            external_event_key="event-key",
            status=WebhookProcessingStatus.PROCESSING,
            processed_at=now - timedelta(minutes=10),
        )
        assert await repository.claim_webhook_event(
            event_id=webhook.id,
            external_event_key="event-key",
            claimed_at=now + timedelta(minutes=3),
            stale_before=now - timedelta(minutes=5),
        )
        await repository.update_webhook_event(
            event_id=webhook.id,
            external_event_key="event-key",
            status=WebhookProcessingStatus.PROCESSED,
            processed_at=now,
        )

        assert await repository.disconnect_connection(
            user_id=user_id,
            disconnected_at=now,
        )
        disconnected = await repository.get_connection(
            user_id=user_id,
            connection_id=connection.id,
        )
        assert disconnected is not None
        assert disconnected.encrypted_access_token is None
        assert disconnected.encrypted_refresh_token is None
        assert disconnected.connection_status is ConnectionStatus.DISCONNECTED


async def test_token_cas_loser_rereads_committed_winner(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 7, 28, 8, tzinfo=UTC)
    cipher = TokenCipher(TokenCipher.generate_key())
    async with session_factory.begin() as setup_session:
        user_id = await create_user(setup_session, telegram_user_id=616)
        await StravaRepository(setup_session).upsert_connection(
            user_id=user_id,
            strava_athlete_id=9011,
            accepted_scopes=["activity:read"],
            encrypted_access_token=cipher.encrypt("old-access"),
            encrypted_refresh_token=cipher.encrypt("old-refresh"),
            access_token_expires_at=now + timedelta(seconds=30),
            connection_status=ConnectionStatus.CONNECTED,
            disconnected_at=None,
        )

    async with session_factory() as loser_session, session_factory() as winner_session:
        loser_repository = StravaRepository(loser_session)
        winner_repository = StravaRepository(winner_session)
        loser_connection = await loser_repository.get_connection(user_id=user_id)
        winner_connection = await winner_repository.get_connection(user_id=user_id)
        assert loser_connection is not None
        assert winner_connection is not None
        expected_refresh = winner_connection.encrypted_refresh_token
        assert expected_refresh is not None

        class RacingRefreshClient:
            async def refresh_token(self, refresh_token: str) -> StravaTokenResponse:
                assert refresh_token == "old-refresh"
                winner = await winner_repository.rotate_tokens(
                    user_id=user_id,
                    connection_id=winner_connection.id,
                    expected_encrypted_refresh_token=expected_refresh,
                    encrypted_access_token=cipher.encrypt("winner-access"),
                    encrypted_refresh_token=cipher.encrypt("winner-refresh"),
                    access_token_expires_at=now + timedelta(hours=6),
                )
                assert winner is not None
                await winner_session.commit()
                return StravaTokenResponse(
                    access_token="loser-access",
                    refresh_token="loser-refresh",
                    expires_at=int((now + timedelta(hours=6)).timestamp()),
                )

        manager = StravaTokenManager(
            repository=loser_repository,
            client=RacingRefreshClient(),  # type: ignore[arg-type]
            cipher=cipher,
            clock=lambda: now,
        )

        access_token = await manager.access_token(connection=loser_connection)
        refreshed = await loser_repository.get_connection(user_id=user_id)

        assert access_token == "winner-access"
        assert refreshed is not None
        assert refreshed.encrypted_refresh_token is not None
        assert cipher.decrypt(refreshed.encrypted_refresh_token) == "winner-refresh"

        await loser_session.rollback()
        current = await loser_repository.get_connection(user_id=user_id)
        winner_current = await winner_repository.get_connection(user_id=user_id)
        assert current is not None
        assert winner_current is not None
        failure_expected_refresh = winner_current.encrypted_refresh_token
        assert failure_expected_refresh is not None

        class RacingFailureClient:
            async def refresh_token(self, refresh_token: str) -> StravaTokenResponse:
                assert refresh_token == "winner-refresh"
                newest = await winner_repository.rotate_tokens(
                    user_id=user_id,
                    connection_id=winner_current.id,
                    expected_encrypted_refresh_token=failure_expected_refresh,
                    encrypted_access_token=cipher.encrypt("newest-access"),
                    encrypted_refresh_token=cipher.encrypt("newest-refresh"),
                    access_token_expires_at=now + timedelta(hours=7),
                )
                assert newest is not None
                await winner_session.commit()
                raise StravaAuthenticationError()

        failure_manager = StravaTokenManager(
            repository=loser_repository,
            client=RacingFailureClient(),  # type: ignore[arg-type]
            cipher=cipher,
            clock=lambda: now,
        )

        winner_access = await failure_manager.access_token(
            connection=current,
            force_refresh=True,
        )
        newest_connection = await loser_repository.get_connection(user_id=user_id)

        assert winner_access == "newest-access"
        assert newest_connection is not None
        assert newest_connection.connection_status == ConnectionStatus.CONNECTED
        assert newest_connection.encrypted_refresh_token is not None
        assert (
            cipher.decrypt(newest_connection.encrypted_refresh_token)
            == "newest-refresh"
        )


async def test_baseline_versions_and_reads_are_user_owned(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 7, 28, 9, tzinfo=UTC)
    async with session_factory() as session:
        user_id = await create_user(session, telegram_user_id=707)
        other_user_id = await create_user(session, telegram_user_id=808)
        repository = BaselineRepository(session)
        discipline = {
            "discipline": Discipline.RUN,
            "level_label": LevelLabel.DEVELOPING,
            "confidence": 0.65,
            "sessions_count": 8,
            "active_weeks": 4,
            "total_duration_seconds": 14_400,
            "average_weekly_duration_seconds": 3600.0,
            "total_distance_meters": 40_000.0,
            "average_weekly_distance_meters": 10_000.0,
            "longest_session_seconds": 2700,
            "longest_distance_meters": 8000.0,
            "recent_session_count": 3,
            "metrics": {"heuristic_is_provisional": True},
        }
        first = await repository.create(
            user_id=user_id,
            generated_at=now,
            analysis_start=now - timedelta(days=56),
            analysis_end=now,
            source=ActivitySource.STRAVA,
            status=BaselineStatus.READY,
            overall_confidence=0.65,
            disciplines=[discipline],
        )
        second = await repository.create(
            user_id=user_id,
            generated_at=now + timedelta(minutes=1),
            analysis_start=now - timedelta(days=56),
            analysis_end=now,
            source=ActivitySource.STRAVA,
            status=BaselineStatus.READY,
            overall_confidence=0.65,
            disciplines=[discipline],
        )
        assert (first.version, second.version) == (1, 2)
        latest = await repository.get_latest(user_id=user_id)
        assert latest is not None
        assert latest.id == second.id
        assert len(latest.discipline_baselines) == 1
        assert await repository.get_latest(user_id=other_user_id) is None
        assert (
            await repository.get_for_user(
                user_id=other_user_id,
                baseline_id=first.id,
            )
            is None
        )
        baseline_count = await session.scalar(
            select(func.count()).select_from(AthleteBaseline),
        )
        discipline_count = await session.scalar(
            select(func.count()).select_from(DisciplineBaseline),
        )
        assert baseline_count == 2
        assert discipline_count == 2
        with pytest.raises(OwnedRecordNotFoundError):
            await repository.create(
                user_id=uuid.uuid4(),
                generated_at=now,
                analysis_start=now - timedelta(days=56),
                analysis_end=now,
                source=ActivitySource.STRAVA,
                status=BaselineStatus.READY,
                overall_confidence=0.65,
                disciplines=[discipline],
            )
