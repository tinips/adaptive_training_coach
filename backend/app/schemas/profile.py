"""Validated normalized profile inputs and safe presentation output."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.db.models import (
    BodyArea,
    EquipmentAccessType,
    EquipmentType,
    GoalType,
    HealthConstraintType,
)
from app.domain.enums import (
    BaselineSource,
    CoachTone,
    DayOfWeek,
    DetailLevel,
    GoalPriority,
    PrimarySport,
)


class AccessSelection(BaseModel):
    """Normalized regular, irregular, or unavailable access."""

    model_config = ConfigDict(extra="forbid")

    type: EquipmentAccessType
    days: list[DayOfWeek] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_days(self) -> AccessSelection:
        if self.type is EquipmentAccessType.REGULAR and not self.days:
            raise ValueError("regular access requires at least one day")
        if self.type is not EquipmentAccessType.REGULAR and self.days:
            raise ValueError("non-regular access cannot contain days")
        return self


class FinalOnboardingAnswers(BaseModel):
    """Complete confirmed staging document required for finalization."""

    model_config = ConfigDict(extra="ignore")

    consent: Literal[True]
    primary_sport: PrimarySport
    goal_type: GoalType
    event_status: bool
    event_name: str | None = Field(default=None, max_length=120)
    event_date: date | None = None
    goal_priority: GoalPriority
    age: int = Field(ge=16, le=100)
    height: float | None = Field(default=None, ge=120, le=230)
    weight: float | None = Field(default=None, ge=35, le=250)
    training_days: list[DayOfWeek] = Field(min_length=1)
    weekday_duration: int | Literal["OVER_90", "VARIABLE"]
    weekend_duration: int | Literal["OVER_180", "VARIABLE"]
    equipment: list[EquipmentType] = Field(default_factory=list)
    equipment_other_description: str | None = Field(default=None, max_length=500)
    pool_access: AccessSelection | None = None
    bike_access: AccessSelection | None = None
    health_areas: list[str] = Field(min_length=1)
    health_areas_other_description: str | None = Field(
        default=None,
        max_length=500,
    )
    health_timing: HealthConstraintType | None = None
    health_description: str | None = Field(default=None, max_length=500)
    coach_tone: CoachTone
    coach_detail: DetailLevel
    baseline_source: BaselineSource

    @model_validator(mode="after")
    def validate_conditionals(self) -> FinalOnboardingAnswers:
        if self.event_status and (not self.event_name or self.event_date is None):
            raise ValueError("target event requires name and date")
        if not self.event_status:
            self.event_name = None
            self.event_date = None

        allowed_areas = {"NONE", *(area.value for area in BodyArea)}
        if any(area not in allowed_areas for area in self.health_areas):
            raise ValueError("unsupported health area")
        if "NONE" in self.health_areas and len(self.health_areas) != 1:
            raise ValueError("NONE is exclusive")
        if self.health_areas != ["NONE"] and self.health_timing is None:
            raise ValueError("health timing is required")
        if self.health_areas == ["NONE"]:
            self.health_timing = None
            self.health_description = None
        if EquipmentType.OTHER not in self.equipment:
            self.equipment_other_description = None
        if BodyArea.OTHER.value not in self.health_areas:
            self.health_areas_other_description = None
        return self


class PersistedEquipmentAccessData(BaseModel):
    """One normalized equipment/access record safe for presentation."""

    equipment_type: EquipmentType
    access_type: EquipmentAccessType
    access_days: list[DayOfWeek] = Field(default_factory=list)
    notes: str | None = None


class PersistedHealthConstraintData(BaseModel):
    """One normalized non-diagnostic health limitation."""

    body_area: BodyArea | None
    constraint_type: HealthConstraintType
    description: str | None = None


class PersistedProfileData(BaseModel):
    """Safe, delivery-neutral representation of a normalized profile."""

    primary_sport: PrimarySport
    goal_type: GoalType
    event_name: str | None
    event_date: date | None
    goal_priority: GoalPriority
    age: int
    height_cm: float | None
    weight_kg: float | None
    training_days: list[DayOfWeek]
    weekday_duration: int | Literal["OVER_90", "VARIABLE"] | None = None
    weekend_duration: int | Literal["OVER_180", "VARIABLE"] | None = None
    equipment: list[EquipmentType]
    equipment_access: list[PersistedEquipmentAccessData] = Field(
        default_factory=list,
    )
    health_constraints: list[str]
    health_constraint_details: list[PersistedHealthConstraintData] = Field(
        default_factory=list,
    )
    coach_tone: CoachTone
    detail_level: DetailLevel
    baseline_source: BaselineSource
