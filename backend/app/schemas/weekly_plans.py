"""Validated, persistence-safe boundaries for one weekly training plan."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.enums import Discipline, DisciplineEvidenceState


class _WeeklyPlanSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# 2026-09-09 "Prescribed-range tolerance model" and "Duration derivation" in
# docs/decisions/locked.md. PROVISIONAL, not calibrated against real athlete
# data yet.
_MIN_PACE_RANGE_WIDTH_SECONDS_PER_KM = 10.0
_MIN_SWIM_PACE_RANGE_WIDTH_SECONDS_PER_100M = 3.0
_MIN_POWER_RANGE_WIDTH_WATTS = 10.0
_MIN_DISTANCE_RANGE_WIDTH_METERS: dict[Discipline, float] = {
    Discipline.RUNNING: 250.0,
    Discipline.SWIMMING: 50.0,
    Discipline.CYCLING: 1000.0,
}
# Duration-deriving disciplines: distance and pace are prescribed as ranges,
# duration is never independently prescribed, it is computed from them.
_DURATION_DERIVED_DISCIPLINES = (Discipline.RUNNING, Discipline.SWIMMING)


def _widen_range_to_minimum(
    bounds: tuple[float, float], minimum_width: float
) -> tuple[float, float]:
    """Expand a too-narrow range around its midpoint so it meets the floor."""

    lower, upper = bounds
    width = upper - lower
    if width >= minimum_width:
        return bounds
    deficit = minimum_width - width
    new_lower = max(0.0001, lower - deficit / 2)
    return (new_lower, new_lower + minimum_width)


class SessionTargets(_WeeklyPlanSchema):
    """Measurable intent for one session; every target is independent."""

    duration_minutes: int | None = Field(default=None, ge=5, le=360)
    distance_range_meters: tuple[float, float] | None = None
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

    @model_validator(mode="after")
    def require_ordered_distance_range(self) -> SessionTargets:
        if self.distance_range_meters is not None:
            lower, upper = self.distance_range_meters
            if lower <= 0 or lower > upper:
                raise ValueError(
                    "distance_range_meters must be ordered and positive"
                )
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

    @model_validator(mode="after")
    def widen_narrow_target_range(self) -> IntensityTarget:
        minimum_width = {
            "PACE_SECONDS_PER_KM": _MIN_PACE_RANGE_WIDTH_SECONDS_PER_KM,
            "SWIM_PACE_SECONDS_PER_100M": _MIN_SWIM_PACE_RANGE_WIDTH_SECONDS_PER_100M,
            "POWER_WATTS": _MIN_POWER_RANGE_WIDTH_WATTS,
        }.get(self.metric)
        if minimum_width is not None:
            widened = _widen_range_to_minimum(self.target_range, minimum_width)
            if widened != self.target_range:
                object.__setattr__(self, "target_range", widened)
        return self

    @property
    def is_hard(self) -> bool:
        """Use the explicit perceived-effort range for load-safety decisions."""

        return self.rpe_range[1] >= 7


class PrescribedIntensityTarget(IntensityTarget):
    """Model-facing intensity: heart rate is shown to the athlete, never prescribed.

    Heart rate stays available on the persisted ``IntensityTarget`` so plans
    written before this narrowing still load; dropping it here removes it from
    the JSON schema the coach model fills, so no new plan can carry it.
    """

    metric: Literal[
        "RPE",
        "POWER_WATTS",
        "PACE_SECONDS_PER_KM",
        "SWIM_PACE_SECONDS_PER_100M",
    ]


class PrescribedSessionTargets(_WeeklyPlanSchema):
    """Model-facing targets; completed-workout HR is never prescribed here."""

    duration_minutes: int | None = Field(default=None, ge=5, le=360)
    distance_range_meters: tuple[float, float] | None = None
    average_power_watts: int | None = Field(default=None, gt=0, le=1000)
    pace_seconds_per_km: int | None = Field(default=None, gt=0)
    swim_pace_seconds_per_100m: int | None = Field(default=None, gt=0)
    rpe: int | None = Field(default=None, ge=1, le=10)

    @model_validator(mode="after")
    def require_ordered_distance_range(self) -> PrescribedSessionTargets:
        if self.distance_range_meters is not None:
            lower, upper = self.distance_range_meters
            if lower <= 0 or lower > upper:
                raise ValueError(
                    "distance_range_meters must be ordered and positive"
                )
        return self


class StrengthSessionTargets(_WeeklyPlanSchema):
    """Strength menus intentionally expose duration, and no dosage targets."""

    duration_minutes: int = Field(ge=5, le=360)


def _derived_duration_minutes(
    *, distance_range_meters: tuple[float, float], pace_range: tuple[float, float], unit_meters: float
) -> int:
    """Duration from the average of the prescribed distance and pace ranges.

    See docs/decisions/locked.md, "Duration derivation (continuous and
    structured sessions)": for a continuous single-effort session, duration
    is never independently prescribed, it is derived from the prescribed
    distance and pace ranges, using their average.
    """

    avg_distance_units = (
        (distance_range_meters[0] + distance_range_meters[1]) / 2
    ) / unit_meters
    avg_pace_seconds = (pace_range[0] + pace_range[1]) / 2
    return max(5, round(avg_distance_units * avg_pace_seconds / 60))


def _require_and_derive_session_targets(session: object) -> object:
    """Shared endurance-target rules for both PlanSession and its prescription.

    Strength is untouched, StrengthSessionTargets already requires duration
    at the field level. Cycling keeps duration required alongside power,
    unaffected by duration derivation. Running and swimming require a
    distance range (in addition to the pace range IntensityTarget already
    requires), widen a too-narrow distance range to the floor, and derive
    duration from the two ranges' averages when the model didn't supply one.
    """

    discipline = session.discipline
    if discipline is Discipline.STRENGTH:
        return session
    targets = session.targets
    has_pace_target = session.intensity.metric in (
        "PACE_SECONDS_PER_KM",
        "SWIM_PACE_SECONDS_PER_100M",
    )
    if discipline in _DURATION_DERIVED_DISCIPLINES and has_pace_target:
        if targets.distance_range_meters is None:
            raise ValueError(
                "running and swimming sessions require targets.distance_range_meters"
            )
        minimum_width = _MIN_DISTANCE_RANGE_WIDTH_METERS[discipline]
        widened = _widen_range_to_minimum(targets.distance_range_meters, minimum_width)
        if widened != targets.distance_range_meters:
            object.__setattr__(targets, "distance_range_meters", widened)
        if targets.duration_minutes is None:
            unit_meters = 1000.0 if discipline is Discipline.RUNNING else 100.0
            derived = _derived_duration_minutes(
                distance_range_meters=targets.distance_range_meters,
                pace_range=session.intensity.target_range,
                unit_meters=unit_meters,
            )
            object.__setattr__(targets, "duration_minutes", derived)
    else:
        # Cycling, and any running/swimming session with no pace target
        # (RPE-fallback, already locked as an exception): distance stays
        # optional and contextual; duration stays independently required,
        # unchanged from before.
        if targets.distance_range_meters is not None:
            minimum_width = _MIN_DISTANCE_RANGE_WIDTH_METERS.get(discipline)
            if minimum_width is not None:
                widened = _widen_range_to_minimum(
                    targets.distance_range_meters, minimum_width
                )
                if widened != targets.distance_range_meters:
                    object.__setattr__(targets, "distance_range_meters", widened)
        if targets.duration_minutes is None:
            raise ValueError("sessions require targets.duration_minutes")
    return session


class PlanSession(_WeeklyPlanSchema):
    """One concise, actionable training session shown to the athlete.

    ``id`` is a code-generated stable reference (docs/decisions/locked.md,
    "First-week evaluator"): every planned session gets one so a logged
    workout can link to it later. It is never authored by the model; the
    LLM-facing ``PlanSessionPrescription`` carries no ``id`` field at all, so
    the default here is the only way one is produced. A legacy (schema-v4 and
    earlier) plan payload has no stored "id" key, so this default also
    synthesizes a fresh id at load time for those rows -- that id is *not*
    stable across reloads and such a plan must not be treated as linkable.
    """

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    discipline: Discipline
    purpose: str = Field(min_length=1, max_length=120)
    intensity: IntensityTarget
    objective: str = Field(min_length=1, max_length=200)
    targets: SessionTargets
    execution: str = Field(min_length=1, max_length=800)

    @model_validator(mode="after")
    def require_duration_target(self) -> PlanSession:
        return _require_and_derive_session_targets(self)


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


class PlanSessionPrescription(_WeeklyPlanSchema):
    """Model-authored session intent with no heart-rate prescription fields."""

    discipline: Discipline
    purpose: str = Field(min_length=1, max_length=120)
    intensity: PrescribedIntensityTarget
    objective: str = Field(min_length=1, max_length=200)
    targets: PrescribedSessionTargets
    execution: str = Field(min_length=1, max_length=800)

    @model_validator(mode="after")
    def require_duration_target(self) -> PlanSessionPrescription:
        return _require_and_derive_session_targets(self)


class FirstWeekEnduranceSessionPrescription(PlanSessionPrescription):
    """A model-authored first-week endurance session."""

    discipline: Literal[
        Discipline.RUNNING,
        Discipline.CYCLING,
        Discipline.SWIMMING,
    ]


class FirstWeekStrengthSessionPrescription(PlanSessionPrescription):
    """A model-authored first-week strength session with duration-only targets."""

    discipline: Literal[Discipline.STRENGTH]
    targets: StrengthSessionTargets  # type: ignore[assignment]


FirstWeekSessionPrescription = Annotated[
    FirstWeekEnduranceSessionPrescription | FirstWeekStrengthSessionPrescription,
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


class SessionPrescription(PlanSessionPrescription):
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
    sessions: tuple[FirstWeekSessionPrescription, ...] = Field(
        min_length=1, max_length=14
    )
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
