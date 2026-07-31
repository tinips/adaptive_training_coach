"""Deterministic exact workout identity tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

from app.domain.enums import ActivitySource, Discipline
from app.services.activities.contracts import ActivityImportData
from app.services.activities.normalization import normalize_import

START = datetime(2026, 7, 29, 6, 30, tzinfo=UTC)


def incoming(
    *,
    source: ActivitySource = ActivitySource.TCX,
    external_id: str | None = None,
    started_at: datetime = START,
    duration_seconds: int = 3600,
    distance_meters: float | None = 10_000,
) -> ActivityImportData:
    return ActivityImportData(
        source=source,
        external_id=external_id,
        discipline=Discipline.RUNNING,
        raw_sport="Running",
        title="Imported run",
        started_at=started_at,
        duration_seconds=duration_seconds,
        distance_meters=distance_meters,
    )


def identity(item: ActivityImportData) -> str:
    normalize_import(item)
    assert item.external_id is not None
    return item.external_id


def test_provider_external_id_is_preserved_as_exact_identity() -> None:
    assert identity(incoming(external_id="  provider-42  ")) == "provider-42"


def test_missing_external_id_uses_a_deterministic_normalized_fingerprint() -> None:
    first = incoming()
    equivalent_utc = incoming(
        started_at=START.astimezone(timezone(timedelta(hours=2))),
        distance_meters=10_000.0,
    )

    first_identity = identity(first)

    assert first_identity.startswith("fingerprint:")
    assert identity(equivalent_utc) == first_identity
    assert identity(replace(incoming(), duration_seconds=3601)) != first_identity
    assert identity(replace(incoming(), distance_meters=10_001)) != first_identity
    assert (
        identity(replace(incoming(), source=ActivitySource.APPLE_HEALTH))
        != first_identity
    )
