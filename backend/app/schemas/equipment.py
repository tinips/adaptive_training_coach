"""Typed equipment review and suggestion boundaries."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.domain.enums import Discipline, EquipmentImportance


class EquipmentOption(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID
    discipline: Discipline
    equipment: str
    display_name: str
    importance: EquipmentImportance
    substitutions: tuple[str, ...] = ()
    selected: bool = False


class EquipmentReview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    disciplines: tuple[Discipline, ...]
    options: tuple[EquipmentOption, ...]


class MissingEssential(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    discipline: Discipline
    display_name: str
    substitutions: tuple[str, ...] = ()


class MissingRecommended(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    discipline: Discipline
    display_name: str
    substitutions: tuple[str, ...] = ()


class EquipmentAccessItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    discipline: Discipline
    display_name: str
    importance: EquipmentImportance


class EquipmentSuggestionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    can_start: bool
    missing_essentials: tuple[MissingEssential, ...] = ()
    missing_recommended: tuple[MissingRecommended, ...] = ()
