"""Safe presentation state for a deterministic profile-settings mini-flow."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, JsonValue

from app.domain.enums import ProfileSettingsStep
from app.schemas.equipment import EquipmentReview, EquipmentSuggestionSummary


class ProfileSettingsResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    step: ProfileSettingsStep
    pending: dict[str, JsonValue] = {}
    saved_field: str | None = None
    current_value: str | int | float | None = None
    equipment_review: EquipmentReview | None = None
    equipment_summary: EquipmentSuggestionSummary | None = None
