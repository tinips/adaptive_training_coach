"""Validation coverage for the extended manual-screenshot import request."""

from __future__ import annotations

from datetime import UTC, datetime

from app.schemas.manual_import import ManualWorkoutImportRequest


def test_manual_import_accepts_optional_pace_power_speed_cadence() -> None:
    request = ManualWorkoutImportRequest(
        discipline="CYCLING",
        source_app_name="Wahoo",
        started_at=datetime(2026, 9, 5, 8, tzinfo=UTC),
        duration_seconds=3600,
        average_speed_kph=28.5,
        max_speed_kph=42.0,
        average_power_watts=185.0,
        max_power_watts=310.0,
        average_cadence=88.0,
        max_cadence=105.0,
    )

    assert request.average_speed_kph == 28.5
    assert request.max_speed_kph == 42.0
    assert request.average_power_watts == 185.0
    assert request.max_power_watts == 310.0
    assert request.average_cadence == 88.0
    assert request.max_cadence == 105.0


def test_manual_import_pace_fields_default_to_none() -> None:
    request = ManualWorkoutImportRequest(
        discipline="RUNNING",
        source_app_name="Strava",
        started_at=datetime(2026, 9, 5, 8, tzinfo=UTC),
        duration_seconds=1800,
    )

    assert request.average_pace_seconds_per_km is None
    assert request.average_pace_seconds_per_100m is None
