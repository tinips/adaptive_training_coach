"""Safe presentation model for the current athlete profile."""

from pydantic import BaseModel

from app.domain.enums import AthleteGender


class PersistedMandatoryProfileData(BaseModel):
    birth_year: int
    gender: AthleteGender
    weight_kg: float
    height_cm: float
    availability_text: str | None = None
    equipment_recommendation_text: str | None = None
    equipment_text: str | None = None
    health_limitations_text: str | None = None
