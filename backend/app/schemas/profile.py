"""Safe presentation models for the current athlete profile and goal."""

from datetime import date

from pydantic import BaseModel

from app.domain.enums import AthleteGender
from app.schemas.capabilities import CapabilityAccessItem


class PersistedTrainingGoalData(BaseModel):
    main_goal: str
    event_date: date | None = None
    secondary_priority: str | None = None
    primary_template: str | None = None
    supporting_template: str | None = None
    target_distance_km: float | None = None
    target_elevation_m: float | None = None
    target_pace_seconds_per_km: float | None = None
    target_swim_pace_seconds_per_100m: float | None = None
    target_average_speed_kph: float | None = None
    target_finish_time_seconds: int | None = None


class PersistedMandatoryProfileData(BaseModel):
    birth_year: int
    gender: AthleteGender
    weight_kg: float
    height_cm: float
    timezone: str | None = None
    availability_text: str | None = None
    equipment_access: tuple[CapabilityAccessItem, ...] = ()
    health_limitations_text: str | None = None
    training_goal: PersistedTrainingGoalData | None = None
