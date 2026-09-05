"""Request/response contracts for the manual (AI-assisted) workout import.

This is a deliberately separate, minimal path: a screenshot of a workout
summary (for example, from a source app's own detail screen) gets read by
an AI assistant, which extracts the metrics visible there and calls this
endpoint directly. It exists because some source apps keep more accurate
numbers privately than they expose in standard exports. Authentication is a
single shared secret; this path is not tied to a particular phone.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

NonEmptyText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=255),
]


class _StrictManualImportSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ManualSwimmingDetails(_StrictManualImportSchema):
    """Swim-specific fields a source app's own screen usually shows."""

    environment: Literal["POOL", "OPEN_WATER"]
    pool_length_meters: float | None = Field(default=None, gt=0)
    total_lengths: int | None = Field(default=None, ge=0)
    primary_stroke: (
        Literal[
            "FREESTYLE",
            "BREASTSTROKE",
            "BACKSTROKE",
            "BUTTERFLY",
            "MIXED",
            "OTHER",
        ]
        | None
    ) = None
    total_strokes: int | None = Field(default=None, ge=0)


class ManualWorkoutImportRequest(_StrictManualImportSchema):
    """One workout, extracted from a screenshot rather than a device sync."""

    discipline: Literal["RUNNING", "CYCLING", "SWIMMING", "STRENGTH"]
    source_app_name: NonEmptyText
    started_at: datetime
    duration_seconds: int = Field(gt=0, le=7 * 24 * 60 * 60)
    distance_meters: float | None = Field(default=None, ge=0)
    calories_active_kcal: float | None = Field(default=None, ge=0)
    calories_total_kcal: float | None = Field(default=None, ge=0)
    average_heart_rate: float | None = Field(default=None, ge=0, le=300)
    max_heart_rate: float | None = Field(default=None, ge=0, le=300)
    average_pace_seconds_per_km: float | None = Field(default=None, ge=0)
    average_pace_seconds_per_100m: float | None = Field(default=None, ge=0)
    average_speed_kph: float | None = Field(default=None, ge=0)
    max_speed_kph: float | None = Field(default=None, ge=0)
    average_power_watts: float | None = Field(default=None, ge=0)
    max_power_watts: float | None = Field(default=None, ge=0)
    average_cadence: float | None = Field(default=None, ge=0)
    max_cadence: float | None = Field(default=None, ge=0)
    swimming: ManualSwimmingDetails | None = None

    @model_validator(mode="after")
    def require_swimming_details_only_for_swimming(
        self,
    ) -> ManualWorkoutImportRequest:
        if self.discipline == "SWIMMING" and self.swimming is None:
            raise ValueError("swimming details are required for a SWIMMING import")
        if self.discipline != "SWIMMING" and self.swimming is not None:
            raise ValueError("swimming details are only valid for a SWIMMING import")
        return self


class ManualWorkoutImportResponse(_StrictManualImportSchema):
    """Confirms what was saved, so the caller can sanity-check the extraction."""

    workout_id: str
    outcome: Literal["inserted", "updated", "unchanged"]


__all__ = [
    "ManualSwimmingDetails",
    "ManualWorkoutImportRequest",
    "ManualWorkoutImportResponse",
]
