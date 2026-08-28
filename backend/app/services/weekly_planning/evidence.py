"""Pure transforms for weekly-planner evidence and safe persistence snapshots."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime

from app.db.models import AthleteBaselineAssessment
from app.domain.enums import Discipline, DisciplineEvidenceState
from app.schemas.fitness import BaselineCalculation
from app.schemas.weekly_plans import PlanReadiness, PlanReadinessDiscipline

MINIMUM_ACTIVE_DAYS = 2
MINIMUM_SESSIONS = 3


def _evidence_state(
    calculation: BaselineCalculation | None,
) -> DisciplineEvidenceState:
    if calculation is None or calculation.session_count == 0:
        return DisciplineEvidenceState.NONE
    if (
        calculation.session_count >= MINIMUM_SESSIONS
        and calculation.active_day_count >= MINIMUM_ACTIVE_DAYS
    ):
        return DisciplineEvidenceState.WELL_EVIDENCED
    return DisciplineEvidenceState.THIN


def build_plan_readiness(
    *,
    week_start: date,
    window_started_at: datetime,
    window_ended_at: datetime,
    calculations: dict[Discipline, BaselineCalculation | None],
) -> PlanReadiness:
    """Build the planner gate, judged on the athlete rather than per sport.

    A discipline with little history is classified rather than used as a veto,
    so a triathlete with one swim still receives a plan for their running and
    cycling, with the swim treated gently.
    """

    rows = tuple(
        PlanReadinessDiscipline(
            discipline=discipline,
            session_count=calculation.session_count if calculation else 0,
            active_day_count=calculation.active_day_count if calculation else 0,
            state=_evidence_state(calculation),
            quality_flags=tuple(calculation.quality_flags_jsonb) if calculation else (),
        )
        for discipline, calculation in sorted(
            calculations.items(), key=lambda item: item[0].value
        )
    )
    active_dates: set[date] = set()
    for calculation in calculations.values():
        if calculation is not None:
            active_dates.update(calculation.active_dates)
    total_session_count = sum(row.session_count for row in rows)
    total_active_day_count = len(active_dates)
    return PlanReadiness(
        week_start=week_start,
        analysis_started_at=window_started_at,
        analysis_ended_at=window_ended_at,
        disciplines=rows,
        total_session_count=total_session_count,
        total_active_day_count=total_active_day_count,
        ready=(
            bool(rows)
            and total_session_count >= MINIMUM_SESSIONS
            and total_active_day_count >= MINIMUM_ACTIVE_DAYS
        ),
    )


def build_evidence_snapshot(
    *,
    readiness: PlanReadiness,
    calculations: dict[Discipline, BaselineCalculation | None],
    baselines: tuple[AthleteBaselineAssessment, ...],
) -> dict[str, object]:
    """Return aggregate evidence only, excluding free text and workout names."""

    return {
        "window": {
            "started_at": readiness.analysis_started_at.isoformat(),
            "ended_at": readiness.analysis_ended_at.isoformat(),
        },
        "readiness": readiness.model_dump(mode="json"),
        "recent_evidence": {
            discipline.value: calculation.model_dump(
                mode="json", exclude={"active_dates"}
            )
            for discipline, calculation in sorted(
                calculations.items(), key=lambda item: item[0].value
            )
            if calculation is not None
        },
        "baselines": [
            {
                "discipline": item.discipline.value,
                "analysis_started_at": item.analysis_started_at.isoformat(),
                "analysis_ended_at": item.analysis_ended_at.isoformat(),
                "calculated_at": item.calculated_at.isoformat(),
                "session_count": item.session_count,
                "active_day_count": item.active_day_count,
                "total_duration_seconds": item.total_duration_seconds,
                "known_distance_meters": item.known_distance_meters,
                "confidence": item.confidence,
                "discipline_metrics": item.discipline_metrics_jsonb,
                "quality_flags": item.quality_flags_jsonb,
                "input_digest": item.input_digest,
            }
            for item in sorted(baselines, key=lambda item: item.discipline.value)
        ],
    }


def evidence_input_digest(evidence_snapshot: dict[str, object]) -> str:
    """Hash only the redacted aggregate state used by the planner."""

    encoded = json.dumps(
        evidence_snapshot,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()
