"""Validated normalized profile inputs and safe presentation output."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from app.db.models import (
    BodyArea,
    EquipmentAccessType,
    EquipmentType,
    HealthConstraintType,
)
from app.domain.enums import (
    AthleteGender,
    BaselineSource,
    CoachTone,
    DayOfWeek,
    DetailLevel,
    PrimarySport,
)


class PersistedMandatoryProfileData(BaseModel):
    """The mandatory profile collected before conversational goal intake."""

    birth_year: int
    gender: AthleteGender
    weight_kg: float
    height_cm: float
    availability_text: str | None = None
    equipment_recommendation_text: str | None = None
    equipment_text: str | None = None
    health_limitations_text: str | None = None


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
    main_goal: str
    event_date: date | None
    target_outcome: str
    secondary_priority: str | None
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
