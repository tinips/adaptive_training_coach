"""Safe presentation state for a deterministic profile-settings mini-flow."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, JsonValue

from app.domain.enums import ProfileSettingsStep


class ProfileSettingsResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    step: ProfileSettingsStep
    pending: dict[str, JsonValue] = {}
    saved_field: str | None = None
