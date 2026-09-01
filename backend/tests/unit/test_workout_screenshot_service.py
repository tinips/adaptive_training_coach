"""Tests for transient screenshot workout drafts."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import cast

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import Settings
from app.db.base import Base
from app.integrations.llm.vision import DeepSeekWorkoutScreenshotExtractor
from app.repositories.users import UserRepository
from app.schemas.manual_import import ManualWorkoutImportRequest
from app.services.activities.adapters.manual_screenshot import from_manual_screenshot
from app.services.activities.normalization import normalize_import
from app.services.workout_screenshot.service import (
    WorkoutScreenshotService,
    _PendingDraft,
)


def _service_with_draft() -> WorkoutScreenshotService:
    service = object.__new__(WorkoutScreenshotService)
    service._pending = {
        "draft-token": _PendingDraft(
            telegram_user_id=8172,
            request=ManualWorkoutImportRequest(
                discipline="RUNNING",
                source_app_name="Treadmill",
                started_at=datetime(2026, 8, 31, 8, tzinfo=UTC),
                duration_seconds=1800,
            ),
        )
    }
    return service


@pytest_asyncio.fixture
async def screenshot_database() -> AsyncIterator[
    tuple[AsyncEngine, async_sessionmaker[AsyncSession]]
]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    yield engine, factory
    await engine.dispose()


def test_provide_heart_rate_updates_the_requested_draft() -> None:
    service = _service_with_draft()

    assert service.request_heart_rate(telegram_user_id=8172, token="draft-token")

    draft = service.provide_heart_rate(telegram_user_id=8172, text="142 / 168")

    assert draft is not None
    assert draft.request.average_heart_rate == 142
    assert draft.request.max_heart_rate == 168
    assert service._pending["draft-token"].awaiting_heart_rate is False


@pytest.mark.asyncio
async def test_confirm_persists_a_pending_screenshot_workout(
    screenshot_database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = screenshot_database
    async with factory.begin() as session:
        user, _ = await UserRepository(session).get_or_create(
            telegram_user_id=8172,
            telegram_username="runner",
            first_name="Ada",
        )
    service = _service_with_draft()
    service._session_factory = factory
    service._settings = Settings(
        environment="test",
        screenshot_import_enabled=True,
    )
    service._extractor = cast(DeepSeekWorkoutScreenshotExtractor, object())

    workout, outcome = await service.confirm(
        telegram_user_id=8172,
        token="draft-token",
    )

    assert outcome == "inserted"
    assert workout.athlete_id == user.id
    assert "draft-token" not in service._pending


def test_strength_screenshot_is_normalized_to_a_strength_workout() -> None:
    incoming = from_manual_screenshot(
        ManualWorkoutImportRequest(
            discipline="STRENGTH",
            source_app_name="Gym App",
            started_at=datetime(2026, 8, 31, 8, tzinfo=UTC),
            duration_seconds=2700,
        )
    )

    normalize_import(incoming)

    assert incoming.discipline.value == "STRENGTH"
    assert incoming.strength_type is not None
    assert incoming.strength_type.value == "GYM"


@pytest.mark.parametrize("text", ["142", "168 / 142", "142 / 301"])
def test_provide_heart_rate_rejects_invalid_values(text: str) -> None:
    service = _service_with_draft()
    service.request_heart_rate(telegram_user_id=8172, token="draft-token")

    with pytest.raises(ValueError, match="invalid heart rate"):
        service.provide_heart_rate(telegram_user_id=8172, text=text)

    draft = service._pending["draft-token"]
    assert draft.awaiting_heart_rate is True
    assert draft.request.average_heart_rate is None
    assert draft.request.max_heart_rate is None
