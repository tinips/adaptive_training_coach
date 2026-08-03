"""Validated normalized profile inputs and safe presentation output."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

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
