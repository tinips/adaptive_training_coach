"""Athlete-local week-membership, without the UTC-midnight bug this rejects.

docs/design/first-week-evaluator.md, "Implementation risks and ambiguity
rules": "the existing comparison service queries UTC midnight boundaries,
which can misclassify edge-of-day workouts for non-UTC athletes; do not copy
that behavior without an explicit test and decision." This module resolves a
workout's calendar date in the athlete's own local timezone before comparing
it against the plan week, mirroring the silent UTC fallback already used by
app.services.weekly_planning.service.next_week_start for a missing or
unrecognized timezone.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def local_date(instant: datetime, timezone: str | None) -> date:
    """The calendar date `instant` falls on in `timezone` (UTC if unusable)."""

    aware = instant if instant.tzinfo is not None else instant.replace(tzinfo=UTC)
    if timezone:
        try:
            return aware.astimezone(ZoneInfo(timezone)).date()
        except ZoneInfoNotFoundError:
            pass
    return aware.astimezone(UTC).date()


def is_within_plan_week(
    *, started_at: datetime, timezone: str | None, week_start: date
) -> bool:
    """Whether a workout's athlete-local date falls in `week_start`'s Mon-Sun."""

    workout_date = local_date(started_at, timezone)
    return week_start <= workout_date <= week_start + timedelta(days=6)
