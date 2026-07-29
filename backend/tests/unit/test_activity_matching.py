"""Deterministic cross-source matching and metric-precedence tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.db.models import Activity
from app.domain.enums import (
    ActivitySource,
    Discipline,
    HeartRateSource,
    HeartRateTemporalQuality,
)
from app.repositories.activities import (
    ActivityImportData,
    activities_are_compatible,
    find_unambiguous_cross_source_match,
    merge_activity_non_destructively,
    should_replace_metric,
)

START = datetime(2026, 7, 29, 6, 30, tzinfo=UTC)


def activity(
    *,
    source: ActivitySource = ActivitySource.APPLE_HEALTH,
    started_at: datetime = START,
    duration_seconds: int = 3600,
    distance_meters: float | None = 10_000,
) -> Activity:
    return Activity(
        id=uuid4(),
        user_id=uuid4(),
        source=source,
        external_id=f"{source.value.lower()}-{uuid4()}",
        sport=Discipline.RUN,
        source_sport_type="Running",
        name="Run",
        started_at=started_at,
        ended_at=started_at + timedelta(seconds=duration_seconds),
        timezone="UTC",
        duration_seconds=duration_seconds,
        moving_time_seconds=None,
        distance_meters=distance_meters,
        elevation_gain_meters=None,
        calories_kcal=None,
        average_heart_rate=None,
        average_heart_rate_source=HeartRateSource.UNAVAILABLE,
        max_heart_rate=None,
        heart_rate_sample_count=0,
        heart_rate_quality=HeartRateTemporalQuality.UNKNOWN,
        heart_rate_reliable=False,
        average_cadence=None,
        route_points=None,
        average_speed=None,
        average_watts=None,
        trainer=False,
        commute=False,
        manual=False,
        raw_summary=None,
        deleted_at=None,
    )


def incoming(
    *,
    source: ActivitySource = ActivitySource.TCX,
    started_at: datetime = START,
    duration_seconds: int = 3600,
    distance_meters: float | None = 10_000,
) -> ActivityImportData:
    return ActivityImportData(
        source=source,
        external_id=f"{source.value.lower()}-source-key",
        sport=Discipline.RUN,
        source_sport_type="Running",
        name="TCX run",
        started_at=started_at,
        ended_at=started_at + timedelta(seconds=duration_seconds),
        duration_seconds=duration_seconds,
        distance_meters=distance_meters,
    )


def test_match_requires_compatible_sport_time_duration_and_distance() -> None:
    candidate = activity()

    assert activities_are_compatible(candidate, incoming())
    assert activities_are_compatible(
        candidate,
        incoming(distance_meters=None),
    )
    assert not activities_are_compatible(
        candidate,
        incoming(started_at=START + timedelta(minutes=6)),
    )
    assert not activities_are_compatible(
        candidate,
        incoming(duration_seconds=4200),
    )
    assert not activities_are_compatible(
        candidate,
        incoming(distance_meters=12_000),
    )
    other_sport = replace(incoming(), sport=Discipline.RIDE)
    assert not activities_are_compatible(candidate, other_sport)


def test_only_one_other_source_candidate_is_merged() -> None:
    first = activity()
    same_source = activity(source=ActivitySource.TCX)

    matched, kind = find_unambiguous_cross_source_match(
        [first, same_source],
        incoming(),
    )

    assert matched is first
    assert kind == "cross_source"

    second = activity(started_at=START + timedelta(seconds=30))
    matched, kind = find_unambiguous_cross_source_match(
        [first, second],
        incoming(),
    )
    assert matched is None
    assert kind == "ambiguous"


def test_metric_precedence_never_uses_null_or_lower_quality() -> None:
    assert should_replace_metric(
        existing_value=148,
        incoming_value=151,
        existing_quality=HeartRateSource.USER_REPORTED,
        incoming_quality=HeartRateSource.MEASURED_SENSOR,
    )
    assert not should_replace_metric(
        existing_value=151,
        incoming_value=148,
        existing_quality=HeartRateSource.MEASURED_SENSOR,
        incoming_quality=HeartRateSource.USER_REPORTED,
    )
    assert not should_replace_metric(
        existing_value=151,
        incoming_value=None,
        existing_quality=HeartRateSource.MEASURED_SENSOR,
        incoming_quality=HeartRateSource.MEASURED_SENSOR,
    )


def test_metric_precedence_includes_reliability() -> None:
    assert should_replace_metric(
        existing_value=148,
        incoming_value=151,
        existing_quality=HeartRateSource.MEASURED_SENSOR,
        incoming_quality=HeartRateSource.PROVIDER_SUMMARY,
        existing_reliable=False,
        incoming_reliable=True,
    )
    assert not should_replace_metric(
        existing_value=151,
        incoming_value=148,
        existing_quality=HeartRateSource.MEASURED_SENSOR,
        incoming_quality=HeartRateSource.MEASURED_SENSOR,
        existing_reliable=True,
        incoming_reliable=False,
    )

    canonical = activity()
    canonical.average_heart_rate = 151
    canonical.average_heart_rate_source = HeartRateSource.MEASURED_SENSOR
    canonical.heart_rate_quality = HeartRateTemporalQuality.EXACT_SAMPLE
    canonical.heart_rate_reliable = True
    unreliable_replay = replace(
        incoming(source=ActivitySource.APPLE_HEALTH),
        average_heart_rate=148,
        average_heart_rate_source=HeartRateSource.MEASURED_SENSOR,
        heart_rate_quality=HeartRateTemporalQuality.UNKNOWN,
        heart_rate_reliable=False,
    )

    merge_activity_non_destructively(
        canonical,
        unreliable_replay,
        same_source=True,
    )

    assert canonical.average_heart_rate == 151
    assert canonical.heart_rate_quality is HeartRateTemporalQuality.EXACT_SAMPLE
    assert canonical.heart_rate_reliable is True


def test_measured_hr_supersedes_manual_without_lower_quality_regression() -> None:
    canonical = activity(distance_meters=None)
    canonical.average_heart_rate = 148
    canonical.average_heart_rate_source = HeartRateSource.USER_REPORTED
    canonical.max_heart_rate = 160
    canonical.heart_rate_quality = HeartRateTemporalQuality.MANUAL
    canonical.heart_rate_reliable = False
    measured = replace(
        incoming(),
        average_heart_rate=151,
        average_heart_rate_source=HeartRateSource.MEASURED_SENSOR,
        max_heart_rate=172,
        heart_rate_sample_count=120,
        heart_rate_quality=HeartRateTemporalQuality.EXACT_SAMPLE,
        heart_rate_reliable=True,
    )

    changed = merge_activity_non_destructively(
        canonical,
        measured,
        same_source=False,
    )

    assert changed
    assert canonical.distance_meters == 10_000
    assert canonical.average_heart_rate == 151
    assert canonical.average_heart_rate_source is HeartRateSource.MEASURED_SENSOR
    assert canonical.max_heart_rate == 172
    assert canonical.heart_rate_sample_count == 120
    assert canonical.heart_rate_reliable is True

    lower_quality = replace(
        incoming(),
        average_heart_rate=149,
        average_heart_rate_source=HeartRateSource.USER_REPORTED,
        heart_rate_quality=HeartRateTemporalQuality.MANUAL,
    )

    merge_activity_non_destructively(
        canonical,
        lower_quality,
        same_source=False,
    )

    assert canonical.average_heart_rate == 151
    assert canonical.average_heart_rate_source is HeartRateSource.MEASURED_SENSOR
    assert canonical.max_heart_rate == 172
