"""Typed application configuration loaded from environment variables."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Runtime settings shared by the API and Telegram processes."""

    model_config = SettingsConfigDict(
        env_file=(REPOSITORY_ROOT / ".env", ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    default_bot_language: Literal["en"] = "en"

    telegram_bot_token: SecretStr | None = None
    telegram_bot_username: str | None = None
    telegram_web_app_url: str | None = None
    telegram_allowed_user_ids: set[int] = Field(default_factory=set)
    dev_telegram_user_ids: set[int] = Field(default_factory=set)
    database_url: str = (
        "postgresql+asyncpg://coach:coach@localhost:55432/adaptive_coach"
    )

    llm_mode: Literal["mock", "live"] = "mock"
    llm_api_key: SecretStr | None = None
    llm_base_url: str | None = "https://api.deepseek.com"
    llm_model: str = "deepseek-v4-flash"
    first_week_llm_model: str | None = None
    llm_min_confidence: float = Field(default=0.75, ge=0, le=1)
    llm_other_requests_per_hour: int = Field(default=10, ge=1, le=100)

    # Optional self-hosted, metadata-only LLM tracing. Disabled by default.
    langfuse_enabled: bool = False
    langfuse_public_key: str | None = None
    langfuse_secret_key: SecretStr | None = None
    langfuse_host: str = "http://langfuse-web:3000"

    # Send a workout summary screenshot to the bot; a vision model reads the
    # visible source-app metrics and the athlete confirms before it is saved.
    # Uses the same DeepSeek credentials as `llm_api_key`/`llm_base_url` above,
    # with its own vision-capable model.
    screenshot_import_enabled: bool = False
    llm_vision_model: str = "deepseek-v4-flash-vision-exp"

    tcx_import_enabled: bool = True
    tcx_import_max_size_mb: int = Field(default=25, ge=1, le=100)

    fitness_window_days: int = Field(default=14, ge=1, le=90)
    planner_window_days: int = Field(default=30, ge=1, le=90)
    planner_repair_enabled: bool = False

    @field_validator("telegram_bot_username", mode="before")
    @classmethod
    def normalize_telegram_bot_username(cls, value: str | None) -> str | None:
        """Normalize a public bot username used only for the callback deep link."""

        if value is None:
            return None
        normalized = value.strip().lstrip("@")
        if not normalized:
            return None
        if (
            not 5 <= len(normalized) <= 32
            or not normalized.isascii()
            or not normalized.replace("_", "").isalnum()
        ):
            raise ValueError("TELEGRAM_BOT_USERNAME is not a valid Telegram username")
        return normalized

    @field_validator("dev_telegram_user_ids", mode="before")
    @classmethod
    def parse_dev_telegram_user_ids(cls, value: object) -> object:
        if isinstance(value, str):
            return {int(item.strip()) for item in value.split(",") if item.strip()}
        if isinstance(value, int):
            return {value}
        return value

    @field_validator("telegram_allowed_user_ids", mode="before")
    @classmethod
    def parse_telegram_allowed_user_ids(cls, value: object) -> object:
        if isinstance(value, str):
            return {int(item.strip()) for item in value.split(",") if item.strip()}
        if isinstance(value, int):
            return {value}
        return value

    def exposed_configuration(self) -> dict[str, object]:
        """Return non-secret configuration suitable for diagnostics."""

        return {
            "environment": self.environment,
            "log_level": self.log_level,
            "default_bot_language": self.default_bot_language,
            "llm_mode": self.llm_mode,
            "llm_model": self.llm_model,
            "first_week_llm_model": self.first_week_llm_model,
            "langfuse_enabled": self.langfuse_enabled,
            "tcx_import_enabled": self.tcx_import_enabled,
            "fitness_window_days": self.fitness_window_days,
            "planner_window_days": self.planner_window_days,
            "planner_repair_enabled": self.planner_repair_enabled,
        }


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide immutable settings object."""

    return Settings()
