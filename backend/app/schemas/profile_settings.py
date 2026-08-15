"""Safe presentation state for a deterministic profile-settings mini-flow."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, JsonValue

from app.domain.enums import ProfileSettingsStep
from app.schemas.capabilities import CapabilityReview, GoalExecutionAssessment


class ProfileSettingsResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    step: ProfileSettingsStep
    pending: dict[str, JsonValue] = {}
    saved_field: str | None = None
    current_value: str | int | float | None = None
    capability_review: CapabilityReview | None = None
    execution_assessment: GoalExecutionAssessment | None = None
