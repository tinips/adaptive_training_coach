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
from app.domain.enums import Discipline
from app.integrations.llm.vision import DeepSeekWorkoutScreenshotExtractor
from app.repositories.planned_session_links import PlannedSessionLinkRepository
from app.repositories.users import UserRepository
from app.repositories.weekly_plans import WeeklyTrainingPlanRepository
from app.schemas.manual_import import ManualWorkoutImportRequest
from app.schemas.weekly_plans import FirstWeekEnduranceSession
from app.services.activities.adapters.manual_screenshot import from_manual_screenshot
from app.services.activities.normalization import normalize_import
from app.services.weekly_planning.constants import FIRST_WEEK_PLAN_SCHEMA_VERSION
from app.services.workout_screenshot.service import (
    ScreenshotEvaluatorEvidenceRequiredError,
    WorkoutScreenshotHeartRateRequiredError,
    WorkoutScreenshotService,
    WorkoutScreenshotSessionLinkRequiredError,
    _PendingDraft,
)
from app.services.workout_screenshot.validation import require_evaluator_capture_metrics


class _StaticScreenshotExtractor:
    def __init__(self, payload: ManualWorkoutImportRequest) -> None:
        self._payload = payload

    async def extract(
        self, *, image_bytes: bytes, image_media_type: str
    ) -> ManualWorkoutImportRequest:
        return self._payload


def _service_with_draft(
    *,
    average_heart_rate: float | None = None,
    max_heart_rate: float | None = None,
) -> WorkoutScreenshotService:
    service = object.__new__(WorkoutScreenshotService)
    service._settings = Settings(environment="test", screenshot_import_enabled=True)
    service._pending = {
        "draft-token": _PendingDraft(
            telegram_user_id=8172,
            request=ManualWorkoutImportRequest(
                discipline="RUNNING",
                source_app_name="Treadmill",
                started_at=datetime(2026, 8, 31, 8, tzinfo=UTC),
                duration_seconds=1800,
                distance_meters=5_000,
                average_heart_rate=average_heart_rate,
                max_heart_rate=max_heart_rate,
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


@pytest.mark.parametrize(
    ("payload", "missing_fields"),
    [
        (
            ManualWorkoutImportRequest(
                discipline="RUNNING",
                source_app_name="Treadmill",
                started_at=datetime(2026, 8, 31, 8, tzinfo=UTC),
                duration_seconds=1800,
            ),
            ("distance",),
        ),
        (
            ManualWorkoutImportRequest(
                discipline="SWIMMING",
                source_app_name="Pool app",
                started_at=datetime(2026, 8, 31, 8, tzinfo=UTC),
                duration_seconds=1800,
                swimming={"environment": "OPEN_WATER"},
            ),
            ("distance",),
        ),
        (
            ManualWorkoutImportRequest(
                discipline="CYCLING",
                source_app_name="Trainer",
                started_at=datetime(2026, 8, 31, 8, tzinfo=UTC),
                duration_seconds=1800,
            ),
            ("distance", "average power"),
        ),
    ],
)
def test_screenshot_capture_requires_evaluator_metrics(
    payload: ManualWorkoutImportRequest, missing_fields: tuple[str, ...]
) -> None:
    with pytest.raises(ScreenshotEvaluatorEvidenceRequiredError) as error:
        require_evaluator_capture_metrics(payload)

    assert error.value.missing_fields == missing_fields


@pytest.mark.parametrize(
    "payload",
    [
        ManualWorkoutImportRequest(
            discipline="RUNNING",
            source_app_name="Treadmill",
            started_at=datetime(2026, 8, 31, 8, tzinfo=UTC),
            duration_seconds=1800,
            distance_meters=5_000,
        ),
        ManualWorkoutImportRequest(
            discipline="CYCLING",
            source_app_name="Trainer",
            started_at=datetime(2026, 8, 31, 8, tzinfo=UTC),
            duration_seconds=1800,
            distance_meters=15_000,
            average_power_watts=180,
        ),
        ManualWorkoutImportRequest(
            discipline="STRENGTH",
            source_app_name="Gym app",
            started_at=datetime(2026, 8, 31, 8, tzinfo=UTC),
            duration_seconds=1800,
        ),
    ],
)
def test_screenshot_capture_accepts_evaluator_complete_metrics(
    payload: ManualWorkoutImportRequest,
) -> None:
    require_evaluator_capture_metrics(payload)


@pytest.mark.asyncio
async def test_extract_draft_rejects_missing_evaluator_metrics_before_storing_a_draft(
    screenshot_database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = screenshot_database
    async with factory.begin() as session:
        await UserRepository(session).get_or_create(
            telegram_user_id=8172,
            telegram_username="runner",
            first_name="Ada",
        )
    service = WorkoutScreenshotService(
        session_factory=factory,
        settings=Settings(environment="test", screenshot_import_enabled=True),
        extractor=cast(
            DeepSeekWorkoutScreenshotExtractor,
            _StaticScreenshotExtractor(
                ManualWorkoutImportRequest(
                    discipline="CYCLING",
                    source_app_name="Trainer",
                    started_at=datetime(2026, 8, 31, 8, tzinfo=UTC),
                    duration_seconds=1800,
                    distance_meters=15_000,
                )
            ),
        ),
    )

    with pytest.raises(ScreenshotEvaluatorEvidenceRequiredError) as error:
        await service.extract_draft(telegram_user_id=8172, image_bytes=b"image")

    assert error.value.missing_fields == ("average power",)
    assert service._pending == {}


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
    service = _service_with_draft(average_heart_rate=142, max_heart_rate=168)
    service._session_factory = factory
    service._settings = Settings(
        environment="test",
        screenshot_import_enabled=True,
    )
    service._extractor = cast(DeepSeekWorkoutScreenshotExtractor, object())
    async with factory.begin() as session:
        planned = FirstWeekEnduranceSession(
            discipline=Discipline.RUNNING,
            purpose="Build a controlled aerobic baseline.",
            intensity={
                "metric": "PACE_SECONDS_PER_KM",
                "target_range": [330, 360],
                "rpe_range": [3, 4],
                "guidance": "Stay relaxed.",
            },
            objective="Run an easy controlled distance.",
            targets={"duration_minutes": 30, "distance_range_meters": [5000, 6000]},
            execution="Keep the effort comfortable.",
        )
        plan = await WeeklyTrainingPlanRepository(session).create(
            athlete_id=user.id,
            week_start=datetime(2026, 8, 31, tzinfo=UTC).date(),
            plan_jsonb={
                "plan_kind": "FIRST_WEEK_MENU",
                "week_start": "2026-08-31",
                "sessions": [planned.model_dump(mode="json")],
                "guardrails": ["Stop for sharp pain."],
                "logging_instructions": ["Log the workout."],
                "sessions_per_discipline": {"RUNNING": 1},
                "total_minutes_per_discipline": {"RUNNING": 30},
            },
            plan_schema_version=FIRST_WEEK_PLAN_SCHEMA_VERSION,
            validation_jsonb=None,
            evidence_snapshot_jsonb={},
            input_digest="0" * 64,
            prompt_version=1,
            calculation_version=1,
            planner_model=None,
            revision=1,
        )
    await service._load_link_options(service._pending["draft-token"])
    assert (
        service.select_link_option(
            telegram_user_id=8172, token="draft-token", option_index=0
        )
        is not None
    )

    confirmation = await service.confirm(
        telegram_user_id=8172,
        token="draft-token",
    )

    workout, outcome = confirmation.workout, confirmation.outcome
    assert outcome == "inserted"
    assert workout.athlete_id == user.id
    assert workout.discipline.value == "RUNNING"
    assert workout.started_at == datetime(2026, 8, 31, 8, tzinfo=UTC)
    assert workout.duration_seconds == 1800
    assert workout.running_details is not None
    assert workout.running_details.distance_meters == 5_000
    assert workout.running_details.average_heart_rate == 142
    assert workout.running_details.max_heart_rate == 168
    assert workout.running_details.average_pace_seconds_per_km == 360
    assert "draft-token" not in service._pending
    async with factory() as session:
        links = await PlannedSessionLinkRepository(session).get_for_plan(
            plan_id=plan.id
        )
    assert len(links) == 1
    assert links[0].workout_id == workout.id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("average_heart_rate", "max_heart_rate"),
    [
        (None, None),
        (142, None),
        (None, 168),
    ],
)
async def test_confirm_rejects_a_screenshot_draft_without_both_heart_rate_values(
    average_heart_rate: float | None,
    max_heart_rate: float | None,
) -> None:
    service = _service_with_draft(
        average_heart_rate=average_heart_rate,
        max_heart_rate=max_heart_rate,
    )

    with pytest.raises(WorkoutScreenshotHeartRateRequiredError):
        await service.confirm(telegram_user_id=8172, token="draft-token")

    assert "draft-token" in service._pending


@pytest.mark.asyncio
async def test_confirm_rejects_complete_hr_draft_without_explicit_session_link() -> (
    None
):
    service = _service_with_draft(average_heart_rate=142, max_heart_rate=168)

    with pytest.raises(WorkoutScreenshotSessionLinkRequiredError):
        await service.confirm(telegram_user_id=8172, token="draft-token")

    assert "draft-token" in service._pending


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
