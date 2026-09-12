"""Deterministic preparation tiers for first-week probe menus."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import Discipline, DisciplineEvidenceState
from app.schemas.baseline import AthleteBaselineData
from app.schemas.weekly_plans import PlanReadiness

BaselineTier = Literal["UNPREPARED", "DEVELOPING", "TRAINED", "WELL_TRAINED"]
AthleteEnduranceLevel = Literal[
    "ENDURANCE_UNTRAINED",
    "ENDURANCE_DEVELOPING",
    "ENDURANCE_TRAINED",
    "ENDURANCE_WELL_TRAINED",
]


class FirstWeekAthleteEnduranceContext(BaseModel):
    """Whole-athlete endurance context, distinct from sport-specific tiers."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    level: AthleteEnduranceLevel
    reported_endurance_sessions: int = Field(ge=0)
    reported_endurance_minutes: int = Field(ge=0)
    active_endurance_disciplines: tuple[Discipline, ...]
    max_controlled_sessions: int = Field(ge=0, le=3)
    guidance: str = Field(min_length=1, max_length=240)


def resolve_first_week_tiers(
    *,
    baseline: AthleteBaselineData | None,
    readiness: PlanReadiness,
    disciplines: tuple[Discipline, ...],
) -> dict[Discipline, BaselineTier]:
    """Classify each discipline from stated volume and recent evidence.

    The tiers describe preparation for a calibration menu, rather than
    physiology or a prediction of performance.
    """

    evidence_by_discipline = {row.discipline: row for row in readiness.disciplines}
    return {
        discipline: _tier_for(
            baseline=baseline,
            discipline=discipline,
            state=evidence_by_discipline.get(discipline),
        )
        for discipline in disciplines
    }


def resolve_first_week_athlete_endurance_context(
    *,
    baseline: AthleteBaselineData | None,
    readiness: PlanReadiness,
) -> FirstWeekAthleteEnduranceContext:
    """Describe whole-athlete training exposure without promoting a sport tier.

    A triathlete may be endurance-trained while still developing running-specific
    tolerance. This context controls total first-week calibration demand; the
    discipline tier continues to control each sport's intensity safety.
    """

    records = (
        baseline.running if baseline is not None else None,
        baseline.cycling if baseline is not None else None,
        baseline.swimming if baseline is not None else None,
    )
    reported_sessions = sum(
        value
        for record in records
        if isinstance((value := getattr(record, "typical_weekly_sessions", 0)), int)
    )
    reported_minutes = sum(
        value
        for record in records
        if isinstance(
            (value := getattr(record, "typical_weekly_duration_minutes", 0)), int
        )
    )
    active_disciplines = tuple(
        discipline
        for discipline, record in zip(
            (Discipline.RUNNING, Discipline.CYCLING, Discipline.SWIMMING),
            records,
            strict=True,
        )
        if getattr(record, "typical_weekly_sessions", 0) > 0
        or getattr(record, "typical_weekly_duration_minutes", 0) > 0
    )
    evidenced_sessions = sum(row.session_count for row in readiness.disciplines)
    well_evidenced = any(
        row.state is DisciplineEvidenceState.WELL_EVIDENCED
        for row in readiness.disciplines
    )
    if reported_sessions == 0 and reported_minutes == 0 and evidenced_sessions == 0:
        return FirstWeekAthleteEnduranceContext(
            level="ENDURANCE_UNTRAINED",
            reported_endurance_sessions=0,
            reported_endurance_minutes=0,
            active_endurance_disciplines=(),
            max_controlled_sessions=0,
            guidance=(
                "No whole-athlete endurance base is stated or evidenced: keep the "
                "entire first week easy and low volume."
            ),
        )
    if (
        len(active_disciplines) >= 2
        and (reported_sessions >= 8 or reported_minutes >= 480)
        and well_evidenced
    ):
        level: AthleteEnduranceLevel = "ENDURANCE_WELL_TRAINED"
        maximum = 3
    elif len(active_disciplines) >= 2 and (
        reported_sessions >= 5 or reported_minutes >= 240 or well_evidenced
    ):
        level = "ENDURANCE_TRAINED"
        maximum = 2
    elif reported_sessions >= 3 or reported_minutes >= 150 or well_evidenced:
        level = "ENDURANCE_TRAINED"
        maximum = 1
    else:
        level = "ENDURANCE_DEVELOPING"
        maximum = 1
    return FirstWeekAthleteEnduranceContext(
        level=level,
        reported_endurance_sessions=reported_sessions,
        reported_endurance_minutes=reported_minutes,
        active_endurance_disciplines=active_disciplines,
        max_controlled_sessions=maximum,
        guidance=(
            "Use whole-athlete endurance context to distribute first-week load and "
            "controlled work; it never upgrades sport-specific intensity safety."
        ),
    )


def _tier_for(
    *,
    baseline: AthleteBaselineData | None,
    discipline: Discipline,
    state: object | None,
) -> BaselineTier:
    record = (
        {
            Discipline.RUNNING: baseline.running,
            Discipline.CYCLING: baseline.cycling,
            Discipline.SWIMMING: baseline.swimming,
        }.get(discipline)
        if baseline is not None
        else None
    )
    sessions = getattr(record, "typical_weekly_sessions", 0)
    minutes = getattr(record, "typical_weekly_duration_minutes", 0)
    longest = _longest_minutes(record, discipline)
    evidence_state = getattr(state, "state", DisciplineEvidenceState.NONE)

    if sessions == 0 and minutes == 0 and getattr(state, "session_count", 0) == 0:
        return "UNPREPARED"
    if evidence_state is DisciplineEvidenceState.WELL_EVIDENCED and (
        sessions >= 4 or minutes >= 240 or longest >= 90
    ):
        return "WELL_TRAINED"
    if (
        sessions >= 3
        or minutes >= 150
        or longest >= 60
        or evidence_state is DisciplineEvidenceState.WELL_EVIDENCED
    ):
        return "TRAINED"
    return "DEVELOPING"


def _longest_minutes(record: object | None, discipline: Discipline) -> int:
    field = {
        Discipline.RUNNING: "longest_recent_run_minutes",
        Discipline.CYCLING: "longest_recent_ride_minutes",
    }.get(discipline)
    value = getattr(record, field, 0) if field is not None else 0
    return value if isinstance(value, int) else 0
