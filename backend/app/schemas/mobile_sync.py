"""Strict HTTP contracts for the iPhone HealthKit proof of concept."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)


class _StrictMobileSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


PairingCode = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=8, max_length=64),
]
HealthKitActivityType = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=128),
]
HealthKitQuantityType = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=256),
]
HealthKitQuantityDescription = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=256),
]


class MobilePairRequest(_StrictMobileSchema):
    """One-time code and stable local app installation identifier."""

    pairing_code: PairingCode
    installation_id: UUID


class MobilePairResponse(_StrictMobileSchema):
    """Opaque bearer token returned exactly once to the iPhone."""

    access_token: str
    token_type: Literal["Bearer"] = "Bearer"


class HealthKitQuantityStatisticsPayload(_StrictMobileSchema):
    """Lossless display values for every statistic associated with a workout.

    HealthKit does not expose the unit used by an ``HKQuantity`` separately.
    The iPhone therefore sends its canonical HealthKit description (for
    example, ``"148 count/min"``) instead of guessing a conversion for an
    unknown future quantity type.
    """

    sum: HealthKitQuantityDescription | None = None
    minimum: HealthKitQuantityDescription | None = None
    maximum: HealthKitQuantityDescription | None = None
    average: HealthKitQuantityDescription | None = None


class HealthKitRawQuantitySamplePayload(_StrictMobileSchema):
    """One source-matched quantity sample within a workout's time window."""

    sample_uuid: UUID
    quantity_type: HealthKitQuantityType
    started_at: datetime
    ended_at: datetime
    value: HealthKitQuantityDescription
    heart_rate_bpm: float | None = Field(default=None, gt=0, le=300)
    source_name: str | None = Field(default=None, max_length=255)
    association: Literal["workout_associated", "time_window_source_match"]

    @field_validator("started_at", "ended_at")
    @classmethod
    def require_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamps must be timezone-aware")
        return value

    @model_validator(mode="after")
    def require_valid_interval(self) -> HealthKitRawQuantitySamplePayload:
        if self.ended_at < self.started_at:
            raise ValueError("ended_at cannot be before started_at")
        return self


class HealthKitWorkoutPayload(_StrictMobileSchema):
    """HealthKit workout evidence plus lossless source-specific metrics."""

    workout_uuid: UUID
    activity_type: HealthKitActivityType
    started_at: datetime
    ended_at: datetime
    duration_seconds: int = Field(gt=0, le=7 * 24 * 60 * 60)
    moving_duration_seconds: int | None = Field(default=None, ge=0)
    distance_meters: float | None = Field(default=None, ge=0)
    elevation_gain_meters: float | None = Field(default=None, ge=0)
    elevation_loss_meters: float | None = Field(default=None, ge=0)
    calories_kcal: float | None = Field(default=None, ge=0)
    average_heart_rate: float | None = Field(default=None, ge=0)
    max_heart_rate: float | None = Field(default=None, ge=0)
    average_cadence: float | None = Field(default=None, ge=0)
    max_cadence: float | None = Field(default=None, ge=0)
    source_name: str | None = Field(default=None, max_length=255)
    all_statistics: dict[HealthKitQuantityType, HealthKitQuantityStatisticsPayload] = (
        Field(default_factory=dict)
    )
    raw_quantity_samples: list[HealthKitRawQuantitySamplePayload] = Field(
        default_factory=list
    )

    @field_validator("started_at", "ended_at")
    @classmethod
    def require_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamps must be timezone-aware")
        return value

    @model_validator(mode="after")
    def require_valid_interval(self) -> HealthKitWorkoutPayload:
        elapsed_seconds = (self.ended_at - self.started_at).total_seconds()
        if elapsed_seconds <= 0:
            raise ValueError("ended_at must be after started_at")
        # HealthKit workout duration can exclude pauses, but cannot be longer
        # than the elapsed interval except for harmless sub-second rounding.
        if self.duration_seconds > elapsed_seconds + 1:
            raise ValueError("duration_seconds cannot exceed the workout interval")
        return self


class HealthKitWorkoutSyncRequest(_StrictMobileSchema):
    """A bounded batch allows a manual sync without accepting an archive."""

    workouts: list[HealthKitWorkoutPayload] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def require_unique_workouts(self) -> HealthKitWorkoutSyncRequest:
        workout_ids = [workout.workout_uuid for workout in self.workouts]
        if len(set(workout_ids)) != len(workout_ids):
            raise ValueError("workout_uuid values must be unique within a sync")
        return self


class HealthKitWorkoutSyncResult(_StrictMobileSchema):
    """Safe idempotency result for one submitted HealthKit workout."""

    workout_uuid: UUID
    workout_id: UUID
    outcome: Literal["inserted", "updated", "unchanged"]


class HealthKitWorkoutSyncResponse(_StrictMobileSchema):
    results: list[HealthKitWorkoutSyncResult]


__all__ = [
    "HealthKitQuantityStatisticsPayload",
    "HealthKitRawQuantitySamplePayload",
    "HealthKitWorkoutPayload",
    "HealthKitWorkoutSyncRequest",
    "HealthKitWorkoutSyncResponse",
    "HealthKitWorkoutSyncResult",
    "MobilePairRequest",
    "MobilePairResponse",
]
