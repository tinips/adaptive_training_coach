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
    database_url: str = (
        "postgresql+asyncpg://coach:coach@localhost:55432/adaptive_coach"
    )
    public_base_url: str = "http://localhost:8000"
    app_encryption_key: SecretStr | None = None

    llm_mode: Literal["mock", "live"] = "mock"
    llm_api_key: SecretStr | None = None
    llm_base_url: str | None = "https://api.deepseek.com"
    llm_model: str = "deepseek-v4-flash"
    llm_min_confidence: float = Field(default=0.75, ge=0, le=1)
    llm_other_requests_per_hour: int = Field(default=10, ge=1, le=100)
    ai_workflow_name: str = "onboarding_text_parser"

    strava_enabled: bool = False
    strava_client_id: str | None = None
    strava_client_secret: SecretStr | None = None
    strava_redirect_uri: str = "http://localhost:8000/integrations/strava/callback"
    strava_initial_sync_days: int = Field(default=56, ge=7, le=365)
    strava_sync_page_size: int = Field(default=100, ge=1, le=200)
    strava_webhook_verify_token: SecretStr | None = None
    strava_webhook_subscription_id: str | None = None
    strava_sync_cooldown_seconds: int = Field(default=300, ge=0, le=3600)
    oauth_state_ttl_seconds: int = Field(default=600, ge=60, le=3600)

    apple_health_import_enabled: bool = True
    apple_health_import_max_compressed_size_mb: int = Field(
        default=100,
        ge=1,
        le=1024,
    )
    apple_health_import_max_uncompressed_size_mb: int = Field(
        default=1024,
        ge=1,
        le=10240,
    )
    apple_health_import_max_zip_members: int = Field(default=100, ge=1, le=10000)
    apple_health_import_max_compression_ratio: float = Field(
        default=200,
        ge=1,
        le=10000,
    )
    apple_health_import_temp_dir: Path | None = None
    apple_health_import_keep_original_files: bool = False
    tcx_import_enabled: bool = True
    tcx_import_max_size_mb: int = Field(default=25, ge=1, le=100)
    workout_feedback_enabled: bool = True

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

    @field_validator("apple_health_import_temp_dir", mode="before")
    @classmethod
    def normalize_apple_health_temp_dir(
        cls,
        value: object,
    ) -> object:
        """Treat the documented empty optional path as the system temp dir."""

        if isinstance(value, str) and not value.strip():
            return None
        return value

    def exposed_configuration(self) -> dict[str, object]:
        """Return non-secret configuration suitable for diagnostics."""

        return {
            "environment": self.environment,
            "log_level": self.log_level,
            "default_bot_language": self.default_bot_language,
            "llm_mode": self.llm_mode,
            "llm_model": self.llm_model,
            "strava_enabled": self.strava_enabled,
            "strava_initial_sync_days": self.strava_initial_sync_days,
            "strava_sync_page_size": self.strava_sync_page_size,
            "apple_health_import_enabled": self.apple_health_import_enabled,
            "tcx_import_enabled": self.tcx_import_enabled,
            "workout_feedback_enabled": self.workout_feedback_enabled,
        }


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide immutable settings object."""

    return Settings()
