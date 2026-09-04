"""Deterministic placement of coach-authored sessions into confirmed availability."""

from __future__ import annotations

from datetime import date

from app.domain.enums import Discipline
from app.schemas.availability import ConfirmedWeeklyAvailability
from app.schemas.weekly_plans import (
    PlanDay,
    PlanSession,
    SessionPrescription,
    WeeklyPlan,
    WeeklyPlanPrescription,
)

_AVAILABILITY_DISCIPLINE = {
    Discipline.RUNNING: "running",
    Discipline.CYCLING: "cycling",
    Discipline.SWIMMING: "swimming",
    Discipline.STRENGTH: "strength_training",
}
_PRIORITY = {"ESSENTIAL": 0, "IMPORTANT": 1, "OPTIONAL": 2}


def schedule_prescription(
    prescription: WeeklyPlanPrescription,
    availability: ConfirmedWeeklyAvailability | None,
) -> WeeklyPlan | None:
    """Place every prescribed session or return ``None`` when none fits safely."""

    if availability is None:
        return None
    scheduled: list[list[PlanSession]] = [[] for _ in range(7)]
    remaining = [
        sum(window.duration_minutes for window in availability.days[day].time_windows)
        for day in (
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
            "friday",
            "saturday",
            "sunday",
        )
    ]
    intents = sorted(
        prescription.sessions,
        key=lambda session: (
            _PRIORITY[session.priority],
            -_duration(session),
        ),
    )
    for intent in intents:
        index = _best_day(intent, scheduled, remaining, availability)
        if index is None:
            return None
        scheduled[index].append(_plan_session(intent))
        remaining[index] -= _duration(intent)
    return WeeklyPlan(
        week_start=prescription.week_start,
        days=tuple(
            PlanDay(
                date=date.fromordinal(prescription.week_start.toordinal() + index),
                sessions=tuple(sessions),
            )
            if sessions
            else PlanDay(
                date=date.fromordinal(prescription.week_start.toordinal() + index),
                rest_note="Rest and recover.",
            )
            for index, sessions in enumerate(scheduled)
        ),
    )


def _best_day(
    intent: SessionPrescription,
    scheduled: list[list[PlanSession]],
    remaining: list[int],
    availability: ConfirmedWeeklyAvailability,
) -> int | None:
    candidates: list[tuple[int, int]] = []
    duration = intent.targets.duration_minutes
    assert duration is not None
    for index, name in enumerate(
        ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
    ):
        details = availability.days[name]
        if (
            not details.available
            or _AVAILABILITY_DISCIPLINE.get(intent.discipline)
            not in details.disciplines
            or remaining[index] < duration
            or len(scheduled[index]) >= 3
            or (scheduled[index] and not intent.can_share_day)
            or _hard_conflicts(intent, scheduled, index)
        ):
            continue
        score = len(scheduled[index])
        if intent.preferred_weekdays and name not in intent.preferred_weekdays:
            score += 10
        if name in intent.avoid_weekdays:
            score += 20
        candidates.append((score, index))
    return min(candidates)[1] if candidates else None


def _hard_conflicts(
    intent: SessionPrescription, scheduled: list[list[PlanSession]], index: int
) -> bool:
    if not intent.intensity.is_hard:
        return False
    return any(
        session.intensity.is_hard
        for adjacent in (index - 1, index, index + 1)
        if 0 <= adjacent < len(scheduled)
        for session in scheduled[adjacent]
    )


def _plan_session(intent: SessionPrescription) -> PlanSession:
    return PlanSession.model_validate(
        intent.model_dump(
            exclude={
                "priority",
                "preferred_weekdays",
                "avoid_weekdays",
                "can_share_day",
            }
        )
    )


def _duration(intent: SessionPrescription) -> int:
    duration = intent.targets.duration_minutes
    assert duration is not None
    return duration
