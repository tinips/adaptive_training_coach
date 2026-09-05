"""Field-coverage tests for the screenshot-to-canonical-import mapping."""

from __future__ import annotations

from datetime import UTC, datetime

from app.schemas.manual_import import ManualWorkoutImportRequest
from app.services.activities.adapters.manual_screenshot import from_manual_screenshot


def test_adapter_maps_cycling_power_speed_and_cadence() -> None:
    payload = ManualWorkoutImportRequest(
        discipline="CYCLING",
        source_app_name="Wahoo",
        started_at=datetime(2026, 9, 5, 8, tzinfo=UTC),
        duration_seconds=3600,
        distance_meters=30_000,
        average_speed_kph=28.5,
        max_speed_kph=42.0,
        average_power_watts=185.0,
        max_power_watts=310.0,
        average_cadence=88.0,
        max_cadence=105.0,
    )

    incoming = from_manual_screenshot(payload)

    assert incoming.average_speed_kph == 28.5
    assert incoming.max_speed_kph == 42.0
    assert incoming.average_power_watts == 185.0
    assert incoming.max_power_watts == 310.0
    assert incoming.average_cadence == 88.0
    assert incoming.max_cadence == 105.0


def test_adapter_maps_running_pace() -> None:
    payload = ManualWorkoutImportRequest(
        discipline="RUNNING",
        source_app_name="Strava",
        started_at=datetime(2026, 9, 5, 8, tzinfo=UTC),
        duration_seconds=1800,
        distance_meters=5000,
        average_pace_seconds_per_km=330,
    )

    incoming = from_manual_screenshot(payload)

    assert incoming.average_pace_seconds_per_km == 330


def test_adapter_leaves_unshown_fields_null() -> None:
    payload = ManualWorkoutImportRequest(
        discipline="CYCLING",
        source_app_name="Wahoo",
        started_at=datetime(2026, 9, 5, 8, tzinfo=UTC),
        duration_seconds=3600,
    )

    incoming = from_manual_screenshot(payload)

    assert incoming.average_power_watts is None
    assert incoming.max_power_watts is None
    assert incoming.average_speed_kph is None
    assert incoming.average_cadence is None
