"""Shared Telegram Mini App authentication helpers."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from fastapi import HTTPException
from pydantic import SecretStr

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


def workout_history_web_app_url(
    baseline_url: str | None,
    *,
    telegram_user_id: int | None = None,
    bot_token: SecretStr | None = None,
) -> str | None:
    """Keep every Mini App page on the configured public Web App origin."""

    if not baseline_url:
        return None
    parts = urlsplit(baseline_url)
    if not parts.scheme or not parts.netloc:
        return None
    query = "v=2"
    if telegram_user_id is not None and bot_token is not None:
        query = f"{query}&session={_history_session_token(telegram_user_id, bot_token)}"
    return urlunsplit(
        (parts.scheme, parts.netloc, "/webapp/workout-history", query, "")
    )


def history_session_identity(
    *, settings: Settings, session_token: str
) -> TelegramIdentity:
    """Validate the short-lived Web App fallback session minted by the bot."""

    token = settings.telegram_bot_token
    if token is None:
        raise HTTPException(status_code=503, detail="bot unavailable")
    try:
        user_id_text, expires_at_text, supplied_signature = session_token.split(".")
        telegram_user_id = int(user_id_text)
        expires_at = int(expires_at_text)
    except ValueError as error:
        raise HTTPException(
            status_code=401, detail="invalid history session"
        ) from error
    expected_signature = _history_session_signature(
        telegram_user_id=telegram_user_id,
        expires_at=expires_at,
        bot_token=token,
    )
    if expires_at < int(time.time()) or not hmac.compare_digest(
        supplied_signature, expected_signature
    ):
        raise HTTPException(status_code=401, detail="invalid history session")
    return TelegramIdentity(
        telegram_user_id=telegram_user_id,
        telegram_username=None,
        first_name=None,
        language_code="en",
    )


def _history_session_token(telegram_user_id: int, bot_token: SecretStr) -> str:
    expires_at = int(time.time()) + 60 * 60
    signature = _history_session_signature(
        telegram_user_id=telegram_user_id,
        expires_at=expires_at,
        bot_token=bot_token,
    )
    return f"{telegram_user_id}.{expires_at}.{signature}"


def _history_session_signature(
    *, telegram_user_id: int, expires_at: int, bot_token: SecretStr
) -> str:
    return hmac.new(
        bot_token.get_secret_value().encode(),
        f"history:{telegram_user_id}:{expires_at}".encode(),
        hashlib.sha256,
    ).hexdigest()
