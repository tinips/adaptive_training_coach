"""Goals are grouped by sport from their target disciplines, not hardcoded."""

from __future__ import annotations

from app.domain.enums import Discipline
from app.services.training_catalog.grouping import (
    GoalOption,
    GoalSport,
    group_goals_by_sport,
)


def _option(code: str, *disciplines: Discipline) -> GoalOption:
    return GoalOption(
        code=code, display_name=code.title(), disciplines=frozenset(disciplines)
    )


def test_a_single_discipline_goal_groups_under_that_sport() -> None:
    grouped = group_goals_by_sport(
        (
            _option("MARATHON", Discipline.RUNNING),
            _option("MTB_RACE", Discipline.CYCLING),
            _option("OPEN_WATER_SWIM", Discipline.SWIMMING),
        )
    )

    assert [item.code for item in grouped[GoalSport.RUNNING]] == ["MARATHON"]
    assert [item.code for item in grouped[GoalSport.CYCLING]] == ["MTB_RACE"]
    assert [item.code for item in grouped[GoalSport.SWIMMING]] == ["OPEN_WATER_SWIM"]
    assert GoalSport.TRIATHLON not in grouped


def test_a_multi_discipline_goal_groups_under_triathlon() -> None:
    grouped = group_goals_by_sport(
        (
            _option(
                "TRIATHLON_SPRINT",
                Discipline.SWIMMING,
                Discipline.CYCLING,
                Discipline.RUNNING,
            ),
        )
    )

    assert [item.code for item in grouped[GoalSport.TRIATHLON]] == ["TRIATHLON_SPRINT"]
    assert GoalSport.RUNNING not in grouped


def test_grouping_is_stable_and_alphabetical_within_a_sport() -> None:
    grouped = group_goals_by_sport(
        (
            _option("MARATHON", Discipline.RUNNING),
            _option("HALF_MARATHON", Discipline.RUNNING),
        )
    )

    assert [item.code for item in grouped[GoalSport.RUNNING]] == [
        "HALF_MARATHON",
        "MARATHON",
    ]
