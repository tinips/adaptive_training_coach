"""End-to-end API checks for the manual iPhone HealthKit POC."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.api.main import create_app
from app.config import Settings
from app.db.base import Base
from app.db.models import (
    ActivitySourceLink,
    AthleteBaselineAssessment,
    MobileSyncCredential,
    Workout,
    WorkoutHeartRateObservation,
)
from app.domain.enums import ActivitySource
from app.repositories.users import UserRepository
from app.schemas.common import TelegramIdentity
from app.services.mobile_sync import MobileSyncPairingError, MobileSyncService


@pytest_asyncio.fixture
async def persistence() -> AsyncIterator[
    tuple[AsyncEngine, async_sessionmaker[AsyncSession]]
]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    yield engine, factory
    await engine.dispose()


def _settings(*, mobile_sync_enabled: bool = True) -> Settings:
    return Settings(
        environment="test",
        database_url="sqlite+aiosqlite:///:memory:",
        mobile_sync_enabled=mobile_sync_enabled,
    )


def _identity(telegram_user_id: int = 101) -> TelegramIdentity:
    return TelegramIdentity(
        telegram_user_id=telegram_user_id,
        telegram_username=f"athlete_{telegram_user_id}",
        first_name="Athlete",
    )


async def _stage_user(
    factory: async_sessionmaker[AsyncSession],
    identity: TelegramIdentity,
) -> uuid.UUID:
    async with factory() as session, session.begin():
        user, _ = await UserRepository(session).get_or_create(
            telegram_user_id=identity.telegram_user_id,
            telegram_username=identity.telegram_username,
            first_name=identity.first_name,
            language_code=identity.language_code,
        )
        return user.id


@pytest.mark.asyncio
async def test_pair_and_sync_healthkit_workout_idempotently(
    persistence: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    engine, factory = persistence
    settings = _settings()
    identity = _identity()
    athlete_id = await _stage_user(factory, identity)
    other_athlete_id = await _stage_user(factory, _identity(202))
    pairing = await MobileSyncService(
        session_factory=factory,
        settings=settings,
    ).issue_pairing_code(identity)
    async with factory() as session:
        pending_credential = await session.scalar(
            select(MobileSyncCredential).where(
                MobileSyncCredential.user_id == athlete_id
            )
        )
    assert pending_credential is not None
    assert pending_credential.pairing_code_hash != pairing.code
    assert pending_credential.pairing_code_hash is not None
    assert len(pending_credential.pairing_code_hash) == 64
    application = create_app(settings, engine=engine)
    transport = httpx.ASGITransport(app=application)
    workout_uuid = uuid.uuid4()
    ended_at = datetime.now(UTC).replace(microsecond=0)
    payload = {
        "workouts": [
            {
                "workout_uuid": str(workout_uuid),
                "activity_type": "HKWorkoutActivityType.running",
                "started_at": (ended_at - timedelta(minutes=30)).isoformat(),
                "ended_at": ended_at.isoformat(),
                "duration_seconds": 1800,
                "distance_meters": 5100.0,
                "calories_kcal": 420.0,
                "source_name": "Mi Fitness",
                "all_statistics": {
                    "HKQuantityTypeIdentifierSwimmingStrokeCount": {"sum": "750 count"}
                },
                "raw_quantity_samples": [
                    {
                        "quantity_type": "HKQuantityTypeIdentifierHeartRate",
                        "sample_uuid": "00000000-0000-0000-0000-000000000113",
                        "started_at": (ended_at - timedelta(minutes=25)).isoformat(),
                        "ended_at": (
                            ended_at - timedelta(minutes=25, seconds=-1)
                        ).isoformat(),
                        "value": "148 count/min",
                        "heart_rate_bpm": 148,
                        "source_name": "Mi Fitness",
                        "association": "time_window_source_match",
                    }
                ],
            }
        ]
    }

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        pair_response = await client.post(
            "/v1/mobile/pair",
            json={
                "pairing_code": pairing.code,
                "installation_id": str(uuid.uuid4()),
            },
        )
        assert pair_response.status_code == 200
        token = pair_response.json()["access_token"]

        first = await client.post(
            "/v1/mobile/healthkit/workouts:sync",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )
        second = await client.post(
            "/v1/mobile/healthkit/workouts:sync",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )

    assert first.status_code == 200
    assert first.json()["results"][0]["outcome"] == "inserted"
    assert second.status_code == 200
    assert second.json()["results"][0]["outcome"] == "unchanged"

    async with factory() as session:
        workouts = list(
            await session.scalars(
                select(Workout).where(Workout.athlete_id == athlete_id)
            )
        )
        baselines = list(
            await session.scalars(
                select(AthleteBaselineAssessment).where(
                    AthleteBaselineAssessment.athlete_id == athlete_id
                )
            )
        )
        other_workouts = list(
            await session.scalars(
                select(Workout).where(Workout.athlete_id == other_athlete_id)
            )
        )
        credential = await session.scalar(
            select(MobileSyncCredential).where(
                MobileSyncCredential.user_id == athlete_id
            )
        )
        source_link = await session.scalar(
            select(ActivitySourceLink).where(
                ActivitySourceLink.workout_id == workouts[0].id
            )
        )
        heart_rate_observation = await session.scalar(
            select(WorkoutHeartRateObservation).where(
                WorkoutHeartRateObservation.workout_id == workouts[0].id
            )
        )

    assert len(workouts) == 1
    assert workouts[0].source is ActivitySource.APPLE_HEALTH
    assert workouts[0].external_id == f"healthkit:{workout_uuid}"
    assert baselines == []
    assert other_workouts == []
    assert credential is not None
    assert credential.device_token_hash != token
    assert credential.device_token_hash is not None
    assert len(credential.device_token_hash) == 64
    assert source_link is not None
    assert source_link.source_metadata_jsonb is not None
    assert source_link.source_metadata_jsonb["healthkit_source_name"] == "Mi Fitness"
    assert source_link.source_metadata_jsonb["healthkit_all_statistics"] == {
        "HKQuantityTypeIdentifierSwimmingStrokeCount": {"sum": "750 count"}
    }
    assert source_link.source_metadata_jsonb["healthkit_raw_quantity_samples"] == [
        {
            "quantity_type": "HKQuantityTypeIdentifierHeartRate",
            "sample_uuid": "00000000-0000-0000-0000-000000000113",
            "started_at": (ended_at - timedelta(minutes=25)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            "ended_at": (ended_at - timedelta(minutes=25, seconds=-1)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            "value": "148 count/min",
            "heart_rate_bpm": 148.0,
            "source_name": "Mi Fitness",
            "association": "time_window_source_match",
        }
    ]
    assert heart_rate_observation is not None
    assert heart_rate_observation.beats_per_minute == 148


@pytest.mark.asyncio
async def test_pairing_code_is_single_use_and_revocation_blocks_sync(
    persistence: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    engine, factory = persistence
    settings = _settings()
    identity = _identity()
    await _stage_user(factory, identity)
    service = MobileSyncService(session_factory=factory, settings=settings)
    pairing = await service.issue_pairing_code(identity)
    application = create_app(settings, engine=engine)
    transport = httpx.ASGITransport(app=application)

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        request = {
            "pairing_code": pairing.code,
            "installation_id": str(uuid.uuid4()),
        }
        paired = await client.post("/v1/mobile/pair", json=request)
        reused = await client.post("/v1/mobile/pair", json=request)
        assert paired.status_code == 200
        assert reused.status_code == 401

        assert await service.revoke_device(identity) is True
        response = await client.post(
            "/v1/mobile/healthkit/workouts:sync",
            json={
                "workouts": [
                    {
                        "workout_uuid": str(uuid.uuid4()),
                        "activity_type": "running",
                        "started_at": "2026-08-20T10:00:00+00:00",
                        "ended_at": "2026-08-20T10:30:00+00:00",
                        "duration_seconds": 1800,
                    }
                ]
            },
            headers={"Authorization": f"Bearer {paired.json()['access_token']}"},
        )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid mobile credentials"


@pytest.mark.asyncio
async def test_mobile_routes_are_unavailable_when_feature_is_disabled(
    persistence: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    engine, _factory = persistence
    application = create_app(_settings(mobile_sync_enabled=False), engine=engine)
    transport = httpx.ASGITransport(app=application)

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/v1/mobile/pair",
            json={
                "pairing_code": "ABCDEFGH",
                "installation_id": str(uuid.uuid4()),
            },
        )

    assert response.status_code == 404
    assert response.json()["detail"] == "Mobile sync is not enabled"


@pytest.mark.asyncio
async def test_expired_pairing_code_cannot_issue_a_device_token(
    persistence: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _engine, factory = persistence
    settings = _settings()
    identity = _identity()
    await _stage_user(factory, identity)
    issued_at = datetime(2026, 8, 20, 10, tzinfo=UTC)
    pairing = await MobileSyncService(
        session_factory=factory,
        settings=settings,
        clock=lambda: issued_at,
    ).issue_pairing_code(identity)

    with pytest.raises(MobileSyncPairingError):
        await MobileSyncService(
            session_factory=factory,
            settings=settings,
            clock=lambda: issued_at + timedelta(minutes=10, seconds=1),
        ).pair(
            pairing_code=pairing.code,
            installation_id=uuid.uuid4(),
        )

    async with factory() as session:
        credential = await session.scalar(select(MobileSyncCredential))
    assert credential is not None
    assert credential.device_token_hash is None


@pytest.mark.asyncio
async def test_invalid_sync_payload_creates_no_workout(
    persistence: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    engine, factory = persistence
    settings = _settings()
    identity = _identity()
    athlete_id = await _stage_user(factory, identity)
    pairing = await MobileSyncService(
        session_factory=factory,
        settings=settings,
    ).issue_pairing_code(identity)
    application = create_app(settings, engine=engine)
    transport = httpx.ASGITransport(app=application)

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        paired = await client.post(
            "/v1/mobile/pair",
            json={
                "pairing_code": pairing.code,
                "installation_id": str(uuid.uuid4()),
            },
        )
        malformed = await client.post(
            "/v1/mobile/healthkit/workouts:sync",
            json={
                "workouts": [
                    {
                        "workout_uuid": str(uuid.uuid4()),
                        "activity_type": "running",
                        "started_at": "2026-08-20T10:30:00+00:00",
                        "ended_at": "2026-08-20T10:00:00+00:00",
                        "duration_seconds": 1800,
                    }
                ]
            },
            headers={"Authorization": f"Bearer {paired.json()['access_token']}"},
        )

    assert paired.status_code == 200
    assert malformed.status_code == 422
    async with factory() as session:
        workouts = list(
            await session.scalars(
                select(Workout).where(Workout.athlete_id == athlete_id)
            )
        )
    assert workouts == []
