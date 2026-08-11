"""Focused deterministic equipment matching tests."""

from __future__ import annotations

import uuid

import pytest

from app.db.models import EquipmentCatalog
from app.domain.enums import Discipline, EquipmentImportance
from app.services.equipment import EquipmentRecommendationService
from tests.equipment_seed import CATALOG


def _items(discipline: Discipline) -> tuple[EquipmentCatalog, ...]:
    return tuple(
        EquipmentCatalog(
            id=uuid.uuid4(),
            discipline=item_discipline,
            equipment=equipment,
            display_name=display_name,
            importance=importance,
            substitutions=list(substitutions),
        )
        for item_discipline, equipment, display_name, importance, substitutions in (
            CATALOG
        )
        if item_discipline is discipline
    )


@pytest.mark.parametrize(
    "goal,outcome,secondary,expected",
    [
        (
            "Finish an Ironman 70.3",
            "Complete comfortably",
            None,
            (Discipline.RUNNING, Discipline.CYCLING, Discipline.SWIMMING),
        ),
        (
            "Complete a duathlon",
            "Finish",
            None,
            (Discipline.RUNNING, Discipline.CYCLING),
        ),
        ("Ride a gran fondo", "Finish", None, (Discipline.CYCLING,)),
        ("Complete a long hike", "Finish", None, (Discipline.HIKING,)),
        ("Build strength", "Get stronger", None, (Discipline.STRENGTH,)),
        ("Improve general fitness", "Be active", None, ()),
    ],
)
def test_resolve_disciplines_is_ordered_and_bounded(
    goal: str,
    outcome: str,
    secondary: str | None,
    expected: tuple[Discipline, ...],
) -> None:
    assert (
        EquipmentRecommendationService.resolve_disciplines(
            main_goal=goal,
            target_outcome=outcome,
            secondary_priority=secondary,
        )
        == expected
    )


def test_every_catalog_discipline_has_an_essential_and_valid_substitutions() -> None:
    by_discipline: dict[Discipline, set[str]] = {}
    essentials: set[Discipline] = set()
    for discipline, equipment, _, importance, _ in CATALOG:
        by_discipline.setdefault(discipline, set()).add(equipment)
        if importance is EquipmentImportance.ESSENTIAL:
            essentials.add(discipline)

    assert essentials == {
        Discipline.RUNNING,
        Discipline.CYCLING,
        Discipline.SWIMMING,
        Discipline.HIKING,
        Discipline.STRENGTH,
    }
    for discipline, _, _, _, substitutions in CATALOG:
        assert set(substitutions) <= by_discipline[discipline]


@pytest.mark.parametrize("substitute", ["stationary_bike", "mountain_bike"])
def test_cycling_substitute_satisfies_bike_but_not_road_bike_recommendation(
    substitute: str,
) -> None:
    catalog = _items(Discipline.CYCLING)
    selected = tuple(item for item in catalog if item.equipment == substitute)

    summary = EquipmentRecommendationService.summarize(
        catalog=catalog,
        selected=selected,
    )

    assert summary.can_start is True
    assert summary.missing_essentials == ()
    assert any(
        item.discipline is Discipline.CYCLING and item.display_name == "Road bike"
        for item in summary.missing_recommended
    )
    assert all(
        item.display_name != "Repair kit" for item in summary.missing_recommended
    )


def test_missing_essential_is_actionable_but_non_blocking_to_the_flow() -> None:
    summary = EquipmentRecommendationService.summarize(
        catalog=_items(Discipline.SWIMMING),
        selected=(),
    )

    assert summary.can_start is False
    assert summary.missing_essentials[0].display_name == "Swimming access"
    assert summary.missing_essentials[0].substitutions == (
        "Pool access",
        "Open-water access",
    )
