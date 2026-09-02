"""Strict contracts for extracted and confirmed weekly availability."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

_DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
AvailabilityDiscipline = Literal["running", "cycling", "swimming", "strength_training"]
TimeOfDay = Literal["morning", "afternoon", "evening", "night"]


class AvailabilityWindow(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    time_of_day: TimeOfDay | None = None
    duration_minutes: int = Field(gt=0, le=1440)


class AvailabilityDay(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    available: bool
    disciplines: tuple[AvailabilityDiscipline, ...] = ()
    time_windows: tuple[AvailabilityWindow, ...] = ()

    @model_validator(mode="after")
    def enforce_day_invariants(self) -> AvailabilityDay:
        if not self.available and (self.disciplines or self.time_windows):
            raise ValueError("unavailable days cannot have disciplines or time windows")
        if self.available and not self.disciplines:
            raise ValueError("available days require at least one discipline")
        return self


class AvailabilityExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    parse_status: Literal["complete", "needs_details", "needs_clarification"]
    clarification_reason: str | None = Field(default=None, max_length=500)
    missing_details: tuple[dict[str, str], ...] = ()
    days: dict[str, AvailabilityDay]

    @model_validator(mode="after")
    def require_all_days(self) -> AvailabilityExtraction:
        if set(self.days) != set(_DAYS) or len(self.days) != len(_DAYS):
            raise ValueError("days must be Monday through Sunday")
        return self


class ConfirmedWeeklyAvailability(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[2] = 2
    status: Literal["confirmed"] = "confirmed"
    days: dict[str, AvailabilityDay]

    @model_validator(mode="after")
    def require_all_days(self) -> ConfirmedWeeklyAvailability:
        if set(self.days) != set(_DAYS) or len(self.days) != len(_DAYS):
            raise ValueError("days must be Monday through Sunday")
        return self
