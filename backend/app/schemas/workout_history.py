"""Typed boundaries for the read-only workout-history dashboard."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.enums import Discipline


class _HistorySchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorkoutHistoryQuery(_HistorySchema):
    """One athlete-scoped dashboard request."""

    start_date: date
    end_date: date
    discipline: Discipline | None = None
    cursor: str | None = Field(default=None, max_length=256)

    @model_validator(mode="after")
    def validate_range(self) -> WorkoutHistoryQuery:
        if self.end_date < self.start_date:
            raise ValueError("end_date must not be before start_date")
        return self


class WorkoutHistoryWebAppRequest(WorkoutHistoryQuery):
    """Web App query plus its Telegram-signed identity evidence."""

    init_data: Annotated[str, Field(min_length=1, max_length=8192)]


class WorkoutHistoryTotals(_HistorySchema):
    session_count: int = Field(ge=0)
    duration_seconds: int = Field(ge=0)
    distance_meters: float = Field(ge=0)


class WorkoutHistoryChartBucket(_HistorySchema):
    start_date: date
    label: str
    duration_seconds_by_discipline: dict[str, int]
    distance_meters_by_discipline: dict[str, float]


class WorkoutHistoryCard(_HistorySchema):
    """Compact display projection. Raw source identity and notes stay private."""

    discipline: Discipline
    started_at: datetime
    title: str | None = None
    duration_seconds: int = Field(gt=0)
    distance_meters: float | None = Field(default=None, ge=0)
    calories_kcal: float | None = Field(default=None, ge=0)
    average_heart_rate: float | None = Field(default=None, ge=0)


class WorkoutHistoryResponse(_HistorySchema):
    timezone: str
    available_disciplines: list[Discipline]
    totals: WorkoutHistoryTotals
    chart_buckets: list[WorkoutHistoryChartBucket]
    workouts: list[WorkoutHistoryCard]
    next_cursor: str | None = None
