"""Typed application configuration loaded from environment variables."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
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
    database_url: str = (
        "postgresql+asyncpg://coach:coach@localhost:55432/adaptive_coach"
    )
    public_base_url: str = "http://localhost:8000"
    app_encryption_key: SecretStr | None = None

    llm_mode: Literal["mock", "live"] = "mock"
    llm_api_key: SecretStr | None = None
    llm_base_url: str | None = None
    llm_model: str = "gpt-4.1-mini"
    llm_min_confidence: float = Field(default=0.75, ge=0, le=1)
    llm_other_requests_per_hour: int = Field(default=10, ge=1, le=100)
    ai_workflow_name: str = "onboarding_text_parser"

    strava_client_id: str | None = None
    strava_client_secret: SecretStr | None = None
    strava_redirect_uri: str = "http://localhost:8000/integrations/strava/callback"
    strava_initial_sync_days: int = Field(default=56, ge=7, le=365)
    strava_sync_page_size: int = Field(default=100, ge=1, le=200)
    strava_webhook_verify_token: SecretStr | None = None
    strava_webhook_subscription_id: str | None = None
    strava_sync_cooldown_seconds: int = Field(default=300, ge=0, le=3600)
    oauth_state_ttl_seconds: int = Field(default=600, ge=60, le=3600)

    def exposed_configuration(self) -> dict[str, object]:
        """Return non-secret configuration suitable for diagnostics."""

        return {
            "environment": self.environment,
            "log_level": self.log_level,
            "default_bot_language": self.default_bot_language,
            "llm_mode": self.llm_mode,
            "llm_model": self.llm_model,
            "strava_initial_sync_days": self.strava_initial_sync_days,
            "strava_sync_page_size": self.strava_sync_page_size,
        }


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide immutable settings object."""

    return Settings()
