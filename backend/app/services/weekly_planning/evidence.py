"""Pure transforms for weekly-planner evidence and safe persistence snapshots."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime

from app.db.models import AthleteBaselineAssessment
from app.domain.enums import Discipline
from app.schemas.fitness import BaselineCalculation
from app.schemas.weekly_plans import PlanReadiness, PlanReadinessDiscipline

MINIMUM_ACTIVE_DAYS = 2
MINIMUM_SESSIONS = 3


def build_plan_readiness(
    *,
    week_start: date,
    window_started_at: datetime,
    window_ended_at: datetime,
    calculations: dict[Discipline, BaselineCalculation | None],
) -> PlanReadiness:
    """Build the deterministic planner gate from deduplicated calculations."""

    rows = tuple(
        PlanReadinessDiscipline(
            discipline=discipline,
            session_count=calculation.session_count if calculation else 0,
            active_day_count=calculation.active_day_count if calculation else 0,
            ready=(
                calculation is not None
                and calculation.session_count >= MINIMUM_SESSIONS
                and calculation.active_day_count >= MINIMUM_ACTIVE_DAYS
            ),
            quality_flags=tuple(calculation.quality_flags_jsonb) if calculation else (),
        )
        for discipline, calculation in sorted(
            calculations.items(), key=lambda item: item[0].value
        )
    )
    return PlanReadiness(
        week_start=week_start,
        analysis_started_at=window_started_at,
        analysis_ended_at=window_ended_at,
        disciplines=rows,
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
            discipline.value: calculation.model_dump(mode="json")
            for discipline, calculation in sorted(
                calculations.items(), key=lambda item: item[0].value
            )
            if calculation is not None
        },
        "baselines": [
            {
                "discipline": item.discipline.value,
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
