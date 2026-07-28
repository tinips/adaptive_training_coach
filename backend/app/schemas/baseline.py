"""Boundary models for deterministic athlete baseline calculation."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.domain.enums import (
    BaselineSource,
    BaselineStatus,
    Discipline,
    LevelLabel,
)


class BaselineActivity(BaseModel):
    """Minimal, provider-neutral activity input used by the engine."""

    id: UUID | None = None
    discipline: Discipline
    started_at: datetime
    duration_seconds: int = Field(ge=0)
    distance_meters: float | None = Field(default=None, ge=0)
    average_heart_rate: float | None = Field(default=None, ge=0)

    @field_validator("started_at")
    @classmethod
    def require_aware_start(cls, value: datetime) -> datetime:
        """Ensure week/recency calculations never use ambiguous time."""

        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Activity timestamps must be timezone-aware.")
        return value


class DisciplineBaselineResult(BaseModel):
    """Calculated metrics for one discipline."""

    discipline: Discipline
    level_label: LevelLabel
    confidence: float = Field(ge=0, le=1)
    sessions_count: int = Field(ge=0)
    active_weeks: int = Field(ge=0)
    total_duration_seconds: int = Field(ge=0)
    average_weekly_duration_seconds: float = Field(ge=0)
    total_distance_meters: float | None = Field(default=None, ge=0)
    average_weekly_distance_meters: float | None = Field(default=None, ge=0)
    longest_session_seconds: int | None = Field(default=None, ge=0)
    longest_distance_meters: float | None = Field(default=None, ge=0)
    recent_session_count: int = Field(ge=0)
    metrics: dict[str, object] = Field(default_factory=dict)


class BaselineCalculation(BaseModel):
    """A versionable baseline result ready for persistence."""

    generated_at: datetime
    analysis_start: datetime
    analysis_end: datetime
    source: BaselineSource = BaselineSource.STRAVA
    status: BaselineStatus
    overall_confidence: float = Field(ge=0, le=1)
    disciplines: list[DisciplineBaselineResult]
