"""Deterministic discipline resolution and equipment matching."""

from __future__ import annotations

import re
from collections.abc import Collection
from uuid import UUID

from app.db.models import EquipmentCatalog
from app.domain.enums import Discipline, EquipmentImportance
from app.repositories.equipment import EquipmentRepository
from app.schemas.equipment import (
    EquipmentOption,
    EquipmentReview,
    EquipmentSuggestionSummary,
    MissingEssential,
    MissingRecommended,
)

_MULTISPORT_ALIASES: tuple[tuple[re.Pattern[str], tuple[Discipline, ...]], ...] = (
    (
        re.compile(r"\b(?:triathlon|ironman|70[.]3)\b", re.IGNORECASE),
        (Discipline.RUNNING, Discipline.CYCLING, Discipline.SWIMMING),
    ),
    (
        re.compile(r"\bduathlon\b", re.IGNORECASE),
        (Discipline.RUNNING, Discipline.CYCLING),
    ),
)
_DISCIPLINE_ALIASES: tuple[tuple[Discipline, re.Pattern[str]], ...] = (
    (
        Discipline.RUNNING,
        re.compile(
            r"\b(?:run|running|runner|5\s?k|10\s?k|half marathon|marathon|"
            r"trail race)\b",
            re.IGNORECASE,
        ),
    ),
    (
        Discipline.CYCLING,
        re.compile(
            r"\b(?:cycl(?:e|ing|ist)|bike|biking|ride|riding|gran fondo)\b",
            re.IGNORECASE,
        ),
    ),
    (
        Discipline.SWIMMING,
        re.compile(r"\b(?:swim|swimming|pool|open water)\b", re.IGNORECASE),
    ),
    (
        Discipline.HIKING,
        re.compile(r"\b(?:hike|hiking|trek|trekking)\b", re.IGNORECASE),
    ),
    (
        Discipline.STRENGTH,
        re.compile(
            r"\b(?:strength|gym|weights?|resistance|calisthenics)\b",
            re.IGNORECASE,
        ),
    ),
)


class EquipmentRecommendationService:
    @staticmethod
    def resolve_disciplines(
        *, main_goal: str, target_outcome: str, secondary_priority: str | None
    ) -> tuple[Discipline, ...]:
        text = " ".join(
            item for item in (main_goal, target_outcome, secondary_priority) if item
        )
        found: list[Discipline] = []
        for pattern, disciplines in _MULTISPORT_ALIASES:
            if pattern.search(text):
                found.extend(disciplines)
        for discipline, pattern in _DISCIPLINE_ALIASES:
            if pattern.search(text):
                found.append(discipline)
        return tuple(dict.fromkeys(found))

    async def review(
        self,
        *,
        repository: EquipmentRepository,
        athlete_id: UUID,
        main_goal: str,
        target_outcome: str,
        secondary_priority: str | None,
    ) -> EquipmentReview | None:
        disciplines = self.resolve_disciplines(
            main_goal=main_goal,
            target_outcome=target_outcome,
            secondary_priority=secondary_priority,
        )
        if not disciplines:
            return None
        catalog = await repository.catalog_for_disciplines(disciplines=disciplines)
        if not catalog:
            return None
        selected_ids = {
            item.id for item in await repository.selected_catalog(athlete_id=athlete_id)
        }
        by_key = {(item.discipline, item.equipment): item for item in catalog}
        return EquipmentReview(
            disciplines=disciplines,
            options=tuple(
                EquipmentOption(
                    id=item.id,
                    discipline=item.discipline,
                    equipment=item.equipment,
                    display_name=item.display_name,
                    importance=item.importance,
                    substitutions=tuple(
                        by_key[(item.discipline, key)].display_name
                        for key in item.substitutions
                        if (item.discipline, key) in by_key
                    ),
                    selected=item.id in selected_ids,
                )
                for item in catalog
            ),
        )

    async def save_and_summarize(
        self,
        *,
        repository: EquipmentRepository,
        athlete_id: UUID,
        review: EquipmentReview,
        selected_ids: Collection[UUID],
    ) -> EquipmentSuggestionSummary:
        selected = await repository.replace_for_disciplines(
            athlete_id=athlete_id,
            disciplines=review.disciplines,
            equipment_ids=selected_ids,
        )
        catalog = await repository.catalog_for_disciplines(
            disciplines=review.disciplines
        )
        return self.summarize(catalog=catalog, selected=selected)

    @staticmethod
    def summarize(
        *,
        catalog: Collection[EquipmentCatalog],
        selected: Collection[EquipmentCatalog],
    ) -> EquipmentSuggestionSummary:
        selected_keys = {(item.discipline, item.equipment) for item in selected}
        by_key = {(item.discipline, item.equipment): item for item in catalog}

        def satisfied(item: EquipmentCatalog) -> bool:
            return (item.discipline, item.equipment) in selected_keys or any(
                (item.discipline, substitute) in selected_keys
                for substitute in item.substitutions
            )

        missing_essentials: list[MissingEssential] = []
        missing_recommended: list[MissingRecommended] = []
        for item in catalog:
            if satisfied(item):
                continue
            if item.importance is EquipmentImportance.ESSENTIAL:
                missing_essentials.append(
                    MissingEssential(
                        discipline=item.discipline,
                        display_name=item.display_name,
                        substitutions=tuple(
                            by_key[(item.discipline, key)].display_name
                            for key in item.substitutions
                            if (item.discipline, key) in by_key
                        ),
                    )
                )
            elif item.importance is EquipmentImportance.RECOMMENDED:
                missing_recommended.append(
                    MissingRecommended(
                        discipline=item.discipline,
                        display_name=item.display_name,
                        substitutions=tuple(
                            by_key[(item.discipline, key)].display_name
                            for key in item.substitutions
                            if (item.discipline, key) in by_key
                        ),
                    )
                )
        return EquipmentSuggestionSummary(
            can_start=not missing_essentials,
            missing_essentials=tuple(missing_essentials),
            missing_recommended=tuple(missing_recommended),
        )
