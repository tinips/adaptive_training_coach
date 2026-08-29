"""HealthKit POC adapter tests independent of mobile HTTP delivery."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.domain.enums import ActivitySource, Discipline
from app.schemas.mobile_sync import (
    HealthKitQuantityStatisticsPayload,
    HealthKitRawQuantitySamplePayload,
    HealthKitWorkoutPayload,
)
from app.services.activities.adapters.healthkit import from_healthkit_workout


def _payload(activity_type: str) -> HealthKitWorkoutPayload:
    return HealthKitWorkoutPayload(
        workout_uuid=uuid.UUID("00000000-0000-0000-0000-000000000111"),
        activity_type=activity_type,
        started_at=datetime(2026, 8, 20, 10, tzinfo=UTC),
        ended_at=datetime(2026, 8, 20, 10, 30, tzinfo=UTC),
        duration_seconds=1800,
        distance_meters=5000,
    )


def test_healthkit_adapter_uses_stable_uuid_identity_and_safe_provenance() -> None:
    imported = from_healthkit_workout(_payload("HKWorkoutActivityType.running"))

    assert imported.source is ActivitySource.APPLE_HEALTH
    assert imported.external_id == "healthkit:00000000-0000-0000-0000-000000000111"
    assert imported.discipline is Discipline.RUNNING
    assert imported.source_metadata == {
        "ingestion_channel": "HEALTHKIT_IOS_POC",
        "healthkit_activity_type": "HKWorkoutActivityType.running",
    }


def test_healthkit_adapter_keeps_bare_walking_as_other() -> None:
    imported = from_healthkit_workout(_payload("walking"))

    assert imported.discipline is Discipline.OTHER


def test_healthkit_adapter_retains_all_statistics_and_raw_samples() -> None:
    imported = from_healthkit_workout(
        HealthKitWorkoutPayload(
            workout_uuid=uuid.UUID("00000000-0000-0000-0000-000000000112"),
            activity_type="swimming",
            started_at=datetime(2026, 8, 20, 10, tzinfo=UTC),
            ended_at=datetime(2026, 8, 20, 10, 30, tzinfo=UTC),
            duration_seconds=1800,
            source_name="Mi Fitness",
            all_statistics={
                "HKQuantityTypeIdentifierSwimmingStrokeCount": (
                    HealthKitQuantityStatisticsPayload(sum="750 count")
                )
            },
            raw_quantity_samples=[
                HealthKitRawQuantitySamplePayload(
                    sample_uuid=uuid.UUID("00000000-0000-0000-0000-000000000113"),
                    quantity_type="HKQuantityTypeIdentifierHeartRate",
                    started_at=datetime(2026, 8, 20, 10, 5, tzinfo=UTC),
                    ended_at=datetime(2026, 8, 20, 10, 5, 1, tzinfo=UTC),
                    value="148 count/min",
                    heart_rate_bpm=148,
                    source_name="Mi Fitness",
                    association="time_window_source_match",
                )
            ],
        )
    )

    assert imported.source_metadata == {
        "ingestion_channel": "HEALTHKIT_IOS_POC",
        "healthkit_activity_type": "swimming",
        "healthkit_source_name": "Mi Fitness",
        "healthkit_all_statistics": {
            "HKQuantityTypeIdentifierSwimmingStrokeCount": {"sum": "750 count"}
        },
        "healthkit_raw_quantity_samples": [
            {
                "quantity_type": "HKQuantityTypeIdentifierHeartRate",
                "sample_uuid": "00000000-0000-0000-0000-000000000113",
                "started_at": "2026-08-20T10:05:00Z",
                "ended_at": "2026-08-20T10:05:01Z",
                "value": "148 count/min",
                "heart_rate_bpm": 148.0,
                "source_name": "Mi Fitness",
                "association": "time_window_source_match",
            }
        ],
    }
    assert len(imported.heart_rate_observations) == 1
    assert imported.heart_rate_observations[0].beats_per_minute == 148
