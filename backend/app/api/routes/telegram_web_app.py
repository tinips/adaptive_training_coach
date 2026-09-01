"""Shared Telegram Mini App authentication helpers."""

from __future__ import annotations

import hashlib
import hmac
import json
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from fastapi import HTTPException

from app.config import Settings
from app.schemas.common import TelegramIdentity


def telegram_web_app_identity(
    *, settings: Settings, init_data: str
) -> TelegramIdentity:
    """Validate Telegram's signed init-data payload and return its identity."""

    token = settings.telegram_bot_token
    if token is None:
        raise HTTPException(status_code=503, detail="bot unavailable")
    data = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = data.pop("hash", "")
    check_string = "\n".join(f"{key}={data[key]}" for key in sorted(data))
    secret = hmac.new(
        b"WebAppData", token.get_secret_value().encode(), hashlib.sha256
    ).digest()
    expected_hash = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(received_hash, expected_hash):
        raise HTTPException(status_code=401, detail="invalid Telegram session")
    try:
        user = json.loads(data["user"])
        return TelegramIdentity(
            telegram_user_id=user["id"],
            telegram_username=user.get("username"),
            first_name=user.get("first_name"),
            language_code=user.get("language_code", "en"),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise HTTPException(status_code=400, detail="invalid Telegram user") from error


def workout_history_web_app_url(baseline_url: str | None) -> str | None:
    """Keep every Mini App page on the configured public Web App origin."""

    if not baseline_url:
        return None
    parts = urlsplit(baseline_url)
    if not parts.scheme or not parts.netloc:
        return None
    return urlunsplit((parts.scheme, parts.netloc, "/webapp/workout-history", "", ""))
