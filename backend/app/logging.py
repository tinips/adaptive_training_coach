"""Structured, secret-conscious logging configuration."""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Mapping
from typing import Any


class SensitiveValueFilter(logging.Filter):
    """Remove known sensitive fields from structured log arguments."""

    _sensitive_fragments = (
        "token",
        "secret",
        "password",
        "encryption_key",
        "authorization",
        "pairing_code",
        "device_token",
        "installation_id",
        "healthkit",
        "workout_uuid",
        "activity_type",
        "started_at",
        "ended_at",
        "duration_seconds",
        "distance_meters",
        "calories_kcal",
        "health_description",
        "user_text",
        "prompt",
    )
    _query_value = re.compile(
        r"(?i)([?&](?:ticket|state|code|pairing_code|access_token|refresh_token|"
        r"device_token|mobile_token|client_secret|verify_token|hub\.verify_token)="
        r")[^&\s\"]+"
    )
    _telegram_bot_path = re.compile(r"(?i)(https://api\.telegram\.org/bot)[^/\s\"]+")
    _database_password = re.compile(
        r"(?i)(postgres(?:ql)?(?:\+\w+)?://[^:/\s]+:)[^@\s]+(@)"
    )

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self._redact_text(record.msg)
        if isinstance(record.args, Mapping):
            record.args = {
                key: (
                    "<redacted>"
                    if any(
                        part in str(key).lower() for part in self._sensitive_fragments
                    )
                    else self._redact_value(value)
                )
                for key, value in record.args.items()
            }
        elif isinstance(record.args, tuple):
            record.args = tuple(self._redact_value(value) for value in record.args)
        return True

    @classmethod
    def _redact_value(cls, value: object) -> object:
        return cls._redact_text(value) if isinstance(value, str) else value

    @classmethod
    def _redact_text(cls, value: str) -> str:
        value = cls._query_value.sub(r"\1<redacted>", value)
        value = cls._telegram_bot_path.sub(r"\1<redacted>", value)
        return cls._database_password.sub(r"\1<redacted>\2", value)


def configure_logging(level: str = "INFO") -> None:
    """Configure application logging once for a process."""

    redactor = SensitiveValueFilter()
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(redactor)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s",
        )
    )
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
    for access_handler in logging.getLogger("uvicorn.access").handlers:
        access_handler.addFilter(redactor)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def safe_log_context(**values: Any) -> dict[str, Any]:
    """Build an explicitly named structured context for logging."""

    return values
