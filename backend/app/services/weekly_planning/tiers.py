"""Deterministic preparation tiers for first-week probe menus."""

from __future__ import annotations

from typing import Literal

from app.domain.enums import Discipline, DisciplineEvidenceState
from app.schemas.baseline import AthleteBaselineData
from app.schemas.weekly_plans import PlanReadiness

BaselineTier = Literal["UNPREPARED", "DEVELOPING", "TRAINED", "WELL_TRAINED"]


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
