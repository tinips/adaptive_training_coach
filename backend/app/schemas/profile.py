"""Safe presentation models for the current athlete profile and goal."""

from datetime import date

from pydantic import BaseModel

from app.domain.enums import AthleteGender, TrainingGoalStatus
from app.schemas.capabilities import CapabilityAccessItem


class PersistedTrainingGoalData(BaseModel):
    main_goal: str
    target_outcome: str
    event_date: date | None = None
    secondary_priority: str | None = None
    primary_template: str | None = None
    supporting_template: str | None = None
    status: TrainingGoalStatus


class PersistedMandatoryProfileData(BaseModel):
    birth_year: int
    gender: AthleteGender
    weight_kg: float
    height_cm: float
    availability_text: str | None = None
    equipment_access: tuple[CapabilityAccessItem, ...] = ()
    health_limitations_text: str | None = None
    training_goal: PersistedTrainingGoalData | None = None
