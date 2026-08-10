"""Validated workout write boundaries and joined read projections."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.db.models import (
    CyclingWorkoutDetails,
    HikingWorkoutDetails,
    OtherWorkoutDetails,
    RunningWorkoutDetails,
    StrengthWorkoutDetails,
    SwimmingWorkoutDetails,
    Workout,
)
from app.domain.enums import (
    ActivitySource,
    CyclingType,
    Discipline,
    HikingType,
    RunningType,
    StrengthType,
    SwimmingEnvironment,
    SwimmingStroke,
)

NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
ActivityName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=255),
]


class _StrictSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class StrengthSet(_StrictSchema):
    """One validated strength set; source-specific fields are not accepted."""

    reps: int = Field(ge=0)
    kg: float = Field(ge=0)


class StrengthExercise(_StrictSchema):
    """One named exercise with zero or more validated sets."""

    exercise: NonEmptyText
    sets: list[StrengthSet] = Field(default_factory=list)


class PoolSwimmingDetailsData(_StrictSchema):
    pool_length_meters: float = Field(gt=0)
    total_lengths: int | None = Field(default=None, ge=0)
    primary_stroke: SwimmingStroke | None = None
    average_swolf: float | None = Field(default=None, ge=0)
    total_strokes: int | None = Field(default=None, ge=0)


class RunningWorkoutDetailsData(_StrictSchema):
    running_type: RunningType
    distance_meters: float | None = Field(default=None, ge=0)
    moving_duration_seconds: int | None = Field(default=None, ge=0)
    average_pace_seconds_per_km: float | None = Field(default=None, ge=0)
    elevation_gain_meters: float | None = Field(default=None, ge=0)
    elevation_loss_meters: float | None = Field(default=None, ge=0)
    average_heart_rate: float | None = Field(default=None, ge=0)
    max_heart_rate: float | None = Field(default=None, ge=0)
    average_cadence_spm: float | None = Field(default=None, ge=0)
    max_cadence_spm: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def derive_canonical_pace(self) -> RunningWorkoutDetailsData:
        derived = pace_seconds_per_unit(
            distance_meters=self.distance_meters,
            moving_duration_seconds=self.moving_duration_seconds,
            unit_meters=1000,
        )
        if derived is not None:
            self.average_pace_seconds_per_km = derived
        return self


class CyclingWorkoutDetailsData(_StrictSchema):
    cycling_type: CyclingType
    distance_meters: float | None = Field(default=None, ge=0)
    moving_duration_seconds: int | None = Field(default=None, ge=0)
    average_speed_kph: float | None = Field(default=None, ge=0)
    max_speed_kph: float | None = Field(default=None, ge=0)
    elevation_gain_meters: float | None = Field(default=None, ge=0)
    elevation_loss_meters: float | None = Field(default=None, ge=0)
    average_heart_rate: float | None = Field(default=None, ge=0)
    max_heart_rate: float | None = Field(default=None, ge=0)
    average_cadence_rpm: float | None = Field(default=None, ge=0)
    max_cadence_rpm: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def derive_canonical_speed(self) -> CyclingWorkoutDetailsData:
        derived = speed_kph(
            distance_meters=self.distance_meters,
            moving_duration_seconds=self.moving_duration_seconds,
        )
        if derived is not None:
            self.average_speed_kph = derived
        return self


class HikingWorkoutDetailsData(_StrictSchema):
    hiking_type: HikingType
    distance_meters: float | None = Field(default=None, ge=0)
    moving_duration_seconds: int | None = Field(default=None, ge=0)
    average_pace_seconds_per_km: float | None = Field(default=None, ge=0)
    elevation_gain_meters: float | None = Field(default=None, ge=0)
    elevation_loss_meters: float | None = Field(default=None, ge=0)
    average_heart_rate: float | None = Field(default=None, ge=0)
    max_heart_rate: float | None = Field(default=None, ge=0)
    pack_weight_kg: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def derive_canonical_pace(self) -> HikingWorkoutDetailsData:
        derived = pace_seconds_per_unit(
            distance_meters=self.distance_meters,
            moving_duration_seconds=self.moving_duration_seconds,
            unit_meters=1000,
        )
        if derived is not None:
            self.average_pace_seconds_per_km = derived
        return self


class SwimmingWorkoutDetailsData(_StrictSchema):
    swimming_environment: SwimmingEnvironment
    distance_meters: float | None = Field(default=None, ge=0)
    moving_duration_seconds: int | None = Field(default=None, ge=0)
    average_pace_seconds_per_100m: float | None = Field(default=None, ge=0)
    average_heart_rate: float | None = Field(default=None, ge=0)
    max_heart_rate: float | None = Field(default=None, ge=0)
    pool_details: PoolSwimmingDetailsData | None = None

    @model_validator(mode="after")
    def validate_environment(self) -> SwimmingWorkoutDetailsData:
        if (
            self.swimming_environment is SwimmingEnvironment.POOL
            and self.pool_details is None
        ):
            raise ValueError("A pool swim requires pool_details")
        if (
            self.swimming_environment is SwimmingEnvironment.OPEN_WATER
            and self.pool_details is not None
        ):
            raise ValueError("An open-water swim cannot have pool_details")
        derived = pace_seconds_per_unit(
            distance_meters=self.distance_meters,
            moving_duration_seconds=self.moving_duration_seconds,
            unit_meters=100,
        )
        if derived is not None:
            self.average_pace_seconds_per_100m = derived
        return self


class StrengthWorkoutDetailsData(_StrictSchema):
    strength_type: StrengthType
    session_focus: str | None = Field(default=None, max_length=255)
    exercises_jsonb: list[StrengthExercise] = Field(default_factory=list)


class OtherWorkoutDetailsData(_StrictSchema):
    activity_name: ActivityName
    activity_description: str | None = None
    raw_sport: str | None = Field(default=None, max_length=128)
    raw_sub_sport: str | None = Field(default=None, max_length=128)
    distance_meters: float | None = Field(default=None, ge=0)
    average_heart_rate: float | None = Field(default=None, ge=0)
    max_heart_rate: float | None = Field(default=None, ge=0)
    metrics_jsonb: dict[str, object] | None = None


WorkoutDetailsData = (
    RunningWorkoutDetailsData
    | CyclingWorkoutDetailsData
    | HikingWorkoutDetailsData
    | SwimmingWorkoutDetailsData
    | StrengthWorkoutDetailsData
    | OtherWorkoutDetailsData
)

DETAIL_DISCIPLINE: dict[type[WorkoutDetailsData], Discipline] = {
    RunningWorkoutDetailsData: Discipline.RUNNING,
    CyclingWorkoutDetailsData: Discipline.CYCLING,
    HikingWorkoutDetailsData: Discipline.HIKING,
    SwimmingWorkoutDetailsData: Discipline.SWIMMING,
    StrengthWorkoutDetailsData: Discipline.STRENGTH,
    OtherWorkoutDetailsData: Discipline.OTHER,
}


class WorkoutCreate(_StrictSchema):
    """Delivery-neutral input for a manual or normalized imported workout."""

    athlete_id: UUID
    discipline: Discipline
    started_at: datetime
    duration_seconds: int = Field(gt=0)
    source: ActivitySource
    external_id: str | None = Field(default=None, max_length=128)
    title: str | None = Field(default=None, max_length=255)
    notes: str | None = None
    details: WorkoutDetailsData

    @field_validator("started_at")
    @classmethod
    def require_aware_started_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("started_at must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_source_and_detail(self) -> WorkoutCreate:
        expected = DETAIL_DISCIPLINE[type(self.details)]
        if self.discipline is not expected:
            raise ValueError(
                f"{self.discipline.value} requires {expected.value} details"
            )
        if self.source is ActivitySource.MANUAL:
            if self.external_id is not None:
                raise ValueError("Manual workouts cannot have an external_id")
        elif not self.external_id or not self.external_id.strip():
            raise ValueError("Imported workouts require an external_id")
        return self


class _DetailReadMixin(_StrictSchema):
    workout_id: UUID


class RunningWorkoutDetailsRead(
    _DetailReadMixin,
    RunningWorkoutDetailsData,
):
    pass


class CyclingWorkoutDetailsRead(
    _DetailReadMixin,
    CyclingWorkoutDetailsData,
):
    pass


class HikingWorkoutDetailsRead(
    _DetailReadMixin,
    HikingWorkoutDetailsData,
):
    pass


class SwimmingWorkoutDetailsRead(
    _DetailReadMixin,
    SwimmingWorkoutDetailsData,
):
    pass


class StrengthWorkoutDetailsRead(
    _DetailReadMixin,
    StrengthWorkoutDetailsData,
):
    pass


class OtherWorkoutDetailsRead(
    _DetailReadMixin,
    OtherWorkoutDetailsData,
):
    pass


WorkoutDetailsRead = (
    RunningWorkoutDetailsRead
    | CyclingWorkoutDetailsRead
    | HikingWorkoutDetailsRead
    | SwimmingWorkoutDetailsRead
    | StrengthWorkoutDetailsRead
    | OtherWorkoutDetailsRead
)


class WorkoutRead(_StrictSchema):
    """Universal workout fields plus exactly one discipline-specific payload."""

    id: UUID
    athlete_id: UUID
    discipline: Discipline
    started_at: datetime
    duration_seconds: int
    source: ActivitySource
    external_id: str | None
    title: str | None
    notes: str | None
    created_at: datetime
    updated_at: datetime
    details: WorkoutDetailsRead


class WorkoutMetricsProjection(BaseModel):
    """Flat internal read model used by training-file import outcomes."""

    id: UUID
    athlete_id: UUID
    discipline: Discipline
    started_at: datetime
    duration_seconds: int
    distance_meters: float | None = None
    moving_duration_seconds: int | None = None
    average_heart_rate: float | None = None
    max_heart_rate: float | None = None
    raw_sport: str | None = None
    raw_sub_sport: str | None = None


def pace_seconds_per_unit(
    *,
    distance_meters: float | None,
    moving_duration_seconds: int | None,
    unit_meters: int,
) -> float | None:
    """Derive canonical pace only from positive distance and moving duration."""

    if (
        distance_meters is None
        or moving_duration_seconds is None
        or distance_meters <= 0
        or moving_duration_seconds <= 0
    ):
        return None
    return round(moving_duration_seconds / (distance_meters / unit_meters), 4)


def speed_kph(
    *,
    distance_meters: float | None,
    moving_duration_seconds: int | None,
) -> float | None:
    """Derive canonical kilometres per hour from explicit SI inputs."""

    if (
        distance_meters is None
        or moving_duration_seconds is None
        or distance_meters <= 0
        or moving_duration_seconds <= 0
    ):
        return None
    return round((distance_meters / 1000) / (moving_duration_seconds / 3600), 4)


def values_conflict(
    imported: float | None,
    canonical: float | None,
    *,
    relative_tolerance: float = 0.02,
) -> bool:
    """Return whether an imported pace/speed materially conflicts with derived."""

    if imported is None or canonical is None:
        return False
    scale = max(abs(imported), abs(canonical), 1.0)
    return not math.isclose(
        imported,
        canonical,
        rel_tol=relative_tolerance,
        abs_tol=scale * relative_tolerance,
    )


def main_detail(workout: Workout) -> object:
    """Return the one matching main detail or reject an inconsistent aggregate."""

    found = [
        detail
        for detail in (
            workout.running_details,
            workout.cycling_details,
            workout.hiking_details,
            workout.swimming_details,
            workout.strength_details,
            workout.other_details,
        )
        if detail is not None
    ]
    if len(found) != 1:
        raise ValueError("A workout must have exactly one main detail record")
    detail = found[0]
    expected_types: dict[Discipline, type[object]] = {
        Discipline.RUNNING: RunningWorkoutDetails,
        Discipline.CYCLING: CyclingWorkoutDetails,
        Discipline.HIKING: HikingWorkoutDetails,
        Discipline.SWIMMING: SwimmingWorkoutDetails,
        Discipline.STRENGTH: StrengthWorkoutDetails,
        Discipline.OTHER: OtherWorkoutDetails,
    }
    if not isinstance(detail, expected_types[workout.discipline]):
        raise ValueError("Workout discipline does not match its detail record")
    if isinstance(detail, SwimmingWorkoutDetails):
        if (
            detail.swimming_environment is SwimmingEnvironment.POOL
            and detail.pool_details is None
        ):
            raise ValueError("A pool swim requires pool details")
        if (
            detail.swimming_environment is SwimmingEnvironment.OPEN_WATER
            and detail.pool_details is not None
        ):
            raise ValueError("An open-water swim cannot have pool details")
    return detail


def serialize_workout(workout: Workout) -> WorkoutRead:
    """Serialize an ORM aggregate without leaking metrics into generic fields."""

    detail = main_detail(workout)
    if isinstance(detail, RunningWorkoutDetails):
        details: WorkoutDetailsRead = RunningWorkoutDetailsRead.model_validate(detail)
    elif isinstance(detail, CyclingWorkoutDetails):
        details = CyclingWorkoutDetailsRead.model_validate(detail)
    elif isinstance(detail, HikingWorkoutDetails):
        details = HikingWorkoutDetailsRead.model_validate(detail)
    elif isinstance(detail, SwimmingWorkoutDetails):
        details = SwimmingWorkoutDetailsRead.model_validate(
            {
                **{
                    field: getattr(detail, field)
                    for field in SwimmingWorkoutDetailsData.model_fields
                    if field != "pool_details"
                },
                "workout_id": detail.workout_id,
                "pool_details": (
                    PoolSwimmingDetailsData.model_validate(detail.pool_details)
                    if detail.pool_details is not None
                    else None
                ),
            }
        )
    elif isinstance(detail, StrengthWorkoutDetails):
        details = StrengthWorkoutDetailsRead.model_validate(detail)
    else:
        details = OtherWorkoutDetailsRead.model_validate(detail)
    return WorkoutRead(
        id=workout.id,
        athlete_id=workout.athlete_id,
        discipline=workout.discipline,
        started_at=workout.started_at,
        duration_seconds=workout.duration_seconds,
        source=workout.source,
        external_id=workout.external_id,
        title=workout.title,
        notes=workout.notes,
        created_at=workout.created_at,
        updated_at=workout.updated_at,
        details=details,
    )


def workout_metrics(workout: Workout) -> WorkoutMetricsProjection:
    """Project canonical metrics stored directly on the discipline detail."""

    detail = main_detail(workout)
    distance = getattr(detail, "distance_meters", None)
    moving_duration = getattr(detail, "moving_duration_seconds", None)
    average_hr = getattr(detail, "average_heart_rate", None)
    max_hr = getattr(detail, "max_heart_rate", None)
    active_links = [link for link in workout.source_links if link.deleted_at is None]
    raw_link = next((link for link in active_links if link.raw_sport), None)
    detail_raw_sport = (
        detail.raw_sport if isinstance(detail, OtherWorkoutDetails) else None
    )
    detail_raw_sub_sport = (
        detail.raw_sub_sport if isinstance(detail, OtherWorkoutDetails) else None
    )
    return WorkoutMetricsProjection(
        id=workout.id,
        athlete_id=workout.athlete_id,
        discipline=workout.discipline,
        started_at=workout.started_at,
        duration_seconds=workout.duration_seconds,
        distance_meters=distance,
        moving_duration_seconds=moving_duration,
        average_heart_rate=average_hr,
        max_heart_rate=max_hr,
        raw_sport=(raw_link.raw_sport if raw_link is not None else detail_raw_sport),
        raw_sub_sport=(
            raw_link.raw_sub_sport if raw_link is not None else detail_raw_sub_sport
        ),
    )


__all__ = [
    "CyclingWorkoutDetailsData",
    "HikingWorkoutDetailsData",
    "OtherWorkoutDetailsData",
    "PoolSwimmingDetailsData",
    "RunningWorkoutDetailsData",
    "StrengthExercise",
    "StrengthSet",
    "StrengthWorkoutDetailsData",
    "SwimmingWorkoutDetailsData",
    "WorkoutCreate",
    "WorkoutDetailsData",
    "WorkoutMetricsProjection",
    "WorkoutRead",
    "main_detail",
    "pace_seconds_per_unit",
    "serialize_workout",
    "speed_kph",
    "values_conflict",
    "workout_metrics",
]
