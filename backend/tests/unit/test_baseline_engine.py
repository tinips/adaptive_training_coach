"""Deterministic baseline metric, confidence, and isolation tests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

import pytest

from app.domain.enums import (
    BaselineStatus,
    Discipline,
    LevelLabel,
)
from app.schemas.baseline import BaselineActivity
from app.services.baseline.engine import BaselineEngine
from app.services.baseline.service import BaselineService

END = datetime(2026, 7, 28, 12, tzinfo=UTC)
START = END - timedelta(days=56)


def activity(
    *,
    discipline: Discipline = Discipline.RUNNING,
    days_ago: float,
    duration_seconds: int = 3600,
    distance_meters: float | None = 10_000,
    heart_rate: float | None = None,
) -> BaselineActivity:
    return BaselineActivity(
        id=uuid4(),
        discipline=discipline,
        started_at=END - timedelta(days=days_ago),
        duration_seconds=duration_seconds,
        distance_meters=distance_meters,
        average_heart_rate=heart_rate,
    )


def result_for(
    activities: list[BaselineActivity],
    discipline: Discipline,
) -> object:
    calculation = BaselineEngine().calculate(
        activities=activities,
        analysis_start=START,
        analysis_end=END,
        generated_at=END,
    )
    return next(
        item for item in calculation.disciplines if item.discipline == discipline
    )


def test_no_activities_produces_unknown_with_explicit_zero_confidence() -> None:
    calculation = BaselineEngine().calculate(
        activities=[],
        analysis_start=START,
        analysis_end=END,
        generated_at=END,
    )

    assert calculation.status == BaselineStatus.INSUFFICIENT_DATA
    assert calculation.overall_confidence == 0
    assert all(
        item.level_label == LevelLabel.UNKNOWN
        and item.confidence == 0
        and item.sessions_count == 0
        for item in calculation.disciplines
    )


def test_one_discipline_does_not_fabricate_metrics_for_another() -> None:
    calculation = BaselineEngine().calculate(
        activities=[activity(days_ago=1)],
        analysis_start=START,
        analysis_end=END,
        generated_at=END,
    )
    run = next(
        item
        for item in calculation.disciplines
        if item.discipline == Discipline.RUNNING
    )
    swim = next(
        item
        for item in calculation.disciplines
        if item.discipline == Discipline.SWIMMING
    )

    assert run.sessions_count == 1
    assert run.level_label == LevelLabel.BEGINNER
    assert swim.sessions_count == 0
    assert swim.level_label == LevelLabel.UNKNOWN


def test_missing_heart_rate_and_zero_distance_strength_are_valid() -> None:
    strength = result_for(
        [
            activity(
                discipline=Discipline.STRENGTH,
                days_ago=2,
                distance_meters=0,
                heart_rate=None,
            )
        ],
        Discipline.STRENGTH,
    )

    assert strength.sessions_count == 1  # type: ignore[attr-defined]
    assert strength.total_duration_seconds == 3600  # type: ignore[attr-defined]
    assert strength.total_distance_meters is None  # type: ignore[attr-defined]
    assert strength.metrics["heart_rate_coverage"] == 0  # type: ignore[attr-defined]


def test_activity_crossing_midnight_keeps_full_duration() -> None:
    crossing = BaselineActivity(
        id=uuid4(),
        discipline=Discipline.RUNNING,
        started_at=datetime(2026, 7, 27, 23, 30, tzinfo=UTC),
        duration_seconds=7200,
        distance_meters=15_000,
    )

    run = result_for([crossing], Discipline.RUNNING)

    assert run.total_duration_seconds == 7200  # type: ignore[attr-defined]
    assert run.active_weeks == 1  # type: ignore[attr-defined]


def test_active_weeks_longest_session_and_recent_count_are_exact() -> None:
    activities = [
        activity(days_ago=1, duration_seconds=1800, distance_meters=5_000),
        activity(days_ago=8, duration_seconds=5400, distance_meters=12_000),
        activity(days_ago=15, duration_seconds=3600, distance_meters=8_000),
    ]

    run = result_for(activities, Discipline.RUNNING)

    assert run.active_weeks == 3  # type: ignore[attr-defined]
    assert run.longest_session_seconds == 5400  # type: ignore[attr-defined]
    assert run.longest_distance_meters == 12_000  # type: ignore[attr-defined]
    assert run.recent_session_count == 2  # type: ignore[attr-defined]


def test_confidence_increases_with_observations_and_stale_data_reduces_it() -> None:
    sparse = [activity(days_ago=1)]
    sufficient_recent = [
        activity(days_ago=days, duration_seconds=5400)
        for days in (1, 3, 8, 10, 15, 17, 22, 24)
    ]
    sufficient_stale = [
        activity(days_ago=days, duration_seconds=5400)
        for days in (30, 32, 37, 39, 44, 46, 51, 53)
    ]

    sparse_result = result_for(sparse, Discipline.RUNNING)
    recent_result = result_for(sufficient_recent, Discipline.RUNNING)
    stale_result = result_for(sufficient_stale, Discipline.RUNNING)

    assert recent_result.confidence > sparse_result.confidence  # type: ignore[attr-defined]
    assert recent_result.confidence > stale_result.confidence  # type: ignore[attr-defined]


def test_central_provisional_thresholds_can_reach_advanced() -> None:
    sustained_volume = [
        activity(
            days_ago=1 + index * 3,
            duration_seconds=3 * 60 * 60,
            distance_meters=25_000,
        )
        for index in range(16)
    ]

    run = result_for(sustained_volume, Discipline.RUNNING)

    assert run.level_label == LevelLabel.ADVANCED  # type: ignore[attr-defined]
    assert run.metrics["heuristic_version"] == "baseline-v1-provisional"  # type: ignore[attr-defined]
    assert run.metrics["heuristic_is_provisional"] is True  # type: ignore[attr-defined]


def test_repeated_calculation_is_deterministic() -> None:
    activities = [
        activity(days_ago=days, duration_seconds=3600 + days)
        for days in (1, 5, 9, 13, 17)
    ]
    engine = BaselineEngine()

    first = engine.calculate(
        activities=activities,
        analysis_start=START,
        analysis_end=END,
        generated_at=END,
    )
    second = engine.calculate(
        activities=reversed(activities),
        analysis_start=START,
        analysis_end=END,
        generated_at=END,
    )

    assert first.model_dump() == second.model_dump()


@dataclass
class WorkoutMetricsRecordFake:
    id: UUID
    athlete_id: UUID
    discipline: Discipline
    started_at: datetime
    duration_seconds: int
    distance_meters: float | None
    average_heart_rate: float | None


class ActivityRepositoryFake:
    def __init__(self, records: list[WorkoutMetricsRecordFake]) -> None:
        self.records = records
        self.queried_users: list[UUID] = []

    async def list_activities(
        self,
        *,
        user_id: UUID,
        started_at_or_after: datetime,
        started_at_or_before: datetime,
        include_deleted: bool = False,
    ) -> list[WorkoutMetricsRecordFake]:
        assert not include_deleted
        self.queried_users.append(user_id)
        return [
            record
            for record in self.records
            if record.athlete_id == user_id
            and started_at_or_after <= record.started_at <= started_at_or_before
        ]


class BaselineRepositoryFake:
    def __init__(self) -> None:
        self.created: list[dict[str, object]] = []

    async def create(self, **values: object) -> object:
        self.created.append(values)
        return object()


@pytest.mark.asyncio
async def test_baseline_service_never_reads_another_users_activity() -> None:
    owner = uuid4()
    other = uuid4()
    records = [
        WorkoutMetricsRecordFake(
            id=uuid4(),
            athlete_id=owner,
            discipline=Discipline.RUNNING,
            started_at=END - timedelta(days=1),
            duration_seconds=3600,
            distance_meters=10_000,
            average_heart_rate=None,
        ),
        WorkoutMetricsRecordFake(
            id=uuid4(),
            athlete_id=other,
            discipline=Discipline.RUNNING,
            started_at=END - timedelta(days=1),
            duration_seconds=36_000,
            distance_meters=100_000,
            average_heart_rate=None,
        ),
    ]
    activities = ActivityRepositoryFake(records)
    baselines = BaselineRepositoryFake()
    service = BaselineService(
        activities=activities,
        baselines=baselines,
        clock=lambda: END,
    )

    result = await service.recalculate(user_id=owner, analysis_end=END)

    run = next(
        item for item in result.disciplines if item.discipline == Discipline.RUNNING
    )
    assert activities.queried_users == [owner]
    assert run.total_duration_seconds == 3600
    assert baselines.created[0]["user_id"] == owner


@pytest.mark.asyncio
async def test_baseline_service_uses_canonical_detail_heart_rate() -> None:
    owner = uuid4()
    activities = ActivityRepositoryFake(
        [
            WorkoutMetricsRecordFake(
                id=uuid4(),
                athlete_id=owner,
                discipline=Discipline.RUNNING,
                started_at=END - timedelta(days=1),
                duration_seconds=3600,
                distance_meters=10_000,
                average_heart_rate=172,
            ),
            WorkoutMetricsRecordFake(
                id=uuid4(),
                athlete_id=owner,
                discipline=Discipline.RUNNING,
                started_at=END - timedelta(days=2),
                duration_seconds=3600,
                distance_meters=10_000,
                average_heart_rate=148,
            ),
        ]
    )
    baselines = BaselineRepositoryFake()
    service = BaselineService(
        activities=activities,
        baselines=baselines,
        clock=lambda: END,
    )

    result = await service.recalculate(user_id=owner, analysis_end=END)

    run = next(
        item for item in result.disciplines if item.discipline == Discipline.RUNNING
    )
    assert run.sessions_count == 2
    assert run.metrics["heart_rate_coverage"] == 1.0
    persisted_disciplines = cast(
        list[dict[str, object]],
        baselines.created[0]["disciplines"],
    )
    persisted_run = next(
        item
        for item in persisted_disciplines
        if item["discipline"] is Discipline.RUNNING
    )
    persisted_metrics = cast(dict[str, object], persisted_run["metrics"])
    assert persisted_metrics["heart_rate_coverage"] == 1.0
