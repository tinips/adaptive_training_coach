"""Validated, persistence-safe boundaries for one weekly training plan."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.enums import Discipline


class _WeeklyPlanSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PlanSession(_WeeklyPlanSchema):
    """One concise, actionable training session shown to the athlete."""

    discipline: Discipline
    objective: str = Field(min_length=1, max_length=200)
    duration_minutes: int = Field(ge=5, le=360)
    intensity: Literal["EASY", "MODERATE", "HARD"]
    structure: str = Field(min_length=1, max_length=800)


class PlanDay(_WeeklyPlanSchema):
    """A calendar day. Empty sessions represent a rest day."""

    date: date
    sessions: tuple[PlanSession, ...] = Field(default=(), max_length=3)
    rest_note: str | None = Field(default=None, max_length=240)

    @model_validator(mode="after")
    def require_a_rest_note_for_rest_days(self) -> PlanDay:
        if not self.sessions and not self.rest_note:
            raise ValueError("rest days require a rest_note")
        if self.sessions and self.rest_note is not None:
            raise ValueError("training days cannot have a rest_note")
        return self


class WeeklyPlan(_WeeklyPlanSchema):
    """Exactly the Monday-to-Sunday plan for one persisted week."""

    week_start: date
    days: tuple[PlanDay, ...] = Field(min_length=7, max_length=7)

    @model_validator(mode="after")
    def require_exact_target_week(self) -> WeeklyPlan:
        expected = tuple(
            date.fromordinal(self.week_start.toordinal() + offset)
            for offset in range(7)
        )
        received = tuple(day.date for day in self.days)
        if self.week_start.weekday() != 0:
            raise ValueError("week_start must be a Monday")
        if received != expected:
            raise ValueError("days must be Monday through Sunday of week_start")
        return self


class PlanReadinessDiscipline(_WeeklyPlanSchema):
    """Recent evidence used to decide whether a discipline may be planned."""

    discipline: Discipline
    session_count: int = Field(ge=0)
    active_day_count: int = Field(ge=0)
    ready: bool
    quality_flags: tuple[str, ...] = ()


class PlanReadiness(_WeeklyPlanSchema):
    """The deterministic preflight outcome, before any provider call."""

    week_start: date
    analysis_started_at: datetime
    analysis_ended_at: datetime
    disciplines: tuple[PlanReadinessDiscipline, ...]

    @property
    def ready(self) -> bool:
        return bool(self.disciplines) and all(item.ready for item in self.disciplines)
