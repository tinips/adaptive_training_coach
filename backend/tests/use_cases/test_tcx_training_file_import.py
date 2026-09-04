"""Focused end-to-end coverage for TCX training-file imports."""

from __future__ import annotations

import shutil
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

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
from app.db.models import Workout
from app.domain.enums import (
    ActivitySource,
    TrainingFileFormat,
    UserStatus,
)
from app.repositories.users import UserRepository
from app.schemas.common import TelegramIdentity
from app.schemas.training_import import TelegramDocumentUpload
from app.services.training_import import (
    TrainingFileImportOutcome,
    TrainingFileImportService,
)


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


def identity() -> TelegramIdentity:
    return TelegramIdentity(
        telegram_user_id=9101,
        telegram_username="athlete_9101",
        first_name="Athlete",
        language_code="en",
    )


async def stage_athlete(factory: async_sessionmaker[AsyncSession]) -> None:
    athlete_identity = identity()
    async with factory.begin() as session:
        user, _ = await UserRepository(session).get_or_create(
            telegram_user_id=athlete_identity.telegram_user_id,
            telegram_username=athlete_identity.telegram_username,
            first_name=athlete_identity.first_name,
        )
        user.status = UserStatus.ONBOARDING_COMPLETED


def write_tcx(path: Path) -> Path:
    path.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<TrainingCenterDatabase
 xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2">
  <Activities>
    <Activity Sport="Running">
      <Id>2026-07-28T09:00:00Z</Id>
      <Lap StartTime="2026-07-28T09:00:00Z">
        <TotalTimeSeconds>2400</TotalTimeSeconds>
        <DistanceMeters>7200</DistanceMeters>
        <Calories>250</Calories>
        <Track/>
      </Lap>
    </Activity>
  </Activities>
</TrainingCenterDatabase>""",
        encoding="utf-8",
    )
    return path


@pytest.mark.asyncio
async def test_existing_athlete_tcx_imports(
    persistence: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    tmp_path: Path,
) -> None:
    _, factory = persistence
    await stage_athlete(factory)
    source = write_tcx(tmp_path / "daily.tcx")
    stages: list[str] = []

    async def download(destination: Path) -> None:
        shutil.copyfile(source, destination)

    async def progress(stage: str) -> None:
        stages.append(stage)

    service = TrainingFileImportService(
        session_factory=factory,
        settings=Settings(
            environment="test",
            database_url="sqlite+aiosqlite:///:memory:",
            telegram_bot_username=None,
            tcx_import_enabled=True,
            tcx_import_max_size_mb=25,
        ),
        clock=lambda: datetime(2026, 7, 29, 12, tzinfo=UTC),
    )
    outcome: TrainingFileImportOutcome = await service.process_upload(
        identity=identity(),
        document=TelegramDocumentUpload(
            file_id="file-101",
            file_unique_id="unique-101",
            display_filename=source.name,
            file_size=None,
            update_id=101,
        ),
        download=download,
        progress=progress,
    )

    assert outcome.status.value == "SUCCEEDED"
    assert outcome.file_format is TrainingFileFormat.TCX
    assert outcome.sport == "RUNNING"
    assert outcome.duration_seconds == 2400
    assert outcome.distance_meters == 7200
    assert "saving_activities" in stages
    async with factory() as session:
        workout = await session.get(Workout, outcome.activity_id)
        assert workout is not None
        assert workout.source is ActivitySource.TCX
