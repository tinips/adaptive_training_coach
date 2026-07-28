"""Shared application-boundary schemas."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class TelegramIdentity(BaseModel):
    """Stable Telegram identity extracted by the delivery layer."""

    model_config = ConfigDict(frozen=True)

    telegram_user_id: int
    telegram_username: str | None = None
    first_name: str | None = None
    language_code: str = Field(default="en", max_length=16)
