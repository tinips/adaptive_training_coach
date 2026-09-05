"""Validated, persistence-safe boundaries for one weekly training plan."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.enums import Discipline, DisciplineEvidenceState


class _WeeklyPlanSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SessionTargets(_WeeklyPlanSchema):
    """Measurable intent for one session; every target is independent."""

    duration_minutes: int | None = Field(default=None, ge=5, le=360)
    distance_meters: float | None = Field(default=None, gt=0)
    average_hr_bpm: int | None = Field(default=None, gt=0, le=230)
    hr_range_bpm: tuple[int, int] | None = None
    average_power_watts: int | None = Field(default=None, gt=0, le=1000)
    pace_seconds_per_km: int | None = Field(default=None, gt=0)
    swim_pace_seconds_per_100m: int | None = Field(default=None, gt=0)
    rpe: int | None = Field(default=None, ge=1, le=10)

    @model_validator(mode="after")
    def require_ordered_heart_rate_range(self) -> SessionTargets:
        if self.hr_range_bpm is not None and (
            self.hr_range_bpm[0] <= 0
            or self.hr_range_bpm[1] > 230
            or self.hr_range_bpm[0] > self.hr_range_bpm[1]
        ):
            raise ValueError("hr_range_bpm must be an ordered positive range up to 230")
        return self


class IntensityTarget(_WeeklyPlanSchema):
    """Evaluator-ready intensity prescription for one training session."""

    metric: Literal[
        "RPE",
        "HEART_RATE_BPM",
        "POWER_WATTS",
        "PACE_SECONDS_PER_KM",
        "SWIM_PACE_SECONDS_PER_100M",
    ]
    target_range: tuple[float, float]
    rpe_range: tuple[int, int]
    guidance: str = Field(min_length=1, max_length=240)

    @model_validator(mode="after")
    def require_ordered_ranges(self) -> IntensityTarget:
        lower, upper = self.target_range
        rpe_lower, rpe_upper = self.rpe_range
        if lower <= 0 or lower > upper:
            raise ValueError("target_range must be ordered and positive")
        if not 1 <= rpe_lower <= rpe_upper <= 10:
            raise ValueError("rpe_range must be ordered and between 1 and 10")
        if self.metric == "RPE" and self.target_range != tuple(
            float(value) for value in self.rpe_range
        ):
            raise ValueError("RPE target_range must match rpe_range")
        return self

    @property
    def is_hard(self) -> bool:
        """Use the explicit perceived-effort range for load-safety decisions."""

        return self.rpe_range[1] >= 7


class StrengthSessionTargets(_WeeklyPlanSchema):
    """Strength menus intentionally expose duration, and no dosage targets."""

    duration_minutes: int = Field(ge=5, le=360)


class PlanSession(_WeeklyPlanSchema):
    """One concise, actionable training session shown to the athlete."""

    discipline: Discipline
    purpose: str = Field(min_length=1, max_length=120)
    intensity: IntensityTarget
    objective: str = Field(min_length=1, max_length=200)
    targets: SessionTargets
    execution: str = Field(min_length=1, max_length=800)

    @model_validator(mode="after")
    def require_duration_target(self) -> PlanSession:
        if self.targets.duration_minutes is None:
            raise ValueError("sessions require targets.duration_minutes")
        return self


class FirstWeekEnduranceSession(PlanSession):
    """A first-week endurance session with the normal metric target vocabulary."""

    discipline: Literal[
        Discipline.RUNNING,
        Discipline.CYCLING,
        Discipline.SWIMMING,
    ]


class FirstWeekStrengthSession(PlanSession):
    """A first-week strength session with a duration-only target contract."""

    discipline: Literal[Discipline.STRENGTH]
    targets: StrengthSessionTargets  # type: ignore[assignment]


FirstWeekSession = Annotated[
    FirstWeekEnduranceSession | FirstWeekStrengthSession,
    Field(discriminator="discipline"),
]


def _coerce_first_week_sessions(value: object) -> object:
    """Accept legacy in-process PlanSession instances at the menu boundary."""

    if not isinstance(value, dict):
        return value
    sessions = value.get("sessions")
    if not isinstance(sessions, (list, tuple)):
        return value
    normalized = [
        session.model_dump(mode="python")
        if isinstance(session, PlanSession)
        else session
        for session in sessions
    ]
    return {**value, "sessions": normalized}


class SessionPrescription(PlanSession):
    """Coach-authored session intent before deterministic calendar placement."""

    priority: Literal["ESSENTIAL", "IMPORTANT", "OPTIONAL"] = "IMPORTANT"
    preferred_weekdays: tuple[
        Literal[
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
            "friday",
            "saturday",
            "sunday",
        ],
        ...,
    ] = ()
    avoid_weekdays: tuple[
        Literal[
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
            "friday",
            "saturday",
            "sunday",
        ],
        ...,
    ] = ()
    can_share_day: bool = True


class WeeklyPlanPrescription(_WeeklyPlanSchema):
    """Volume and session intent returned by the coach model, without dates."""

    week_start: date
    sessions: tuple[SessionPrescription, ...] = Field(min_length=1, max_length=14)


class FirstWeekPlanPrescription(_WeeklyPlanSchema):
    """Unscheduled probe sessions proposed by the first-week coach."""

    week_start: date
    sessions: tuple[FirstWeekSession, ...] = Field(min_length=1, max_length=14)
    guardrails: tuple[str, ...] = Field(default=(), max_length=12)
    logging_instructions: tuple[str, ...] = Field(default=(), max_length=8)
    tests: tuple[str, ...] = Field(default=(), max_length=0)

    @model_validator(mode="before")
    @classmethod
    def coerce_legacy_sessions(cls, value: object) -> object:
        return _coerce_first_week_sessions(value)


class FirstWeekPlan(_WeeklyPlanSchema):
    """Athlete-placed first-week menu, intentionally without calendar dates."""

    plan_kind: Literal["FIRST_WEEK_MENU"] = "FIRST_WEEK_MENU"
    week_start: date
    sessions: tuple[FirstWeekSession, ...] = Field(min_length=1, max_length=14)
    guardrails: tuple[str, ...] = Field(min_length=1, max_length=12)
    logging_instructions: tuple[str, ...] = Field(min_length=1, max_length=8)
    tests: tuple[str, ...] = Field(default=(), max_length=0)
    sessions_per_discipline: dict[Discipline, int]
    total_minutes_per_discipline: dict[Discipline, int]

    @model_validator(mode="before")
    @classmethod
    def coerce_legacy_sessions(cls, value: object) -> object:
        return _coerce_first_week_sessions(value)

    @model_validator(mode="after")
    def require_accurate_summaries(self) -> FirstWeekPlan:
        counts: dict[Discipline, int] = {}
        minutes: dict[Discipline, int] = {}
        for session in self.sessions:
            counts[session.discipline] = counts.get(session.discipline, 0) + 1
            minutes[session.discipline] = minutes.get(session.discipline, 0) + (
                session.targets.duration_minutes or 0
            )
        if self.sessions_per_discipline != counts:
            raise ValueError("sessions_per_discipline must summarize sessions exactly")
        if self.total_minutes_per_discipline != minutes:
            raise ValueError(
                "total_minutes_per_discipline must summarize sessions exactly"
            )
        return self


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
    """Recent evidence held for one target discipline."""

    discipline: Discipline
    session_count: int = Field(ge=0)
    active_day_count: int = Field(ge=0)
    state: DisciplineEvidenceState
    quality_flags: tuple[str, ...] = ()


class PlanReadiness(_WeeklyPlanSchema):
    """The deterministic preflight outcome, before any provider call.

    ``ready`` is judged on the athlete as a whole rather than per discipline,
    so a sport with little history is planned gently instead of blocking the
    sports that are ready.
    """

    week_start: date
    analysis_started_at: datetime
    analysis_ended_at: datetime
    disciplines: tuple[PlanReadinessDiscipline, ...]
    total_session_count: int = Field(ge=0)
    total_active_day_count: int = Field(ge=0)
    ready: bool
