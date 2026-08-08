"""Database-driven recommendation and Telegram-safe rendering."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from app.db.models import EquipmentStageWindow
from app.domain.enums import EquipmentPriority, EquipmentTrainingStage
from app.repositories.equipment import EquipmentRepository

_ORDER = {
    EquipmentTrainingStage.START: 0,
    EquipmentTrainingStage.BASE: 1,
    EquipmentTrainingStage.BUILD: 2,
    EquipmentTrainingStage.RACE_SPECIFIC: 3,
    EquipmentTrainingStage.RACE: 4,
}
_LABELS = {
    EquipmentTrainingStage.START: "Start now",
    EquipmentTrainingStage.BASE: "Base training",
    EquipmentTrainingStage.BUILD: "Build training",
    EquipmentTrainingStage.RACE_SPECIFIC: "Race-specific prep",
    EquipmentTrainingStage.RACE: "Race day",
}


@dataclass(frozen=True, slots=True)
class RecommendedResource:
    resource_id: UUID
    name: str
    priority: EquipmentPriority
    required_stage: EquipmentTrainingStage
    condition_text: str | None


@dataclass(frozen=True, slots=True)
class EquipmentRecommendation:
    text: str
    resources: tuple[RecommendedResource, ...]
    substitutions: tuple[str, ...]


class EquipmentRecommendationService:
    async def recommend(
        self,
        *,
        repository: EquipmentRepository,
        main_goal: str,
        target_outcome: str,
        event_date: date | None,
        today: date,
    ) -> EquipmentRecommendation | None:
        haystack = f"{main_goal} {target_outcome}".casefold()
        goal_type = next(
            (
                item
                for item in await repository.goal_types()
                if any(term.casefold() in haystack for term in item.match_terms)
            ),
            None,
        )
        if goal_type is None:
            return None
        current_stage = self._resolve_stage(
            await repository.stage_windows(goal_type_id=goal_type.id), event_date, today
        )
        rows = await repository.requirements(goal_type_id=goal_type.id)
        resources = tuple(
            RecommendedResource(
                resource_id=resource.id,
                name=resource.display_name,
                priority=requirement.priority,
                required_stage=requirement.required_stage,
                condition_text=requirement.condition_text,
            )
            for requirement, resource in rows
        )
        substitutions = await repository.substitutions(
            required_resource_ids=tuple(item.resource_id for item in resources)
        )
        notes = tuple(
            f"{substitute.display_name}: "
            f"{substitution.quality.value.replace('_', ' ').lower()} substitute"
            for substitution, substitute in substitutions
        )
        return EquipmentRecommendation(
            text=self.render(
                resources=resources, current_stage=current_stage, substitutions=notes
            ),
            resources=resources,
            substitutions=notes,
        )

    @staticmethod
    def _resolve_stage(
        windows: tuple[EquipmentStageWindow, ...], event_date: date | None, today: date
    ) -> EquipmentTrainingStage:
        if event_date is None:
            return EquipmentTrainingStage.START
        days = (event_date - today).days
        for window in windows:
            if window.minimum_days_until_event <= days and (
                window.maximum_days_until_event is None
                or days <= window.maximum_days_until_event
            ):
                return window.stage
        return EquipmentTrainingStage.START

    @staticmethod
    def render(
        *,
        resources: tuple[RecommendedResource, ...],
        current_stage: EquipmentTrainingStage,
        substitutions: tuple[str, ...],
    ) -> str:
        rows = [
            (
                item.name,
                item.priority.value,
                _LABELS[item.required_stage]
                + (
                    " (now)"
                    if _ORDER[item.required_stage] <= _ORDER[current_stage]
                    else ""
                ),
            )
            for item in resources
        ]
        width = max(len("Equipment"), *(len(row[0]) for row in rows))
        output = [
            f"{'Equipment':<{width}}  Importance  When needed",
            f"{'-' * width}  ----------  ------------------",
        ]
        output.extend(
            f"{name:<{width}}  {priority:<11} {stage}" for name, priority, stage in rows
        )
        conditions = [
            f"{item.name}: {item.condition_text}"
            for item in resources
            if item.condition_text
        ]
        if conditions:
            output.extend(["", "Conditions: " + "; ".join(conditions)])
        if substitutions:
            output.extend(["Alternatives: " + "; ".join(substitutions)])
        text = "\n".join(output)
        if len(text) > 3500:
            raise ValueError("equipment table exceeds Telegram message capacity")
        return text
