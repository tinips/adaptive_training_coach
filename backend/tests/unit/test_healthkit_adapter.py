"""HealthKit POC adapter tests independent of mobile HTTP delivery."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.domain.enums import ActivitySource, Discipline
from app.schemas.mobile_sync import HealthKitWorkoutPayload
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
