"""Telegram workout-history Web App endpoint coverage."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime
from urllib.parse import urlencode

import httpx
import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from app.api.main import create_app
from app.config import Settings
from app.db.base import Base
from app.domain.enums import ActivitySource, Discipline, RunningType
from app.repositories.activities import TrainingActivityRepository
from app.repositories.users import UserRepository
from app.schemas.workouts import RunningWorkoutDetailsData, WorkoutCreate


def _init_data(token: str, telegram_user_id: int) -> str:
    values = {
        "auth_date": "1780000000",
        "user": json.dumps(
            {"id": telegram_user_id, "username": "athlete", "first_name": "Athlete"},
            separators=(",", ":"),
        ),
    }
    check_string = "\n".join(f"{key}={values[key]}" for key in sorted(values))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    values["hash"] = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(values)


@pytest.mark.asyncio
async def test_history_web_app_serves_dashboard_and_requires_signed_data() -> None:
    settings = Settings(
        environment="test",
        database_url="sqlite+aiosqlite:///:memory:",
        telegram_bot_token="history-test-token",
    )
    engine = create_async_engine(settings.database_url)
    application = create_app(settings, engine=engine)
    transport = httpx.ASGITransport(app=application)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        page = await client.get("/webapp/workout-history")
        rejected = await client.post(
            "/webapp/workout-history/data",
            json={
                "init_data": "bad",
                "start_date": "2026-09-01",
                "end_date": "2026-09-01",
            },
        )

    assert page.status_code == 200
    assert "Workout history" in page.text
    assert 'data-days="90"' in page.text
    assert 'data-metric="distance"' in page.text
    assert "Daily totals" in page.text
    assert "Loading workout history" in page.text
    assert "aria-describedby" in page.text
    assert rejected.status_code == 401
    await engine.dispose()


@pytest.mark.asyncio
async def test_history_data_returns_only_signed_athletes_workouts() -> None:
    token = "history-test-token"
    settings = Settings(
        environment="test",
        database_url="sqlite+aiosqlite:///:memory:",
        telegram_bot_token=token,
    )
    engine = create_async_engine(settings.database_url)
    application = create_app(settings, engine=engine)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with application.state.session_factory.begin() as session:
        owner, _ = await UserRepository(session).get_or_create(
            telegram_user_id=33,
            telegram_username="owner",
            first_name="Owner",
            timezone="Europe/Madrid",
        )
        other, _ = await UserRepository(session).get_or_create(
            telegram_user_id=34,
            telegram_username="other",
            first_name="Other",
        )
        repository = TrainingActivityRepository(session)
        for user, title in ((owner, "Owned run"), (other, "Other run")):
            await repository.create_manual(
                WorkoutCreate(
                    athlete_id=user.id,
                    discipline=Discipline.RUNNING,
                    started_at=datetime(2026, 9, 1, 8, tzinfo=UTC),
                    duration_seconds=1800,
                    source=ActivitySource.MANUAL,
                    title=title,
                    details=RunningWorkoutDetailsData(
                        running_type=RunningType.OUTDOOR,
                        distance_meters=5000,
                        moving_duration_seconds=1800,
                    ),
                )
            )
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/webapp/workout-history/data",
            json={
                "init_data": _init_data(token, 33),
                "start_date": "2026-09-01",
                "end_date": "2026-09-01",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["totals"]["session_count"] == 1
    assert [item["title"] for item in payload["workouts"]] == ["Owned run"]
    assert payload["workouts"][0]["distance_meters"] == 5000
    await engine.dispose()
