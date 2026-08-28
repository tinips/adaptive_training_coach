"""Group primary goals into the menu the athlete picks from.

The grouping is derived from each goal's target disciplines rather than
hardcoded, so adding a goal stays a change to the seed alone.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from app.domain.enums import Discipline


class GoalSport(StrEnum):
    """The first level of the goal menu."""

    RUNNING = "RUNNING"
    CYCLING = "CYCLING"
    SWIMMING = "SWIMMING"
    TRIATHLON = "TRIATHLON"


@dataclass(frozen=True, slots=True)
class GoalOption:
    """One selectable primary goal and the disciplines it targets."""

    code: str
    display_name: str
    disciplines: frozenset[Discipline]


_SPORT_BY_DISCIPLINE = {
    Discipline.RUNNING: GoalSport.RUNNING,
    Discipline.CYCLING: GoalSport.CYCLING,
    Discipline.SWIMMING: GoalSport.SWIMMING,
}


def group_goals_by_sport(
    goals: Sequence[GoalOption],
) -> dict[GoalSport, tuple[GoalOption, ...]]:
    """Bucket goals by sport; anything spanning several is a triathlon goal."""

    buckets: dict[GoalSport, list[GoalOption]] = defaultdict(list)
    for goal in goals:
        if len(goal.disciplines) > 1:
            buckets[GoalSport.TRIATHLON].append(goal)
            continue
        for discipline in goal.disciplines:
            sport = _SPORT_BY_DISCIPLINE.get(discipline)
            if sport is not None:
                buckets[sport].append(goal)
    return {
        sport: tuple(sorted(options, key=lambda option: option.code))
        for sport, options in buckets.items()
    }
